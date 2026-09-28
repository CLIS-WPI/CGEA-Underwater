"""M5/M7: governor bypass impossible; RECOVERING blocks consequential."""

from __future__ import annotations

from cgea.agent import ActionProposal, ExecutionAdapter, MissionPlanner
from cgea.baselines import make_baseline
from cgea.governance import (
    ConnectivityState,
    GovernorDecision,
    ReasonCode,
    issue_capsule,
)
from cgea.mission import ActionType, RiskClass, build_pipeline_mission, EnergyState


def test_agent_cannot_bypass_governor():
    """Architecture: proposal → governor → adapter. Direct adapter use is separate;
    baselines must call governor/controller before execute."""
    world = build_pipeline_mission(n_auvs=2)
    planner = MissionPlanner()
    adapter = ExecutionAdapter()
    b4 = make_baseline("B4", planner, adapter)
    auv = [a for a in world.auvs.values() if not a.is_gateway][0]
    cap = issue_capsule(auv.auv_id, world.mission_id, 0.0)
    # Forbidden consequential under hard-expired capsule
    cap.hard_expiry = -1.0
    proposal = ActionProposal(
        proposer_id=auv.auv_id,
        action_type=ActionType.CHANGE_HIGH_LEVEL_OBJECTIVE,
        parameters={},
        expected_energy_cost_j=10.0,
        risk_class=RiskClass.CONSEQUENTIAL,
        confidence=0.9,
        mission_state_version=0,
    )
    result = b4.decide(
        proposal,
        ConnectivityState.CONNECTED,
        cap,
        last_authority_update=0.0,
        now=1000.0,
        energy=auv.energy,
        supervisor_reachable=True,
    )
    assert result.decision == GovernorDecision.DENY
    assert result.reason_code is not None


def test_recovering_blocks_consequential_b4():
    world = build_pipeline_mission(n_auvs=2)
    planner = MissionPlanner()
    adapter = ExecutionAdapter()
    b4 = make_baseline("B4", planner, adapter)
    auv = [a for a in world.auvs.values() if not a.is_gateway][0]
    cap = issue_capsule(auv.auv_id, world.mission_id, 0.0, broad=True)
    proposal = ActionProposal(
        proposer_id=auv.auv_id,
        action_type=ActionType.REASSIGN_ANOTHER_AUV,
        parameters={"target_auv": "auv_01", "segment_id": "seg_01"},
        expected_energy_cost_j=10.0,
        risk_class=RiskClass.CONSEQUENTIAL,
        confidence=0.9,
        mission_state_version=0,
    )
    result = b4.decide(
        proposal,
        ConnectivityState.RECOVERING,
        cap,
        last_authority_update=0.0,
        now=10.0,
        energy=auv.energy,
        supervisor_reachable=True,
        immediate_resume=False,
    )
    assert result.decision == GovernorDecision.DENY
    assert result.reason_code == ReasonCode.DENY_RECOVERING_CONSEQUENTIAL


def test_low_risk_allowed_during_recovering():
    world = build_pipeline_mission(n_auvs=2)
    planner = MissionPlanner()
    adapter = ExecutionAdapter()
    b4 = make_baseline("B4", planner, adapter)
    auv = [a for a in world.auvs.values() if not a.is_gateway][0]
    cap = issue_capsule(auv.auv_id, world.mission_id, 0.0, broad=True)
    proposal = ActionProposal(
        proposer_id=auv.auv_id,
        action_type=ActionType.REPEAT_SONAR_SCAN,
        parameters={"segment_id": auv.assigned_segment},
        expected_energy_cost_j=10.0,
        risk_class=RiskClass.LOW,
        confidence=0.95,
        mission_state_version=0,
    )
    result = b4.decide(
        proposal,
        ConnectivityState.RECOVERING,
        cap,
        last_authority_update=0.0,
        now=10.0,
        energy=auv.energy,
        supervisor_reachable=False,
    )
    assert result.decision == GovernorDecision.ALLOW
