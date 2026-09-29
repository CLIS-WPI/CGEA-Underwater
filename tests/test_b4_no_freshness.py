"""Freeze check: B4-NoFreshness disables age contraction, not the governor."""

from __future__ import annotations

from cgea.agent import ActionProposal, ExecutionAdapter, MissionPlanner
from cgea.baselines import make_baseline
from cgea.governance import ConnectivityState, GovernorDecision, ReasonCode, issue_capsule
from cgea.mission import ActionType, RiskClass, build_pipeline_mission


def _reassign(world, auv_id: str) -> ActionProposal:
    failed = world.failed_auv_ids[0] if world.failed_auv_ids else "auv_01"
    seg = world.auvs[failed].assigned_segment
    return ActionProposal(
        proposer_id=auv_id,
        action_type=ActionType.REASSIGN_ANOTHER_AUV,
        parameters={"target_auv": failed, "segment_id": seg},
        expected_energy_cost_j=10.0,
        risk_class=RiskClass.CONSEQUENTIAL,
        confidence=0.9,
        mission_state_version=0,
    )


def test_nofreshness_allows_aging_reassign_when_conditions_met():
    world = build_pipeline_mission(n_auvs=12, workload="stress")
    planner = MissionPlanner()
    adapter = ExecutionAdapter()
    b4 = make_baseline("B4", planner, adapter)
    nf = make_baseline("B4-NoFreshness", planner, adapter)
    assert getattr(nf, "ablation_variant", None) == "b4_no_freshness_v1"
    auv = world.auvs["auv_06"]
    cap = issue_capsule(auv.auv_id, world.mission_id, 0.0)
    energy = auv.energy
    proposal = _reassign(world, auv.auv_id)
    # Age 220 s → AGING; B4 strips reassign. NoFreshness keeps conditional path.
    r4 = b4.decide(
        proposal,
        ConnectivityState.PARTITIONED,
        cap,
        last_authority_update=0.0,
        now=220.0,
        energy=energy,
        supervisor_reachable=False,
        conditional_ok=True,
    )
    rnf = nf.decide(
        proposal,
        ConnectivityState.PARTITIONED,
        cap,
        last_authority_update=0.0,
        now=220.0,
        energy=energy,
        supervisor_reachable=False,
        conditional_ok=True,
    )
    assert r4.decision == GovernorDecision.DENY
    assert r4.reason_code == ReasonCode.DENY_FORBIDDEN
    assert rnf.decision == GovernorDecision.ALLOW
    assert rnf.reason_code == ReasonCode.ALLOW_AUTHORIZED


def test_nofreshness_still_denies_hard_safety():
    world = build_pipeline_mission(n_auvs=4, workload="stress")
    nf = make_baseline("B4-NoFreshness", MissionPlanner(), ExecutionAdapter())
    auv = [a for a in world.auvs.values() if not a.is_gateway][0]
    cap = issue_capsule(auv.auv_id, world.mission_id, 0.0)
    proposal = ActionProposal(
        proposer_id=auv.auv_id,
        action_type=ActionType.ENTER_EXCLUSION_ZONE,
        parameters={},
        expected_energy_cost_j=10.0,
        risk_class=RiskClass.CONSEQUENTIAL,
        confidence=0.9,
        mission_state_version=0,
    )
    r = nf.decide(
        proposal,
        ConnectivityState.CONNECTED,
        cap,
        last_authority_update=0.0,
        now=0.0,
        energy=auv.energy,
        supervisor_reachable=True,
        violates_frozen_risk=True,
    )
    assert r.decision == GovernorDecision.DENY
    assert r.reason_code == ReasonCode.DENY_FORBIDDEN
