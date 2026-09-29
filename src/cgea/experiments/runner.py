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
from cgea.agent import ActionProposal, ExecutionAdapter, MissionPlanner
from cgea.experiments.e2_authority_age import (
    AUTHORITY_EPOCH_S,
    capture_snapshot,
    controlled_reassign_proposal,
    e2_recovers_globally,
    fail_target,
    local_conditional_ok,
    recover_target,
    resolve_target_segment,
    unfail_stress_targets,
)
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
    PAPER_POLICY_VERSION,
    authority_age,
    classify_freshness,
    issue_capsule,
    make_digest,
    reassign_condition_satisfied,
    reconcile_partitions,
)
from cgea.governance.semantic_conflicts import detect_semantic_conflicts
from cgea.experiments.paper_gates import (
    PaperAssertionError,
    assert_aubellhop_backend,
    assert_cuda_device,
    assert_paper_config,
    assert_trace_paper_meta,
)
from cgea.metrics import RunMetrics, compute_overhead, save_metrics
from cgea.mission import CONSEQUENTIAL_ACTIONS, ActionType, RiskClass, build_pipeline_mission
from cgea.mission.oracle import oracle_label
from cgea.mission.utility import UTILITY_FREEZE_ID, world_utility
from cgea.network import Packet, PacketType, UnderwaterNetwork
from cgea.types import Position3D, Provenance, config_hash, git_commit

