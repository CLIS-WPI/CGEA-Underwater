#!/usr/bin/env python3
"""Read-only diagnosis of E1-v3 DIGEST timeout and B4 hard-safety ALLOW.

Does not change retry/timeout/packet size, PHY, CGEA thresholds, B5, or utility.
Replays existing GPU traces (forbid_trace_generation).
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx
from omegaconf import OmegaConf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_e1_pilot_v2 import OUTAGES, SEEDS, _mission_kwargs  # noqa: E402
from run_e1_pilot_v3 import ENV_FILES, load_base_cfg  # noqa: E402
from cgea.experiments.runner import run_single  # noqa: E402
from cgea.governance import (  # noqa: E402
    AuthorityFreshness,
    ExecutionGovernor,
    GovernorDecision,
    authority_age,
    classify_freshness,
    contract_capsule,
)
from cgea.mission import RiskClass
from cgea.mission.oracle import oracle_label as _oracle_label
from cgea.network import PacketType, UnderwaterNetwork, stable_bernoulli

OUT = ROOT / "results" / "e1_v3_diagnosis"
GOV_TYPES = {PacketType.DIGEST, PacketType.PROVENANCE, PacketType.RECONCILE, PacketType.AUTHORITY}

_orig_send = UnderwaterNetwork._send_proc
_orig_force = UnderwaterNetwork.force_partitions
_orig_decide = ExecutionGovernor.decide

PACKET_LOG: list[dict] = []
TOPO_LOG: list[dict] = []
ALLOW_EVENTS: list[dict] = []
_last_label = None
_audit_b4 = False
_trace_packets = False


def _oracle_wrap(proposal, world):
    global _last_label
    _last_label = _oracle_label(proposal, world)
    return _last_label


def _force_partitions(self, groups):
    _orig_force(self, groups)
    if not _trace_packets:
        return
    t = float(self.env.now)
    g = self.connectivity_graph()
    nodes = [n for n in self.node_ids if n != self.gateway_id]
    rec = {
        "t": t,
        "forced_groups": None if groups is None else [sorted(x) for x in groups],
        "n_edges": int(g.number_of_edges()),
        "n_undirected_components": len(list(nx.connected_components(g.to_undirected()))),
        "nodes": {},
    }
    for n in nodes:
        direct = self.link_available(n, self.gateway_id)
        sample = self.link_sample(n, self.gateway_id)
        try:
            hops = int(nx.shortest_path_length(g, n, self.gateway_id))
            has_path = True
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            hops = None
            has_path = False
        rec["nodes"][n] = {
            "direct_link_to_gw": bool(direct),
            "multi_hop_path_to_gw": bool(has_path),
            "shortest_hops_to_gw": hops,
            "psp_direct": None if sample is None else float(sample.packet_success_probability),
            "link_available_sample": None if sample is None else bool(sample.link_available),
            "rate_bps": None if sample is None else float(sample.estimated_rate_bps),
        }
    TOPO_LOG.append(rec)


def _send_proc(self, packet):
    if not _trace_packets:
        yield from _orig_send(self, packet)
        return
    t0 = float(self.env.now)
    sample = self.link_sample(packet.src, packet.dst)
    direct = self.link_available(packet.src, packet.dst)
    g = self.connectivity_graph()
    try:
        hops = int(nx.shortest_path_length(g, packet.src, packet.dst))
        has_path = True
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        hops = None
        has_path = False
    qlen = len(self.nodes[packet.src].queue)
    psp = None if sample is None else float(sample.packet_success_probability)
    draw = stable_bernoulli(packet.packet_id)
    would_erasure = psp is not None and draw >= psp
    rec = {
        "t_send": t0,
        "src": packet.src,
        "dst": packet.dst,
        "ptype": packet.ptype.value,
        "bytes": int(packet.size_bytes),
        "packet_id": packet.packet_id,
        "queue_len_before": qlen,
        "queue_limit": int(self.queue_limit),
        "direct_link_available": bool(direct),
        "multi_hop_path_exists": bool(has_path),
        "shortest_hops": hops,
        "sample_present": sample is not None,
        "psp": psp,
        "deterministic_draw": draw,
        "would_fail_erasure_if_on_air": bool(would_erasure),
        "prop_delay_s": None if sample is None else float(sample.propagation_delay_s),
        "rate_bps": None if sample is None else float(sample.estimated_rate_bps),
        "forced_partition_active": self._forced_partition_groups is not None,
    }
    n_del = len(self.deliveries)
    yield from _orig_send(self, packet)
    d = self.deliveries[-1] if len(self.deliveries) > n_del else None
    rec["t_done"] = float(self.env.now)
    rec["success"] = None if d is None else bool(d.success)
    rec["drop_reason"] = None if d is None else d.reason
    if packet.ptype in GOV_TYPES or packet.ptype == PacketType.DIGEST:
        PACKET_LOG.append(rec)


def _decide(self, proposal, connectivity, capsule, last_authority_update, now, energy,
            position=None, freshness_mode="continuous", violates_frozen_risk=False):
    result = _orig_decide(
        self,
        proposal,
        connectivity,
        capsule,
        last_authority_update,
        now,
        energy,
        position,
        freshness_mode,
        violates_frozen_risk=violates_frozen_risk,
    )
    if not _audit_b4:
        return result
    label = _last_label
    if label is None or not label.violates_frozen_risk:
        return result
    if result.decision != GovernorDecision.ALLOW:
        return result
    if proposal.risk_class != RiskClass.CONSEQUENTIAL:
        return result
    age = authority_age(now, last_authority_update)
    if now >= capsule.hard_expiry:
        freshness = AuthorityFreshness.HARD_EXPIRED
    elif freshness_mode == "disabled":
        freshness = AuthorityFreshness.FRESH
    else:
        freshness = classify_freshness(age, capsule, self.freshness_policy)
        if now >= capsule.hard_expiry:
            freshness = AuthorityFreshness.HARD_EXPIRED
    eff = contract_capsule(capsule, freshness, self.freshness_policy, now)
    ALLOW_EVENTS.append(
        {
            "action_type": proposal.action_type.value,
            "time": float(now),
            "connectivity": connectivity.value,
            "freshness": freshness.value,
            "effective_allowed_actions": list(eff.allowed_actions),
            "effective_forbidden_actions": list(eff.forbidden_actions),
            "reason_code": result.reason_code.value if result.reason_code else None,
            "oracle_reason": label.reason,
            "authority_age_s": float(age),
            "violates_frozen_risk": True,
        }
    )
    return result


def _cfg_for(env_path: Path, seed: int, outage: str):
    cfg = load_base_cfg(env_path)
    ocfg = OUTAGES[outage]
    return OmegaConf.merge(
        cfg,
        {
            "seed": int(seed),
            "scenario": {
                "outage_disabled": ocfg["outage_disabled"],
                "outage_start_s": ocfg["outage_start_s"],
                "outage_end_s": ocfg["outage_end_s"],
                "partition_groups": None,
            },
            "experiment": {"name": "e1_v3_diagnosis"},
            "force_regenerate_trace": False,
            "forbid_trace_generation": True,
        },
    )


def digest_one_run():
    global PACKET_LOG, TOPO_LOG, _trace_packets, _audit_b4
    PACKET_LOG = []
    TOPO_LOG = []
    _trace_packets = True
    _audit_b4 = False
    cfg = _cfg_for(ROOT / "configs" / "acoustic" / "default.yaml", 0, "300")
    m = run_single(cfg, "B4")
    _trace_packets = False
    digest = [p for p in PACKET_LOG if p["ptype"] == "digest"]
    by_reason = Counter(p["drop_reason"] for p in digest)
    n_ok = sum(1 for p in digest if p["success"])
    n_no_direct = sum(1 for p in digest if not p["direct_link_available"])
    n_path_no_direct = sum(
        1 for p in digest if p["multi_hop_path_exists"] and not p["direct_link_available"]
    )
    reconnect = [t for t in TOPO_LOG if t["forced_groups"] is None]
    payload = {
        "representative": {
            "environment_id": "paper_ssp_200m_v1",
            "seed": 0,
            "outage": "300",
            "baseline": "B4",
            "recon_status": m.extra.get("recon_status"),
            "recon_latency_s": m.extra.get("recon_latency_s"),
        },
        "digest_n": len(digest),
        "digest_success": n_ok,
        "digest_drop_reasons": dict(by_reason),
        "digest_no_direct_link": n_no_direct,
        "digest_multi_hop_but_no_direct": n_path_no_direct,
        "topo_at_partition_clear": reconnect[:3],
        "packets": PACKET_LOG,
        "topo": TOPO_LOG,
    }
    (OUT / "digest_representative.json").write_text(json.dumps(payload, indent=2))
    return payload


def audit_all_b4():
    global ALLOW_EVENTS, _audit_b4, _trace_packets
    ALLOW_EVENTS = []
    _audit_b4 = True
    _trace_packets = False
    n = 0
    for env_path in ENV_FILES:
        for seed in SEEDS:
            for outage in OUTAGES:
                cfg = _cfg_for(env_path, seed, outage)
                start = len(ALLOW_EVENTS)
                run_single(cfg, "B4")
                n += 1
                print(f"[audit] {n}/54 {env_path.name} seed={seed} outage={outage} new={len(ALLOW_EVENTS)-start}", flush=True)
    _audit_b4 = False
    counts: dict[str, int] = defaultdict(int)
    for e in ALLOW_EVENTS:
        key = f"{e['action_type']}|{e['freshness']}|{e['reason_code']}"
        counts[key] += 1
    by_action = Counter(e["action_type"] for e in ALLOW_EVENTS)
    by_fresh = Counter(e["freshness"] for e in ALLOW_EVENTS)
    by_conn = Counter(e["connectivity"] for e in ALLOW_EVENTS)
    summary = {
        "n_b4_runs": 54,
        "n_hard_safety_allow": len(ALLOW_EVENTS),
        "by_action_type": dict(by_action),
        "by_freshness": dict(by_fresh),
        "by_connectivity": dict(by_conn),
        "by_action_freshness_reason": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "capsule_policy_note": {
            "issue_capsule_broad": True,
            "fresh_forbidden_actions_empty": True,
            "aging_remove_actions": [
                "change_high_level_objective",
                "abandon_mandatory_inspection",
                "reassign_another_auv",
            ],
            "aging_does_not_remove": [
                "enter_exclusion_zone",
                "exceed_return_energy_reserve",
            ],
            "oracle_violates_frozen_risk": [
                "enter_exclusion_zone",
                "exceed_return_energy_reserve",
                "abandon_mandatory_inspection",
                "change_high_level_objective",
            ],
            "oracle_reassign_not_hard_safety": True,
        },
    }
    (OUT / "b4_hard_safety_allow_events.json").write_text(json.dumps(ALLOW_EVENTS, indent=2))
    (OUT / "b4_hard_safety_allow_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    import cgea.experiments.runner as runner_mod

    runner_mod.oracle_label = _oracle_wrap
    UnderwaterNetwork._send_proc = _send_proc
    UnderwaterNetwork.force_partitions = _force_partitions
    ExecutionGovernor.decide = _decide

    print("[diag] DIGEST representative B4 default seed=0 outage=300", flush=True)
    d = digest_one_run()
    print(
        "digest",
        d["digest_n"],
        "ok",
        d["digest_success"],
        "reasons",
        d["digest_drop_reasons"],
        "path_no_direct",
        d["digest_multi_hop_but_no_direct"],
        flush=True,
    )
    print("[diag] B4 hard-safety ALLOW audit 54 runs", flush=True)
    s = audit_all_b4()
    print(json.dumps({k: s[k] for k in s if k != "by_action_freshness_reason"}, indent=2))
    print("counts", json.dumps(s["by_action_freshness_reason"], indent=2))


if __name__ == "__main__":
    main()
