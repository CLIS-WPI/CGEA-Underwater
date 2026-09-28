"""Config-driven experiment runner for E1–E5 over B1–B5."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import simpy
from omegaconf import DictConfig, OmegaConf

from cgea.acoustic.bellhop import AcousticEnvironment, BellhopEngine, SoundSpeedProfile
from cgea.acoustic.trace import ChannelTraceStore, generate_mission_trace
from cgea.agent import ExecutionAdapter, MissionPlanner
from cgea.baselines import BaselineId, make_baseline
from cgea.governance import (
    AuthorityFreshness,
    ConnectivityClassifier,
    ConnectivityMetrics,
    ConnectivityState,
    Digest,
    FreshnessPolicy,
    GovernorDecision,
    NodeJournalEntry,
    authority_age,
    classify_freshness,
    issue_capsule,
    make_digest,
    reconcile_partitions,
)
from cgea.experiments.paper_gates import (
    PaperAssertionError,
    assert_aubellhop_backend,
    assert_cuda_device,
    assert_paper_config,
    assert_trace_paper_meta,
)
from cgea.metrics import RunMetrics, compute_overhead, save_metrics
from cgea.mission import CONSEQUENTIAL_ACTIONS, RiskClass, build_pipeline_mission
from cgea.mission.oracle import oracle_label
from cgea.mission.utility import UTILITY_FREEZE_ID, world_utility
from cgea.network import Packet, PacketType, UnderwaterNetwork
from cgea.types import Position3D, Provenance, config_hash, git_commit

DISCONNECTED_LIKE = {
    ConnectivityState.PARTITIONED,
    ConnectivityState.ISOLATED,
    ConnectivityState.RECOVERING,
}


def _env_from_cfg(cfg: DictConfig) -> AcousticEnvironment:
    ac = cfg.acoustic
    ssp = SoundSpeedProfile(
        depths_m=list(ac.ssp.depths_m),
        speeds_mps=list(ac.ssp.speeds_mps),
    )
    return AcousticEnvironment(
        water_depth_m=float(ac.water_depth_m),
        carrier_frequency_hz=float(ac.carrier_frequency_hz),
        ssp=ssp,
        environment_id=str(ac.environment_id),
        seed=int(cfg.seed),
    )


def ensure_trace(cfg: DictConfig, world_positions: dict[str, Position3D]) -> Any:
    """Generate or load the shared ChannelTrace. Never regenerate per baseline.

    GPU-PHY traces use a distinct trace_id suffix and must never be mixed with
    the legacy Sionna-wrap family in one comparison.
    """
    paper_run = not bool(cfg.acoustic.get("allow_fallback", False))
    use_gpu_phy = bool(cfg.acoustic.get("use_gpu_phy", paper_run))
    if paper_run:
        assert_paper_config(cfg)
        if not use_gpu_phy:
            raise PaperAssertionError("paper runs require acoustic.use_gpu_phy=true")

    store = ChannelTraceStore(Path(cfg.paths.traces))
    engine = BellhopEngine(
        _env_from_cfg(cfg),
        prefer_aubellhop=bool(cfg.acoustic.get("prefer_aubellhop", True)),
        allow_fallback=bool(cfg.acoustic.get("allow_fallback", False)),
    )
    if paper_run:
        assert_aubellhop_backend(engine.backend)

    duration = float(cfg.mission.duration_s)
    dt = float(cfg.mission.channel_sample_dt_s)
    times = list(np.arange(0.0, duration + 1e-9, dt))
    from cgea.acoustic.trace import make_trace_id

    base_id = make_trace_id(engine.env.environment_id, int(cfg.seed), len(world_positions), duration)
    trace_id = f"{base_id}_gpu" if use_gpu_phy else base_id
    meta_path = store.meta_path(trace_id)
    parquet_path = store.trace_path(trace_id)
    traces_ready = meta_path.exists() and parquet_path.exists()
    if bool(cfg.get("forbid_trace_generation", False)) and (
        not traces_ready or bool(cfg.get("force_regenerate_trace", False))
    ):
        raise RuntimeError(
            f"refusing to generate ChannelTrace {trace_id}; prebuild traces in the parent process first"
        )
    if (
        traces_ready
        and not bool(cfg.get("force_regenerate_trace", False))
    ):
        meta = store.load_meta(trace_id)
        if paper_run:
            assert_trace_paper_meta(meta, require_gpu_phy=use_gpu_phy)
        if use_gpu_phy and not meta.get("gpu_phy"):
            raise PaperAssertionError(f"refusing to load non-GPU trace {trace_id} under use_gpu_phy")
        if (not use_gpu_phy) and meta.get("gpu_phy"):
            raise PaperAssertionError(f"refusing to load GPU-PHY trace {trace_id} under legacy channel path")
        return store.load(trace_id)

    if use_gpu_phy:
        from cgea.acoustic.gpu_pipeline import generate_mission_trace_gpu
        from cgea.phy.config import PAPER_PHY

        phy = PAPER_PHY.model_copy(
            update={
                "bandwidth_hz": float(cfg.acoustic.bandwidth_hz),
                "snr_threshold_db": float(cfg.acoustic.get("snr_threshold_db", 12.0)),
            }
        )
        trace, gpu_meta, _lut = generate_mission_trace_gpu(
            engine,
            world_positions,
            times,
            seed=int(cfg.seed),
            noise_psd_dbm_hz=float(cfg.acoustic.noise_psd_dbm_hz),
            bandwidth_hz=float(cfg.acoustic.bandwidth_hz),
            tx_power_dbm=float(cfg.acoustic.tx_power_dbm),
            phy=phy,
            repo_root=Path(cfg.paths.root),
        )
        trace.trace_id = trace_id
        extra = {
            "backend": "aubellhop" if paper_run else engine.backend,
            "allow_fallback": bool(cfg.acoustic.get("allow_fallback", False)),
            "use_sionna_bridge": True,
            "sionna_device": gpu_meta.get("cuda_device") or gpu_meta.get("device"),
            "gpu_phy": True,
            "doppler_mode": "not_modeled",
            "trace_family": "gpu_phy",
            "git_commit": git_commit(Path(cfg.paths.root)),
            "n_nodes": len(world_positions),
            "duration_s": duration,
            **{k: v for k, v in gpu_meta.items() if k != "lut"},
        }
        store.save(trace, extra_meta=extra)
        if paper_run:
            assert_trace_paper_meta(store.load_meta(trace_id), require_gpu_phy=True)
        return trace

    sionna_device = "cpu"
    if paper_run:
        from cgea.sionna_ext import UnderwaterAcousticChannel

        auv_positions = [p for aid, p in world_positions.items() if not str(aid).startswith("gw")]
        if len(auv_positions) < 2:
            auv_positions = list(world_positions.values())
        probe = engine.compute_channel(
            "probe_tx",
            "probe_rx",
            auv_positions[0],
            auv_positions[1],
            seed=int(cfg.seed),
        )
        assert_aubellhop_backend(probe.backend)
        ch = UnderwaterAcousticChannel(realization=probe)
        a, _tau = ch(batch_size=2, num_time_steps=1, sampling_frequency=float(cfg.acoustic.bandwidth_hz))
        assert_cuda_device(a.device)
        sionna_device = str(a.device)

    trace = generate_mission_trace(
        engine,
        world_positions,
        times,
        seed=int(cfg.seed),
        noise_psd_dbm_hz=float(cfg.acoustic.noise_psd_dbm_hz),
        bandwidth_hz=float(cfg.acoustic.bandwidth_hz),
        tx_power_dbm=float(cfg.acoustic.tx_power_dbm),
        use_sionna_bridge=bool(cfg.acoustic.get("use_sionna_bridge", True)) if paper_run else bool(
            cfg.acoustic.get("use_sionna_bridge", False)
        ),
        snr_threshold_db=float(cfg.acoustic.get("snr_threshold_db", 12.0)),
    )
    trace.trace_id = trace_id
    store.save(
        trace,
        extra_meta={
            "backend": engine.backend if not paper_run else "aubellhop",
            "allow_fallback": bool(cfg.acoustic.get("allow_fallback", False)),
            "use_sionna_bridge": bool(cfg.acoustic.get("use_sionna_bridge", paper_run)),
            "sionna_device": sionna_device,
            "gpu_phy": False,
            "doppler_mode": "not_modeled",
            "trace_family": "legacy_sionna_wrap",
            "git_commit": git_commit(Path(cfg.paths.root)),
            "n_nodes": len(world_positions),
            "duration_s": duration,
        },
    )
    if paper_run:
        assert_trace_paper_meta(store.load_meta(trace_id), require_gpu_phy=False)
    return trace


def _bump(counter: dict[str, int], key: str, n: int = 1) -> None:
    counter[key] = counter.get(key, 0) + n


def run_single(cfg: DictConfig, baseline: str) -> RunMetrics:
    seed = int(cfg.seed)
    np.random.seed(seed)
    paper_run = not bool(cfg.acoustic.get("allow_fallback", False))
    if paper_run:
        assert_paper_config(cfg)

    world = build_pipeline_mission(
        n_auvs=int(cfg.mission.n_auvs),
        pipeline_length_m=float(cfg.mission.pipeline_length_m),
        depth_m=float(cfg.mission.depth_m),
        battery_j=float(cfg.mission.battery_j),
        reserve_j=float(cfg.mission.reserve_j),
        mission_id=str(cfg.mission.mission_id),
        workload=str(cfg.mission.get("workload", "nominal")),
    )
    positions = {aid: a.position for aid, a in world.auvs.items()}
    trace = ensure_trace(cfg, positions)
    store = ChannelTraceStore(Path(cfg.paths.traces))
    if paper_run:
        assert_trace_paper_meta(
            store.load_meta(trace.trace_id),
            require_gpu_phy=bool(cfg.acoustic.get("use_gpu_phy", True)),
        )

    env = simpy.Environment()
    node_ids = list(world.auvs.keys())
    net = UnderwaterNetwork(env, node_ids, world.gateway_id, trace, queue_limit=int(cfg.network.queue_limit))

    planner = MissionPlanner(
        consequential_period_s=float(cfg.agent.consequential_period_s),
        seed=seed,
    )
    adapter = ExecutionAdapter()
    controller = make_baseline(baseline, planner, adapter)

    freshness_mode = str(cfg.governance.get("freshness_mode", "continuous"))
    immediate_resume = bool(cfg.governance.get("immediate_resume", False))
    soft_h = float(cfg.governance.soft_expiry_s)
    hard_h = float(cfg.governance.hard_expiry_s)

    capsules = {
        aid: issue_capsule(aid, world.mission_id, 0.0, world.gateway_id, soft_h, hard_h, broad=True)
        for aid in node_ids
        if not world.auvs[aid].is_gateway
    }
    last_auth = {aid: 0.0 for aid in capsules}
    last_supervisor = {aid: 0.0 for aid in capsules}
    journals: dict[str, list[NodeJournalEntry]] = {aid: [] for aid in capsules}

    classifier = ConnectivityClassifier(
        pdr_degraded=float(cfg.governance.connectivity.pdr_degraded),
        supervisor_timeout_s=float(cfg.governance.connectivity.supervisor_timeout_s),
        authority_timeout_s=float(cfg.governance.connectivity.authority_timeout_s),
        min_throughput_bps=float(cfg.governance.connectivity.min_throughput_bps),
    )

    outage_start = float(cfg.scenario.get("outage_start_s", 1e18))
    outage_end = float(cfg.scenario.get("outage_end_s", 1e18))
    outage_duration_s = max(0.0, outage_end - outage_start) if outage_end < 1e17 else 0.0
    if bool(cfg.scenario.get("outage_disabled", False)):
        outage_start, outage_end, outage_duration_s = 1e18, 1e18, 0.0
    partition_groups = cfg.scenario.get("partition_groups", None)

    governance_bytes = 0
    decisions_allow = 0
    decisions_deny = 0
    decisions_defer = 0
    consequential_proposed = 0
    consequential_allowed = 0
    high_risk_action_count = 0
    false_denials = 0
    denied_consequential = 0
    useful_cons_allowed = 0
    useful_cons_proposed = 0
    recovering = False
    recon_latency = 0.0
    conflict_count = 0
    recon_done = False

    proposed_by_type: dict[str, int] = {}
    decision_by_type: dict[str, dict[str, int]] = {}
    decision_by_risk: dict[str, dict[str, int]] = {
        "low": {"ALLOW": 0, "DENY": 0, "DEFER": 0},
        "consequential": {"ALLOW": 0, "DENY": 0, "DEFER": 0},
    }
    connectivity_ticks: dict[str, int] = {s.value: 0 for s in ConnectivityState}
    cons_classes = [a.value for a in CONSEQUENTIAL_ACTIONS]
    coverage_by_class: dict[str, dict[str, int]] = {
        c: {"proposed": 0, "ALLOW": 0, "DENY": 0, "DEFER": 0} for c in cons_classes
    }
    coverage_by_conn: dict[str, dict[str, int]] = {
        s.value: {"proposed": 0, "ALLOW": 0, "DENY": 0, "DEFER": 0} for s in ConnectivityState
    }
    coverage_by_fresh: dict[str, dict[str, int]] = {
        f.value: {"proposed": 0, "ALLOW": 0, "DENY": 0, "DEFER": 0} for f in AuthorityFreshness
    }
    denied_consequential_log: list[dict[str, Any]] = []
    timeline_auv = str(cfg.mission.get("timeline_auv_id", "auv_08"))
    authority_timeline: list[dict[str, Any]] = []
    freshness_policy = controller.governor.freshness_policy

    tick = float(cfg.mission.sim_tick_s)
    duration = float(cfg.mission.duration_s)

    def mission_loop():
        nonlocal decisions_allow, decisions_deny, decisions_defer
        nonlocal consequential_proposed, consequential_allowed, high_risk_action_count
        nonlocal false_denials, denied_consequential, useful_cons_allowed, useful_cons_proposed
        nonlocal recovering, recon_done, recon_latency, conflict_count

        while env.now < duration:
            t = env.now
            if outage_start <= t < outage_end:
                if partition_groups:
                    groups = [set(g) for g in partition_groups]
                    net.force_partitions(groups)
                else:
                    auvs = [n for n in node_ids if n != world.gateway_id]
                    mid = len(auvs) // 2
                    g1 = set(auvs[:mid]) | {world.gateway_id}
                    g2 = set(auvs[mid:])
                    net.force_partitions([g1, g2])
            elif t >= outage_end and net._forced_partition_groups is not None and not recon_done:
                net.force_partitions(None)
                recovering = True
                digests = [make_digest(nid, journals[nid]) for nid in journals]
                result = reconcile_partitions(
                    digests, journals, t, immediate_resume=immediate_resume
                )
                recon_latency = result.latency_s
                conflict_count = result.conflicts
                if not immediate_resume and result.latency_s > 0:
                    yield env.timeout(result.latency_s)
                    for aid in capsules:
                        capsules[aid] = issue_capsule(
                            aid,
                            world.mission_id,
                            env.now,
                            world.gateway_id,
                            soft_h,
                            hard_h,
                            broad=True,
                        )
                        last_auth[aid] = env.now
                recovering = False
                recon_done = True

            world.time_s = float(env.now)
            g = net.connectivity_graph()
            parts = net.partitions()

            for aid, auv in world.auvs.items():
                if auv.is_gateway:
                    continue

                neighbors = list(g.successors(aid)) if aid in g else []
                auv.neighbor_table = neighbors
                gw_reach = net.gateway_reachable(aid)
                auv.local_observations["_gw_reachable"] = gw_reach
                if gw_reach:
                    last_supervisor[aid] = env.now
                    if env.now - last_auth[aid] > float(cfg.governance.authority_refresh_s):
                        capsules[aid] = issue_capsule(
                            aid, world.mission_id, env.now, world.gateway_id, soft_h, hard_h, broad=True
                        )
                        last_auth[aid] = env.now
                        pkt = Packet(
                            packet_id=f"auth-{aid}-{env.now}",
                            src=world.gateway_id,
                            dst=aid,
                            ptype=PacketType.AUTHORITY,
                            size_bytes=int(cfg.governance.authority_capsule_bytes),
                            payload={"capsule_id": capsules[aid].capsule_id},
                            created_at=env.now,
                        )
                        net.send(pkt)

                part_size = next((len(p) for p in parts if aid in p), 1)
                recent = [d for d in net.deliveries if d.packet.src == aid or d.packet.dst == aid][-20:]
                pdr = (
                    sum(1 for d in recent if d.success) / len(recent) if recent else (1.0 if gw_reach else 0.2)
                )
                sample = net.link_sample(aid, world.gateway_id)
                thr = sample.estimated_rate_bps if sample else 0.0
                metrics = ConnectivityMetrics(
                    packet_delivery_ratio=pdr,
                    mean_delay_s=sample.propagation_delay_s if sample else 0.0,
                    available_throughput_bps=thr,
                    time_since_supervisor_s=env.now - last_supervisor[aid],
                    time_since_authority_update_s=env.now - last_auth[aid],
                    neighbor_count=len(neighbors),
                    gateway_reachable=gw_reach,
                    partition_size=part_size,
                    local_population=len(node_ids),
                )
                conn = classifier.classify(metrics, recovering=recovering)
                _bump(connectivity_ticks, conn.value)

                age_s = authority_age(env.now, last_auth[aid])
                if env.now >= capsules[aid].hard_expiry:
                    freshness = AuthorityFreshness.HARD_EXPIRED
                else:
                    freshness = classify_freshness(age_s, capsules[aid], freshness_policy)

                proposal = planner.propose(world, aid)
                atype = proposal.action_type.value
                risk_key = "consequential" if proposal.risk_class == RiskClass.CONSEQUENTIAL else "low"
                _bump(proposed_by_type, atype)
                label = None
                if proposal.risk_class == RiskClass.CONSEQUENTIAL:
                    consequential_proposed += 1
                    label = oracle_label(proposal, world)
                    if label.mission_beneficial:
                        useful_cons_proposed += 1
                    _bump(coverage_by_class.setdefault(atype, {"proposed": 0, "ALLOW": 0, "DENY": 0, "DEFER": 0}), "proposed")
                    _bump(coverage_by_conn[conn.value], "proposed")
                    _bump(coverage_by_fresh[freshness.value], "proposed")

                result = controller.decide(
                    proposal,
                    conn,
                    capsules[aid],
                    last_auth[aid],
                    env.now,
                    auv.energy,
                    supervisor_reachable=gw_reach,
                    position={"x": auv.position.x, "y": auv.position.y},
                    freshness_mode=freshness_mode,
                    immediate_resume=immediate_resume,
                )

                dec = result.decision.value
                decision_by_type.setdefault(atype, {"ALLOW": 0, "DENY": 0, "DEFER": 0})
                _bump(decision_by_type[atype], dec)
                _bump(decision_by_risk[risk_key], dec)
                if proposal.risk_class == RiskClass.CONSEQUENTIAL:
                    _bump(coverage_by_class[atype], dec)
                    _bump(coverage_by_conn[conn.value], dec)
                    _bump(coverage_by_fresh[freshness.value], dec)

                if result.decision == GovernorDecision.ALLOW:
                    decisions_allow += 1
                    adapter.execute(world, proposal)
                    if proposal.risk_class == RiskClass.CONSEQUENTIAL:
                        consequential_allowed += 1
                        if label is not None and label.mission_beneficial:
                            useful_cons_allowed += 1
                        if conn in DISCONNECTED_LIKE or not gw_reach:
                            high_risk_action_count += 1
                elif result.decision == GovernorDecision.DEFER:
                    decisions_defer += 1
                else:
                    decisions_deny += 1
                    if proposal.risk_class == RiskClass.CONSEQUENTIAL and label is not None:
                        denied_consequential += 1
                        denied_consequential_log.append(
                            {
                                "time_s": float(env.now),
                                "auv": aid,
                                "action": atype,
                                "connectivity": conn.value,
                                "freshness": freshness.value,
                                "authority_age_s": age_s,
                                "mission_beneficial": label.mission_beneficial,
                                "violates_frozen_risk": label.violates_frozen_risk,
                                "false_denial": label.false_denial_if_denied,
                                "oracle_reason": label.reason,
                                "rationale": proposal.rationale,
                            }
                        )
                        if label.false_denial_if_denied:
                            false_denials += 1

                if aid == timeline_auv:
                    authority_timeline.append(
                        {
                            "time_s": float(env.now),
                            "connectivity": conn.value,
                            "authority_age_s": age_s,
                            "freshness": freshness.value,
                            "action": atype,
                            "risk": risk_key,
                            "decision": dec,
                            "gw_reachable": gw_reach,
                            "outage": bool(outage_start <= env.now < outage_end),
                        }
                    )

                journals[aid].append(
                    NodeJournalEntry(
                        time=env.now,
                        node_id=aid,
                        actions_proposed=[atype],
                        actions_executed=[atype] if result.decision == GovernorDecision.ALLOW else [],
                        capsule_id=capsules[aid].capsule_id if str(baseline).startswith("B4") else None,
                        energy_battery_j=auv.energy.battery_j,
                        task_ownership={s.segment_id: (s.owner or "") for s in world.segments.values()},
                        observations=dict(auv.local_observations),
                        state_version=auv.state_version,
                    )
                )

            yield env.timeout(tick)

    env.process(mission_loop())
    env.run(until=duration)

    acc = net.total_bytes()
    governance_bytes = int(acc["governance_tx"])
    total_bytes = int(acc["tx"])

    unauth_rate = high_risk_action_count / max(consequential_proposed, 1)
    false_denial_rate = false_denials / max(denied_consequential, 1)
    retention = useful_cons_allowed / max(useful_cons_proposed, 1)

    energy_p = sum(a.energy.propulsion_j for a in world.auvs.values())
    energy_c = sum(a.energy.communication_j for a in world.auvs.values()) + 0.001 * total_bytes
    energy_k = sum(a.energy.compute_j for a in world.auvs.values())

    total_conn_ticks = max(sum(connectivity_ticks.values()), 1)
    conn_occupancy = {k: v / total_conn_ticks for k, v in connectivity_ticks.items()}

    ub = world_utility(world, unresolved_conflicts=conflict_count)
    exercised = [c for c, v in coverage_by_class.items() if v.get("proposed", 0) > 0]
    conn_frac = {}
    for s, v in coverage_by_conn.items():
        tot = max(v.get("proposed", 0), 1)
        conn_frac[s] = {k: (v.get(k, 0) / tot if k != "proposed" else v.get(k, 0)) for k in ("proposed", "ALLOW", "DENY", "DEFER")}
        conn_frac[s]["fraction_of_consequential"] = v.get("proposed", 0) / max(consequential_proposed, 1)
    fresh_frac = {}
    for s, v in coverage_by_fresh.items():
        tot = max(v.get("proposed", 0), 1)
        fresh_frac[s] = {k: v.get(k, 0) for k in ("proposed", "ALLOW", "DENY", "DEFER")}
        fresh_frac[s]["fraction_of_consequential"] = v.get("proposed", 0) / max(consequential_proposed, 1)

    prov = Provenance(
        git_commit=git_commit(Path(cfg.paths.root)),
        configuration_hash=config_hash(cfg),
        random_seed=seed,
        baseline=baseline,
        channel_trace_id=trace.trace_id,
        environment_id=str(cfg.acoustic.environment_id),
        notes=f"outage_duration_s={outage_duration_s}",
    )
    metrics = RunMetrics(
        mission_completion_ratio=world.completion_ratio(),
        mission_utility=ub.utility,
        unauthorized_high_risk_action_rate=unauth_rate,
        governance_false_denial_rate=false_denial_rate,
        useful_action_retention=retention,
        governance_bytes=int(governance_bytes),
        total_communication_bytes=int(total_bytes),
        governance_communication_overhead=compute_overhead(governance_bytes, total_bytes),
        reconciliation_latency_s=recon_latency,
        conflict_count=conflict_count,
        energy_propulsion_j=energy_p,
        energy_communication_j=energy_c,
        energy_compute_j=energy_k,
        provenance=prov,
        extra={
            "gco_convention": "governance_TX_bytes / total_TX_bytes",
            "tx_bytes": int(acc["tx"]),
            "rx_bytes": int(acc["rx"]),
            "governance_tx_bytes": int(acc["governance_tx"]),
            "doppler_mode": str(cfg.acoustic.get("doppler_mode", "not_modeled")),
            "gpu_phy": bool(cfg.acoustic.get("use_gpu_phy", paper_run)),
            "trace_family": "gpu_phy" if bool(cfg.acoustic.get("use_gpu_phy", paper_run)) else "legacy_sionna_wrap",
            "outage_start_s": outage_start if outage_start < 1e17 else None,
            "outage_end_s": outage_end if outage_end < 1e17 else None,
            "high_risk_action_count": high_risk_action_count,
            "consequential_proposed": consequential_proposed,
            "consequential_allowed": consequential_allowed,
            "false_denial_count": false_denials,
            "denied_consequential_count": denied_consequential,
            "useful_consequential_proposed": useful_cons_proposed,
            "useful_consequential_allowed": useful_cons_allowed,
            "useful_consequential_retention": retention,
            "denied_consequential": denied_consequential_log[:200],
            "decisions_allow": decisions_allow,
            "decisions_deny": decisions_deny,
            "decisions_defer": decisions_defer,
            "proposed_by_type": proposed_by_type,
            "decision_by_type": decision_by_type,
            "decision_by_risk_class": decision_by_risk,
            "action_opportunity_coverage": {
                "by_class": coverage_by_class,
                "by_connectivity": conn_frac,
                "by_freshness": fresh_frac,
                "exercised_consequential_classes": exercised,
                "n_exercised_consequential_classes": len(exercised),
                "valid_coverage": len(exercised) >= 4,
            },
            "utility_breakdown": ub.model_dump(),
            "utility_freeze_id": UTILITY_FREEZE_ID,
            "workload": world.workload,
            "authority_timeline_auv": timeline_auv,
            "authority_timeline": authority_timeline,
            "connectivity_state_occupancy": conn_occupancy,
            "connectivity_state_ticks": connectivity_ticks,
            "trace_backend": store.load_meta(trace.trace_id).get("backend"),
            "allow_fallback": False,
            "use_sionna_bridge": True,
            "sionna_device": store.load_meta(trace.trace_id).get("sionna_device"),
        },
    )
    out_dir = Path(cfg.paths.results) / str(cfg.experiment.name) / baseline
    save_metrics(metrics, out_dir)
    return metrics


def run_experiment(cfg: DictConfig) -> list[RunMetrics]:
    baselines = list(cfg.experiment.baselines)
    seeds = list(cfg.experiment.seeds)
    results: list[RunMetrics] = []
    for seed in seeds:
        cfg_s = OmegaConf.merge(cfg, {"seed": int(seed)})
        for b in baselines:
            results.append(run_single(cfg_s, str(b)))
    return results