SAFE_USEFUL_RETENTION_FREEZE_ID = "safe_useful_retention_v1_2026-09-28"
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
    net = UnderwaterNetwork(
        env,
        node_ids,
        world.gateway_id,
        trace,
        queue_limit=int(cfg.network.queue_limit),
        governance_reserved_queue_slots=int(cfg.network.get("governance_reserved_queue_slots", 4)),
    )

    planner = MissionPlanner(
        consequential_period_s=float(cfg.agent.consequential_period_s),
        seed=seed,
    )
    e2_cfg = cfg.experiment.get("e2_authority_age_context")
    e2_on = bool(e2_cfg and e2_cfg.get("enabled"))
    challenge_path = Path(cfg.paths.root) / "configs" / "mission" / "freshness_challenge.yaml"
    if challenge_path.is_file() and not e2_on:
        ch = OmegaConf.load(challenge_path)
        planner.challenge_times_s = [float(x) for x in ch.get("proposal_times_s", [])]
        planner.challenge_cycle = [str(x) for x in ch.get("action_cycle", [])]
        planner.sim_tick_s = float(cfg.mission.sim_tick_s)
        n_auvs = int(cfg.mission.n_auvs)
        planner.challenge_cohort = {f"auv_{i:02d}" for i in range(n_auvs // 2, n_auvs)}
    adapter = ExecutionAdapter()
    controller = make_baseline(baseline, planner, adapter)
    evidence_mode = str(baseline) in (
        "B4-Evidence",
        "B4_Evidence",
        "B3-Evidence",
        "B3_Evidence",
    ) or str(cfg.governance.get("authority_mode", "")) == "evidence"
    evidence_policy = None
    evidence_stores: dict[str, Any] = {}
    evidence_local_ver: dict[str, int] = {}
    if evidence_mode:
        from cgea.governance.evidence import EvidenceStore, load_action_evidence_policy

        evidence_policy = load_action_evidence_policy(
            Path(cfg.paths.root) / "configs" / "governance" / "action_evidence_policy_v1.yaml"
        )
        evidence_stores = {aid: EvidenceStore() for aid in node_ids if not world.auvs[aid].is_gateway}
        evidence_local_ver = {aid: 0 for aid in evidence_stores}

    if str(baseline) in ("B4-FixedExpiry", "B4_FixedExpiry", "B4_fixed_expiry"):
        ttl_raw = None
        if e2_cfg is not None:
            ttl_raw = e2_cfg.get("lease_ttl_s")
        if ttl_raw is None:
            ttl_raw = cfg.experiment.get("lease_ttl_s")
        if ttl_raw is None or str(ttl_raw).lower() in ("inf", "infinity", "none"):
            controller.lease_ttl_s = float("inf")
        else:
            controller.lease_ttl_s = float(ttl_raw)

    freshness_mode = str(cfg.governance.get("freshness_mode", "continuous"))
    immediate_resume = bool(cfg.governance.get("immediate_resume", False))
    soft_h = float(cfg.governance.soft_expiry_s)
    hard_h = float(cfg.governance.hard_expiry_s)

    gcfg = cfg.governance.get("capsule_grants")
    capsule_grants = None
    if gcfg:
        capsule_grants = {
            "conditional": [str(x) for x in list(gcfg.get("conditional", []))],
            "forbid": [str(x) for x in list(gcfg.get("forbid", []))],
        }
    policy_version = str(cfg.governance.get("policy_version", PAPER_POLICY_VERSION))

    capsules = {
        aid: issue_capsule(
            aid, world.mission_id, 0.0, world.gateway_id, soft_h, hard_h, broad=False, grants=capsule_grants
        )
        for aid in node_ids
        if not world.auvs[aid].is_gateway
    }
    last_auth = {aid: 0.0 for aid in capsules}
    last_supervisor = {aid: 0.0 for aid in capsules}
    e2_state: dict[str, Any] = {
        "on": e2_on,
        "snapshot": None,
        "n_controlled": 0,
        "event": None,
        "epoch_done": False,
        "change_done": False,
    }
    if e2_on:
        proposer = str(e2_cfg.proposer_id)
        target = str(e2_cfg.target_auv)
        if proposer not in world.auvs or world.auvs[proposer].is_gateway:
            raise RuntimeError(f"E2: proposer {proposer} missing")
        if target not in world.auvs:
            raise RuntimeError(f"E2: target {target} missing")
        e2_state["proposer"] = proposer
        e2_state["target"] = target
        e2_state["segment_id"] = resolve_target_segment(world, target)
        e2_state["epoch"] = float(e2_cfg.get("authority_epoch_s", AUTHORITY_EPOCH_S))
        e2_state["challenge_time"] = float(e2_cfg.challenge_time_s)
        e2_state["authority_age"] = float(e2_cfg.authority_age_s)
        e2_state["context"] = str(e2_cfg.context_mode)
        e2_state["expected_freshness"] = str(e2_cfg.expected_freshness)
        e2_state["change_time"] = float(e2_state["epoch"]) + float(e2_cfg.get("change_age_s", 300.0))
        unfail_stress_targets(world, keep="")
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
    safe_useful_allowed = 0
    safe_useful_proposed = 0
    recovering = False
    recon_latency = 0.0
    conflict_count = 0
    recon_done = False
    recon_active = False
    recon_start_s = None
    recon_status = "idle"
    digest_ok: set[str] = set()
    digest_attempts: dict[str, int] = {}
    digest_last_try: dict[str, float] = {}
    recovering_ids: set[str] = set()
    reauth_ok: set[str] = set()
    reauth_latency: dict[str, float] = {}
    recon_timeout_ids: set[str] = set()
    recruits_used: dict[str, int] = {}
    hard_safety_allow = 0
    reason_counts: dict[str, int] = {}
    recon_cfg = cfg.governance.get("reconciliation", {})
    digest_max_retries = int(recon_cfg.get("digest_max_retries", 3))
    digest_retry_s = float(recon_cfg.get("digest_retry_s", 20.0))
    digest_bytes = int(recon_cfg.get("digest_bytes", 128))
    provenance_bytes = int(recon_cfg.get("provenance_bytes", 256))
    reconcile_bytes = int(recon_cfg.get("reconcile_bytes", 128))

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
    log_cons = bool(cfg.experiment.get("log_consequential_decisions", False)) or e2_on
    cons_decision_log: list[dict[str, Any]] = []
    timeline_auv = str(cfg.mission.get("timeline_auv_id", "auv_08"))
    authority_timeline: list[dict[str, Any]] = []
    freshness_policy = controller.governor.freshness_policy

    tick = float(cfg.mission.sim_tick_s)
    duration = float(cfg.mission.duration_s)

    def mission_loop():
        nonlocal decisions_allow, decisions_deny, decisions_defer
        nonlocal consequential_proposed, consequential_allowed, high_risk_action_count
        nonlocal false_denials, denied_consequential, useful_cons_allowed, useful_cons_proposed
        nonlocal safe_useful_allowed, safe_useful_proposed
        nonlocal recovering, recon_done, recon_latency, conflict_count
        nonlocal recon_active, recon_start_s, recon_status, hard_safety_allow
        nonlocal recovering_ids

        while env.now < duration:
            t = env.now
            if e2_on and (not e2_state["epoch_done"]) and t + 1e-9 >= e2_state["epoch"]:
                fail_target(world, e2_state["target"])
                snap = capture_snapshot(
                    world,
                    proposer_id=e2_state["proposer"],
                    target_auv=e2_state["target"],
                    segment_id=e2_state["segment_id"],
                    t=e2_state["epoch"],
                )
                e2_state["snapshot"] = snap
                pid = e2_state["proposer"]
                capsules[pid] = issue_capsule(
                    pid,
                    world.mission_id,
                    e2_state["epoch"],
                    world.gateway_id,
                    soft_h,
                    hard_h,
                    broad=False,
                    grants=capsule_grants,
                )
                last_auth[pid] = float(e2_state["epoch"])
                e2_state["capsule_id"] = capsules[pid].capsule_id
                e2_state["epoch_done"] = True
                if evidence_mode:
                    from cgea.governance.evidence import populate_from_trusted_snapshot

                    populate_from_trusted_snapshot(
                        evidence_stores[pid],
                        proposer_id=pid,
                        snapshot=snap,
                        energy=world.auvs[pid].energy,
                        position=world.auvs[pid].position,
                        observations=dict(world.auvs[pid].local_observations),
                        version=1,
                    )
                    evidence_local_ver[pid] = 1
            if (
                e2_on
                and e2_recovers_globally(str(e2_state["context"]))
                and (not e2_state["change_done"])
                and t + 1e-9 >= e2_state["change_time"]
            ):
                recover_target(world, e2_state["target"])
                e2_state["change_done"] = True
                e2_state["ground_truth_change_time"] = float(e2_state["change_time"])
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
                recovering_ids = set(capsules)
                recon_active = True
                recon_start_s = float(t)
                recon_status = "digest"
                digest_ok.clear()
                digest_attempts.clear()
                digest_last_try.clear()
                reauth_ok.clear()
                reauth_latency.clear()
                recon_timeout_ids.clear()
                recon_done = True
                if immediate_resume:
                    recovering_ids.clear()
                    recon_active = False
                    recon_status = "immediate"
                    recon_latency = 0.0

            if recon_active:
                for aid in list(recovering_ids):
                    for d in net.deliveries:
                        pkt = d.packet
                        origin = pkt.origin or pkt.src
                        final = pkt.final_dst or pkt.dst
                        if (
                            d.success
                            and pkt.ptype == PacketType.DIGEST
                            and origin == aid
                            and pkt.dst == world.gateway_id
                            and final == world.gateway_id
                        ):
                            digest_ok.add(aid)
                            break
                    if aid in digest_ok:
                        net.send(
                            Packet(
                                packet_id=f"prov-{aid}-{t}",
                                src=aid,
                                dst=world.gateway_id,
                                ptype=PacketType.PROVENANCE,
                                size_bytes=provenance_bytes,
                                payload={"ownership_hash": make_digest(aid, journals.get(aid, [])).ownership_hash},
                                created_at=t,
                            )
                        )
                        net.send(
                            Packet(
                                packet_id=f"reconcile-gw-{aid}-{t}",
                                src=world.gateway_id,
                                dst=aid,
                                ptype=PacketType.RECONCILE,
                                size_bytes=reconcile_bytes,
                                payload={"status": "commit", "subject": aid},
                                created_at=t,
                            )
                        )
                        capsules[aid] = issue_capsule(
                            aid,
                            world.mission_id,
                            env.now,
                            world.gateway_id,
                            soft_h,
                            hard_h,
                            broad=False,
                            grants=capsule_grants,
                        )
                        last_auth[aid] = env.now
                        if evidence_mode:
                            from cgea.governance.evidence import refresh_known_remote_from_supervisor_view

                            refresh_known_remote_from_supervisor_view(
                                evidence_stores[aid],
                                peer_failed_by_id={
                                    x: (x in world.failed_auv_ids) for x in world.auvs if not world.auvs[x].is_gateway
                                },
                                segments={
                                    sid: {
                                        "mandatory": bool(s.mandatory),
                                        "incomplete": bool((not s.completed) and (not s.abandoned)),
                                        "owner": s.owner,
                                    }
                                    for sid, s in world.segments.items()
                                },
                                trusted_at=float(env.now),
                                source="recon",
                            )
                        net.send(
                            Packet(
                                packet_id=f"auth-reauth-{aid}-{t}",
                                src=world.gateway_id,
                                dst=aid,
                                ptype=PacketType.AUTHORITY,
                                size_bytes=int(cfg.governance.authority_capsule_bytes),
                                payload={"capsule_id": capsules[aid].capsule_id},
                                created_at=t,
                            )
                        )
                        reauth_ok.add(aid)
                        reauth_latency[aid] = float(t) - float(recon_start_s or t)
                        recovering_ids.discard(aid)
                        continue
                    ntry = digest_attempts.get(aid, 0)
                    last = digest_last_try.get(aid, -1e18)
                    if ntry < digest_max_retries and (t - last) >= digest_retry_s - 1e-9:
                        dgst = make_digest(aid, journals.get(aid, []))
                        net.send(
                            Packet(
                                packet_id=f"digest-{aid}-{t}-{ntry}",
                                src=aid,
                                dst=world.gateway_id,
                                ptype=PacketType.DIGEST,
                                size_bytes=digest_bytes,
                                payload=dgst.model_dump(),
                                created_at=t,
                            )
                        )
                        digest_attempts[aid] = ntry + 1
                        digest_last_try[aid] = float(t)
                    elif ntry >= digest_max_retries:
                        recon_timeout_ids.add(aid)
                        recovering_ids.discard(aid)
                if not recovering_ids:
                    recon_active = False
                    n_auvs = max(len(capsules), 1)
                    if len(reauth_ok) == n_auvs:
                        recon_status = "ok"
                    elif reauth_ok:
                        recon_status = "partial"
                    else:
                        recon_status = "timeout"
                    recon_latency = max(reauth_latency.values()) if reauth_latency else float(t) - float(recon_start_s or t)

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
                    skip_refresh = bool(e2_on and aid == e2_state.get("proposer"))
                    if (not skip_refresh) and env.now - last_auth[aid] > float(cfg.governance.authority_refresh_s):
                        capsules[aid] = issue_capsule(
                            aid,
                            world.mission_id,
                            env.now,
                            world.gateway_id,
                            soft_h,
                            hard_h,
                            broad=False,
                            grants=capsule_grants,
                        )
                        last_auth[aid] = env.now
                        if evidence_mode:
                            from cgea.governance.evidence import refresh_known_remote_from_supervisor_view

                            refresh_known_remote_from_supervisor_view(
                                evidence_stores[aid],
                                peer_failed_by_id={
                                    x: (x in world.failed_auv_ids) for x in world.auvs if not world.auvs[x].is_gateway
                                },
                                segments={
                                    sid: {
                                        "mandatory": bool(s.mandatory),
                                        "incomplete": bool((not s.completed) and (not s.abandoned)),
                                        "owner": s.owner,
                                    }
                                    for sid, s in world.segments.items()
                                },
                                trusted_at=float(env.now),
                                source="authority_packet",
                            )
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
                conn = classifier.classify(metrics, recovering=(aid in recovering_ids))
                if evidence_mode and aid in evidence_stores:
                    from cgea.governance.evidence import put_local_self

                    evidence_local_ver[aid] = int(evidence_local_ver.get(aid, 0)) + 1
                    put_local_self(
                        evidence_stores[aid],
                        auv_id=aid,
                        now=float(env.now),
                        energy=auv.energy,
                        position=auv.position,
                        observations=dict(auv.local_observations),
                        version=evidence_local_ver[aid],
                    )
                _bump(connectivity_ticks, conn.value)

                age_s = authority_age(env.now, last_auth[aid])
                if env.now >= capsules[aid].hard_expiry:
                    freshness = AuthorityFreshness.HARD_EXPIRED
                else:
                    freshness = classify_freshness(age_s, capsules[aid], freshness_policy)

                proposal = planner.propose(world, aid)
                if e2_on:
                    is_challenge = abs(t - float(e2_state["challenge_time"])) < 1e-9
                    if aid == e2_state["proposer"] and is_challenge:
                        if e2_state["snapshot"] is None:
                            raise RuntimeError("E2: snapshot missing at challenge")
                        proposal = controlled_reassign_proposal(world, e2_state["snapshot"])
                        e2_state["n_controlled"] += 1
                    elif aid == e2_state["proposer"]:
                        proposal = planner._hold(auv, "e2_wait_controlled")
                    elif aid == e2_state["target"]:
                        proposal = planner._hold(auv, "e2_hold_target")
                    elif proposal.action_type == ActionType.REASSIGN_ANOTHER_AUV:
                        proposal = planner._hold(auv, "e2_block_extra_reassign")
                atype = proposal.action_type.value
                risk_key = "consequential" if proposal.risk_class == RiskClass.CONSEQUENTIAL else "low"
                _bump(proposed_by_type, atype)
                label = None
                if proposal.risk_class == RiskClass.CONSEQUENTIAL:
                    consequential_proposed += 1
                    label = oracle_label(proposal, world)
                    if label.mission_beneficial:
                        useful_cons_proposed += 1
                    if label.mission_beneficial and not label.violates_frozen_risk:
                        safe_useful_proposed += 1
                    _bump(coverage_by_class.setdefault(atype, {"proposed": 0, "ALLOW": 0, "DENY": 0, "DEFER": 0}), "proposed")
                    _bump(coverage_by_conn[conn.value], "proposed")
                    _bump(coverage_by_fresh[freshness.value], "proposed")

                cond_ok = False
                if proposal.action_type.value == ActionType.REASSIGN_ANOTHER_AUV.value:
                    if evidence_mode:
                        # Recruits only. Predicates come from evaluate_requirements (local store).
                        cond_ok = int(recruits_used.get(aid, 0)) < int(capsules[aid].max_neighbor_recruits)
                    elif e2_on and e2_state.get("snapshot") and aid == e2_state.get("proposer"):
                        cond_ok = local_conditional_ok(
                            e2_state["snapshot"],
                            int(recruits_used.get(aid, 0)),
                            int(capsules[aid].max_neighbor_recruits),
                        )
                    else:
                        seg_id = proposal.parameters.get("segment_id")
                        seg = world.segments.get(seg_id) if seg_id else None
                        cond_ok = reassign_condition_satisfied(
                            proposal,
                            world.failed_auv_ids,
                            bool(seg and seg.mandatory),
                            bool(seg and (not seg.completed) and (not seg.abandoned)),
                            int(recruits_used.get(aid, 0)),
                            int(capsules[aid].max_neighbor_recruits),
                        )
                ev_kwargs: dict[str, Any] = {}
                if evidence_mode:
                    ev_kwargs = {
                        "authority_mode": "evidence",
                        "evidence_store": evidence_stores[aid],
                        "evidence_policy": evidence_policy,
                    }
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
                    violates_frozen_risk=bool(label.violates_frozen_risk) if label is not None else False,
                    conditional_ok=cond_ok,
                    **ev_kwargs,
                )
                _bump(reason_counts, result.reason_code.value)

                dec = result.decision.value
                decision_by_type.setdefault(atype, {"ALLOW": 0, "DENY": 0, "DEFER": 0})
                _bump(decision_by_type[atype], dec)
                _bump(decision_by_risk[risk_key], dec)
                if proposal.risk_class == RiskClass.CONSEQUENTIAL:
                    _bump(coverage_by_class[atype], dec)
                    _bump(coverage_by_conn[conn.value], dec)
                    _bump(coverage_by_fresh[freshness.value], dec)

                ur_before = int(world.useful_reassignment_count)
                dup_before = int(world.duplicate_work_count)
                anom_before = sum(1 for a in world.anomalies.values() if a.get("resolved"))
                miss_before = sum(
                    1 for s in world.segments.values() if s.mandatory and (not s.completed or s.abandoned)
                )
                util_before = None
                owner_before = None
                e2_global_before: dict[str, Any] | None = None
                if e2_on and proposal.rationale == "e2_controlled_reassign":
                    util_before = float(world_utility(world, unresolved_conflicts=0).utility)
                    sid = e2_state["segment_id"]
                    gseg_b = world.segments[sid]
                    owner_before = gseg_b.owner
                    e2_global_before = {
                        "global_target_failed": e2_state["target"] in world.failed_auv_ids,
                        "global_segment_mandatory": bool(gseg_b.mandatory),
                        "global_segment_incomplete": bool((not gseg_b.completed) and (not gseg_b.abandoned)),
                        "global_segment_owner": gseg_b.owner,
                    }
                if result.decision == GovernorDecision.ALLOW:
                    decisions_allow += 1
                    adapter.execute(world, proposal)
                    if proposal.risk_class == RiskClass.CONSEQUENTIAL:
                        consequential_allowed += 1
                        if label is not None and label.mission_beneficial:
                            useful_cons_allowed += 1
                        if label is not None and label.mission_beneficial and not label.violates_frozen_risk:
                            safe_useful_allowed += 1
                        if proposal.action_type.value == ActionType.REASSIGN_ANOTHER_AUV.value:
                            recruits_used[aid] = int(recruits_used.get(aid, 0)) + 1
                        if conn in DISCONNECTED_LIKE or not gw_reach:
                            high_risk_action_count += 1
                        if label is not None and label.violates_frozen_risk:
                            hard_safety_allow += 1
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

                if log_cons and proposal.risk_class == RiskClass.CONSEQUENTIAL:
                    cons_decision_log.append(
                        {
                            "time_s": float(env.now),
                            "auv": aid,
                            "action": atype,
                            "target_auv": proposal.parameters.get("target_auv"),
                            "segment_id": proposal.parameters.get("segment_id"),
                            "decision": dec,
                            "reason_code": result.reason_code.value,
                            "freshness": freshness.value,
                            "connectivity": conn.value,
                            "mission_beneficial": None if label is None else bool(label.mission_beneficial),
                            "violates_frozen_risk": None if label is None else bool(label.violates_frozen_risk),
                            "useful_reassignment_delta": int(world.useful_reassignment_count) - ur_before,
                            "duplicate_work_delta": int(world.duplicate_work_count) - dup_before,
                            "anomaly_resolved_delta": sum(
                                1 for a in world.anomalies.values() if a.get("resolved")
                            )
                            - anom_before,
                            "missed_mandatory_delta": sum(
                                1
                                for s in world.segments.values()
                                if s.mandatory and (not s.completed or s.abandoned)
                            )
                            - miss_before,
                            "rationale": proposal.rationale,
                            "evidence_policy_id": result.evidence_policy_id,
                            "evidence_evaluation": result.evidence_evaluation,
                            "oracle_global_target_failed": (
                                e2_state.get("target") in world.failed_auv_ids if e2_on else None
                            ),
                            "oracle_global_segment_owner": (
                                world.segments[e2_state["segment_id"]].owner if e2_on else None
                            ),
                            "oracle_global_segment_incomplete": (
                                (not world.segments[e2_state["segment_id"]].completed)
                                and (not world.segments[e2_state["segment_id"]].abandoned)
                                if e2_on
                                else None
                            ),
                        }
                    )

                if e2_on and proposal.rationale == "e2_controlled_reassign":
                    snap = e2_state["snapshot"]
                    executed = result.decision == GovernorDecision.ALLOW
                    gseg = world.segments[e2_state["segment_id"]]
                    owner_after = gseg.owner
                    gb = e2_global_before or {}
                    global_failed = bool(gb.get("global_target_failed", e2_state["target"] in world.failed_auv_ids))
                    override = bool(
                        executed and (not global_failed) and owner_after != e2_state["target"]
                    )
                    beneficial = bool(label.mission_beneficial) if label is not None else False
                    violates = bool(label.violates_frozen_risk) if label is not None else False
                    safe_useful = int(executed and beneficial and not violates)
                    obsolete = int(executed and cond_ok and (not beneficial))
                    allowed = int(executed)
                    false_denial = int(
                        (not executed)
                        and beneficial
                        and (not violates)
                    )
                    util_after = float(world_utility(world, unresolved_conflicts=0).utility)
                    e2_state["event"] = {
                        "authority_epoch_s": float(e2_state["epoch"]),
                        "challenge_time_s": float(e2_state["challenge_time"]),
                        "authority_age_s": float(e2_state["authority_age"]),
                        "expected_freshness_band": str(e2_state["expected_freshness"]),
                        "actual_freshness_band": freshness.value,
                        "proposer_id": e2_state["proposer"],
                        "target_auv": e2_state["target"],
                        "segment_id": e2_state["segment_id"],
                        "capsule_id": e2_state.get("capsule_id"),
                        "snapshot_timestamp": snap["captured_at"],
                        "last_authority_update_s": float(last_auth[aid]),
                        "local_target_failed": bool(snap["target_failed"]),
                        "local_segment_mandatory": bool(snap["segment_mandatory"]),
                        "local_segment_incomplete": bool(snap["segment_incomplete"]),
                        "local_segment_owner": snap["segment_owner"],
                        "local_snapshot_time": float(snap["captured_at"]),
                        "local_snapshot_age": float(env.now) - float(snap["captured_at"]),
                        "local_snapshot_version": int(snap["snapshot_version"]),
                        "global_target_failed": bool(global_failed),
                        "global_segment_mandatory": bool(gb.get("global_segment_mandatory", gseg.mandatory)),
                        "global_segment_incomplete": bool(
                            gb.get(
                                "global_segment_incomplete",
                                (not gseg.completed) and (not gseg.abandoned),
                            )
                        ),
                        "global_segment_owner": gb.get("global_segment_owner", owner_before),
                        "global_state_changed": bool(e2_state.get("change_done")),
                        "global_change_time": e2_state.get("ground_truth_change_time"),
                        "baseline": str(baseline),
                        "conditional_ok_local": bool(cond_ok),
                        "decision": dec,
                        "reason_code": result.reason_code.value,
                        "connectivity_state": conn.value,
                        "freshness_mode": (
                            "disabled"
                            if str(baseline) in ("B4-NoFreshness", "B4_NoFreshness", "B4_no_freshness")
                            else freshness_mode
                        ),
                        "mission_beneficial": beneficial,
                        "violates_frozen_risk": violates,
                        "oracle_reason": None if label is None else label.reason,
                        "executed": int(executed),
                        "controlled_reassignment_allowed": allowed,
                        "controlled_safe_useful_execution": safe_useful,
                        "obsolete_reassignment_execution": obsolete,
                        "controlled_false_denial": false_denial,
                        "ownership_override_of_available_target": int(override),
                        "useful_reassignment_delta": int(world.useful_reassignment_count) - ur_before,
                        "duplicate_work_delta": int(world.duplicate_work_count) - dup_before,
                        "contradictory_reassignment_delta": int(override),
                        "missed_mandatory_delta": miss_before
                        - sum(
                            1
                            for s in world.segments.values()
                            if s.mandatory and (not s.completed or s.abandoned)
                        ),
                        "utility_before": util_before,
                        "utility_after": util_after,
                        "owner_before": owner_before,
                        "owner_after": owner_after,
                        "lease_ttl_s": getattr(controller, "lease_ttl_s", None),
                        "lease_state": (
                            "EXPIRED"
                            if getattr(controller, "lease_ttl_s", None) is not None
                            and float(getattr(controller, "lease_ttl_s")) < float("inf")
                            and age_s >= float(controller.lease_ttl_s)
                            else (
                                "VALID"
                                if str(baseline) in ("B4-FixedExpiry", "B4_FixedExpiry", "B4_fixed_expiry")
                                else None
                            )
                        ),
                    }

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
                            "reason_code": result.reason_code.value,
                            "gw_reachable": gw_reach,
                            "outage": bool(outage_start <= env.now < outage_end),
                        }
                    )

                exec_params = []
                if result.decision == GovernorDecision.ALLOW:
                    exec_params = [{"action": atype, **dict(proposal.parameters)}]
                journals[aid].append(
                    NodeJournalEntry(
                        time=env.now,
                        node_id=aid,
                        actions_proposed=[atype],
                        actions_executed=[atype] if result.decision == GovernorDecision.ALLOW else [],
                        executed_params=exec_params,
                        capsule_id=capsules[aid].capsule_id,
                        energy_battery_j=auv.energy.battery_j,
                        task_ownership={s.segment_id: (s.owner or "") for s in world.segments.values()},
                        observations=dict(auv.local_observations),
                        state_version=auv.state_version,
                    )
                )
                if not auv.is_gateway:
                    dst = world.gateway_id if gw_reach else (neighbors[0] if neighbors else None)
                    if dst is not None:
                        net.send(
                            Packet(
                                packet_id=f"tel-{aid}-{env.now}",
                                src=aid,
                                dst=dst,
                                ptype=PacketType.DATA,
                                size_bytes=48,
                                payload={"kind": "telemetry", "seg": auv.assigned_segment},
                                created_at=env.now,
                            )
                        )
                    if dst is not None and dst != aid:
                        net.send(
                            Packet(
                                packet_id=f"coord-{aid}-{env.now}",
                                src=aid,
                                dst=dst,
                                ptype=PacketType.DATA,
                                size_bytes=32,
                                payload={"kind": "task_coord", "owner": auv.assigned_segment},
                                created_at=env.now,
                            )
                        )
                    unresolved = [k for k, a in world.anomalies.items() if not a.get("resolved")]
                    if unresolved and gw_reach:
                        net.send(
                            Packet(
                                packet_id=f"anom-{aid}-{env.now}",
                                src=aid,
                                dst=world.gateway_id,
                                ptype=PacketType.DATA,
                                size_bytes=40,
                                payload={"kind": "anomaly_report", "ids": unresolved[:3]},
                                created_at=env.now,
                            )
                        )

            yield env.timeout(tick)

    env.process(mission_loop())
    env.run(until=duration)
    if e2_on:
        if int(e2_state["n_controlled"]) != 1:
            raise RuntimeError(
                f"E2: expected exactly 1 controlled reassignment proposal, got {e2_state['n_controlled']}"
            )
        if e2_state.get("event") is None:
            raise RuntimeError("E2: controlled event was not logged")

    acc = net.total_bytes()
    air = net.total_airtime_s()
    governance_bytes = int(acc["governance_tx"])
    total_bytes = int(acc["tx"])
    mission_tx = int(acc.get("data_tx", total_bytes - governance_bytes))

    sem = detect_semantic_conflicts(
        journals,
        failed_ids=list(world.failed_auv_ids),
        duplicate_work_count=int(world.duplicate_work_count),
    )
    conflict_count = int(sem["semantic_conflicts_per_mission"])

    unauth_rate = high_risk_action_count / max(consequential_proposed, 1)
    false_denial_rate = false_denials / max(denied_consequential, 1)
    retention = useful_cons_allowed / max(useful_cons_proposed, 1)
    safe_retention = safe_useful_allowed / max(safe_useful_proposed, 1)
    n_subjects = max(len(capsules), 1)
    auv_reauth_fraction = len(reauth_ok) / n_subjects if recon_status != "idle" else None
    auv_timeout_fraction = len(recon_timeout_ids) / n_subjects if recon_status != "idle" else None

    energy_p = sum(a.energy.propulsion_j for a in world.auvs.values())
    energy_c = sum(a.energy.communication_j for a in world.auvs.values()) + 0.001 * total_bytes
    energy_k = sum(a.energy.compute_j for a in world.auvs.values())

    total_conn_ticks = max(sum(connectivity_ticks.values()), 1)
    conn_occupancy = {k: v / total_conn_ticks for k, v in connectivity_ticks.items()}

    ub = world_utility(world, unresolved_conflicts=conflict_count)
    ub_noc = world_utility(world, unresolved_conflicts=0)
    violation_per_proposal = hard_safety_allow / max(consequential_proposed, 1)
    violation_per_executed = hard_safety_allow / max(consequential_allowed, 1)
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
            "policy_version": policy_version,
            "tx_bytes": int(acc["tx"]),
            "rx_bytes": int(acc["rx"]),
            "governance_tx_bytes": int(acc["governance_tx"]),
            "mission_tx_bytes": mission_tx,
            "total_tx_bytes": int(total_bytes),
            "gco": compute_overhead(governance_bytes, total_bytes),
            "environment_role": "deterministic_scenario",
            "seed_role": "monte_carlo_phy_and_bernoulli",
            "disconnected_consequential_execution_rate": unauth_rate,
            "violation_per_proposal": violation_per_proposal,
            "violation_per_executed_consequential": violation_per_executed,
            "hard_safety_violation_count": hard_safety_allow,
            "semantic_conflicts_per_mission": conflict_count,
            "conflict_by_kind": sem["by_kind"],
            "conflict_events": sem["events"],
            "utility_without_conflict_penalty": ub_noc.utility,
            "reason_code_counts": reason_counts,
            "ablation_variant": (
                "b4_no_freshness_v1"
                if str(baseline) in ("B4-NoFreshness", "B4_NoFreshness", "B4_no_freshness")
                else (
                    "fixed_expiry_v1"
                    if str(baseline) in ("B4-FixedExpiry", "B4_FixedExpiry", "B4_fixed_expiry")
                    else None
                )
            ),
            "lease_ttl_s": getattr(controller, "lease_ttl_s", None)
            if str(baseline) in ("B4-FixedExpiry", "B4_FixedExpiry", "B4_fixed_expiry")
            else None,
            "useful_reassignment_count": int(world.useful_reassignment_count),
            "duplicate_work_count": int(world.duplicate_work_count),
            "missed_mandatory": int(ub.missed_mandatory),
            "contradictory_reassignment": int(sem["by_kind"].get("contradictory_reassignment", 0)),
            "consequential_decision_log": cons_decision_log,
            "recon_status": recon_status,
            "recon_latency_s": recon_latency,
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
            "safe_useful_retention_freeze_id": SAFE_USEFUL_RETENTION_FREEZE_ID,
            "safe_useful_proposed": safe_useful_proposed,
            "safe_useful_allowed": safe_useful_allowed,
            "safe_useful_retention": safe_retention,
            "auv_reauth_ok": sorted(reauth_ok),
            "auv_reauth_latency_s": reauth_latency,
            "auv_reauth_fraction": auv_reauth_fraction,
            "auv_timeout_ids": sorted(recon_timeout_ids),
            "auv_timeout_fraction": auv_timeout_fraction,
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
            "e2_scenario_id": "authority_age_context_v1" if e2_on else None,
            "e2_n_controlled_proposals": int(e2_state["n_controlled"]) if e2_on else 0,
            "e2_context_mode": e2_state.get("context") if e2_on else None,
            "e2_authority_epoch_s": e2_state.get("epoch") if e2_on else None,
            "e2_capsule_id": e2_state.get("capsule_id") if e2_on else None,
            "e2_controlled_event": e2_state.get("event") if e2_on else None,
            "governance_airtime_s": float(air["governance_airtime_s"]),
            "mission_airtime_s": float(air["mission_airtime_s"]),
            "total_airtime_s": float(air["total_airtime_s"]),
            "governance_airtime_frac": float(air["governance_airtime_s"]) / max(duration, 1e-9),
            "mission_airtime_frac": float(air["mission_airtime_s"]) / max(duration, 1e-9),
            "total_airtime_frac": float(air["total_airtime_s"]) / max(duration, 1e-9),
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
