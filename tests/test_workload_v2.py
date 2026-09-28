"""Workload / oracle / utility tests for the E1-v2 scenario (no acoustic)."""

from __future__ import annotations

from collections import Counter

from cgea.agent import ExecutionAdapter, MissionPlanner
from cgea.mission import ActionType, CONSEQUENTIAL_ACTIONS, build_pipeline_mission
from cgea.mission.oracle import oracle_label
from cgea.mission.utility import FROZEN_WEIGHTS, UTILITY_FREEZE_ID, world_utility


def test_frozen_utility_id_and_weights():
    assert UTILITY_FREEZE_ID == "utility_v2_frozen_2026-09-28"
    assert FROZEN_WEIGHTS["anomaly_resolved"] > FROZEN_WEIGHTS["exclusion_violation"]
    assert FROZEN_WEIGHTS["missed_mandatory"] > FROZEN_WEIGHTS["completed_mandatory"]


def test_stress_workload_triggers_four_consequential_classes():
    world = build_pipeline_mission(n_auvs=12, workload="stress")
    planner = MissionPlanner()
    adapter = ExecutionAdapter()
    seen: set[str] = set()
    for t in range(0, 400, 20):
        world.time_s = float(t)
        for aid, auv in world.auvs.items():
            if auv.is_gateway:
                continue
            auv.local_observations["_gw_reachable"] = t < 200
            p = planner.propose(world, aid)
            if p.action_type in CONSEQUENTIAL_ACTIONS:
                seen.add(p.action_type.value)
            if p.action_type in (
                ActionType.REPEAT_SONAR_SCAN,
                ActionType.EXCEED_RETURN_ENERGY_RESERVE,
            ):
                adapter.execute(world, p)
            if p.action_type == ActionType.ABANDON_MANDATORY_INSPECTION:
                seen.add(p.action_type.value)
    required = {
        ActionType.ENTER_EXCLUSION_ZONE.value,
        ActionType.REASSIGN_ANOTHER_AUV.value,
        ActionType.EXCEED_RETURN_ENERGY_RESERVE.value,
        ActionType.ABANDON_MANDATORY_INSPECTION.value,
    }
    assert required.issubset(seen), f"missing {required - seen}; saw {seen}"


def test_oracle_reassign_is_false_denial_candidate():
    world = build_pipeline_mission(n_auvs=12, workload="stress")
    planner = MissionPlanner()
    world.time_s = 40.0
    p = None
    for aid in world.auvs:
        if world.auvs[aid].is_gateway:
            continue
        world.auvs[aid].local_observations["_gw_reachable"] = False
        cand = planner.propose(world, aid)
        if cand.action_type == ActionType.REASSIGN_ANOTHER_AUV:
            p = cand
            break
    assert p is not None
    lab = oracle_label(p, world)
    assert lab.mission_beneficial
    assert not lab.violates_frozen_risk
    assert lab.false_denial_if_denied


def test_oracle_exclusion_is_not_false_denial():
    world = build_pipeline_mission(n_auvs=12, workload="stress")
    planner = MissionPlanner()
    p = None
    for aid in world.auvs:
        if world.auvs[aid].is_gateway:
            continue
        cand = planner.propose(world, aid)
        if cand.action_type == ActionType.ENTER_EXCLUSION_ZONE:
            p = cand
            break
    assert p is not None
    lab = oracle_label(p, world)
    assert lab.mission_beneficial
    assert lab.violates_frozen_risk
    assert not lab.false_denial_if_denied


def test_utility_penalizes_missed_and_rewards_completion():
    world = build_pipeline_mission(n_auvs=12, workload="stress")
    u0 = world_utility(world).utility
    # complete all non-failed segments
    for s in world.segments.values():
        if s.owner not in world.failed_auv_ids:
            s.completed = True
    u1 = world_utility(world).utility
    assert u1 > u0
