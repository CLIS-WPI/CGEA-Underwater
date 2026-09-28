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
    ConnectivityClassifier,
    ConnectivityMetrics,
    ConnectivityState,
    Digest,
    FreshnessPolicy,
    GovernorDecision,
    NodeJournalEntry,
    issue_capsule,
    make_digest,
    reconcile_partitions,
)
from cgea.metrics import RunMetrics, compute_overhead, save_metrics
from cgea.mission import RiskClass, build_pipeline_mission
from cgea.network import Packet, PacketType, UnderwaterNetwork
from cgea.types import Position3D, Provenance, config_hash, git_commit


GOVERNANCE_PACKET_TYPES = {
    PacketType.AUTHORITY,
    PacketType.DIGEST,
    PacketType.PROVENANCE,
    PacketType.RECONCILE,
    PacketType.SUPERVISOR,
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
    """Generate or load the shared ChannelTrace. Never regenerate per baseline."""
    store = ChannelTraceStore(Path(cfg.paths.traces))
    engine = BellhopEngine(_env_from_cfg(cfg), prefer_aubellhop=bool(cfg.acoustic.get("prefer_aubellhop", True)))
    duration = float(cfg.mission.duration_s)
    dt = float(cfg.mission.channel_sample_dt_s)
    times = list(np.arange(0.0, duration + 1e-9, dt))
    # Trace ID independent of baseline
    from cgea.acoustic.trace import make_trace_id

    trace_id = make_trace_id(engine.env.environment_id, int(cfg.seed), len(world_positions), duration)
    meta = store.meta_path(trace_id)
    if meta.exists() and not bool(cfg.get("force_regenerate_trace", False)):
        return store.load(trace_id)

    trace = generate_mission_trace(
        engine,
        world_positions,
        times,
        seed=int(cfg.seed),
        noise_psd_dbm_hz=float(cfg.acoustic.noise_psd_dbm_hz),
        bandwidth_hz=float(cfg.acoustic.bandwidth_hz),
        tx_power_dbm=float(cfg.acoustic.tx_power_dbm),
    )
    # Force stable ID
    object.__setattr__(trace, "trace_id", trace_id) if False else None
    trace.trace_id = trace_id
    store.save(trace)
    return trace


def run_single(cfg: DictConfig, baseline: str) -> RunMetrics:
    seed = int(cfg.seed)
    np.random.seed(seed)

    world = build_pipeline_mission(
        n_auvs=int(cfg.mission.n_auvs),
        pipeline_length_m=float(cfg.mission.pipeline_length_m),
        depth_m=float(cfg.mission.depth_m),
        battery_j=float(cfg.mission.battery_j),
        reserve_j=float(cfg.mission.reserve_j),
        mission_id=str(cfg.mission.mission_id),
    )
    positions = {aid: a.position for aid, a in world.auvs.items()}
    trace = ensure_trace(cfg, positions)

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

    # Outage / partition schedule from config
    outage_start = float(cfg.scenario.get("outage_start_s", 1e18))
    outage_end = float(cfg.scenario.get("outage_end_s", 1e18))
    partition_groups = cfg.scenario.get("partition_groups", None)

    governance_bytes = 0
    decisions_allow = 0
    decisions_deny = 0
    consequential_proposed = 0
    unauthorized_high_risk = 0
    false_denials = 0  # low-risk denied
    useful_allowed = 0
    useful_proposed = 0
    recovering = False
    recon_latency = 0.0
    conflict_count = 0
    recon_done = False

    tick = float(cfg.mission.sim_tick_s)
    duration = float(cfg.mission.duration_s)

    def mission_loop():
        nonlocal governance_bytes, decisions_allow, decisions_deny
        nonlocal consequential_proposed, unauthorized_high_risk, false_denials
        nonlocal useful_allowed, useful_proposed, recovering
        nonlocal recon_done, recon_latency, conflict_count

        while env.now < duration:
            t = env.now
            # Outage / partition schedule
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
                if gw_reach:
                    last_supervisor[aid] = env.now
                    # Authority refresh when connected
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
                        governance_bytes += pkt.size_bytes

                # Connectivity metrics (history-based)
                part_size = next((len(p) for p in parts if aid in p), 1)
                # Simple PDR proxy from recent deliveries
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

                proposal = planner.propose(world, aid)
                if proposal.risk_class == RiskClass.CONSEQUENTIAL:
                    consequential_proposed += 1
                if proposal.risk_class == RiskClass.LOW:
                    useful_proposed += 1

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

                if result.decision == GovernorDecision.ALLOW:
                    decisions_allow += 1
                    adapter.execute(world, proposal)
                    if proposal.risk_class == RiskClass.LOW:
                        useful_allowed += 1
                    # Unauthorized high-risk: consequential allowed without valid capsule under B4 rules
                    # Metric: consequential executed while isolated/partitioned under regimes that should block
                    if proposal.risk_class == RiskClass.CONSEQUENTIAL:
                        if baseline == "B4" and conn in (
                            ConnectivityState.ISOLATED,
                            ConnectivityState.RECOVERING,
                        ):
                            # Should have been blocked — count if somehow allowed
                            unauthorized_high_risk += 1
                        if baseline in ("B2", "B5") and conn in (
                            ConnectivityState.PARTITIONED,
                            ConnectivityState.ISOLATED,
                        ):
                            unauthorized_high_risk += 1
                        if baseline == "B1" and not gw_reach:
                            unauthorized_high_risk += 1
                else:
                    decisions_deny += 1
                    if proposal.risk_class == RiskClass.LOW:
                        false_denials += 1

                journals[aid].append(
                    NodeJournalEntry(
                        time=env.now,
                        node_id=aid,
                        actions_proposed=[proposal.action_type.value],
                        actions_executed=[proposal.action_type.value]
                        if result.decision == GovernorDecision.ALLOW
                        else [],
                        capsule_id=capsules[aid].capsule_id if baseline == "B4" else None,
                        energy_battery_j=auv.energy.battery_j,
                        task_ownership={s.segment_id: (s.owner or "") for s in world.segments.values()},
                        observations=dict(auv.local_observations),
                        state_version=auv.state_version,
                    )
                )

            yield env.timeout(tick)

    env.process(mission_loop())
    env.run(until=duration)

    total_bytes = net.total_bytes()["total"] + governance_bytes
    # Count governance from packet types
    for d in net.deliveries:
        if d.packet.ptype in GOVERNANCE_PACKET_TYPES:
            governance_bytes += d.packet.size_bytes

    unauth_rate = unauthorized_high_risk / max(consequential_proposed, 1)
    false_denial_rate = false_denials / max(useful_proposed, 1)
    retention = useful_allowed / max(useful_proposed, 1)

    energy_p = sum(a.energy.propulsion_j for a in world.auvs.values())
    energy_c = sum(a.energy.communication_j for a in world.auvs.values()) + 0.001 * total_bytes
    energy_k = sum(a.energy.compute_j for a in world.auvs.values())

    prov = Provenance(
        git_commit=git_commit(Path(cfg.paths.root)),
        configuration_hash=config_hash(cfg),
        random_seed=seed,
        baseline=baseline,
        channel_trace_id=trace.trace_id,
        environment_id=str(cfg.acoustic.environment_id),
    )
    metrics = RunMetrics(
        mission_completion_ratio=world.completion_ratio(),
        mission_utility=world.mission_utility(),
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
            "decisions_allow": decisions_allow,
            "decisions_deny": decisions_deny,
            "consequential_proposed": consequential_proposed,
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
