"""E3 fixed-expiry lease vs CGEA. Does not retune B4."""

from __future__ import annotations

from cgea.agent import ActionProposal, ExecutionAdapter, MissionPlanner
from cgea.baselines import make_baseline
from cgea.experiments.e2_authority_age import AUTHORITY_EPOCH_S
from cgea.experiments.e3_fixed_expiry import CGEA_AGE_180_IS, cgea_expected_freshness, context_status
from cgea.governance import ConnectivityState, GovernorDecision, ReasonCode, classify_freshness, issue_capsule
from cgea.governance import FreshnessPolicy
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


def test_cgea_age_180_is_aging_unchanged():
    assert CGEA_AGE_180_IS == "aging"
    pol = FreshnessPolicy()
    cap = issue_capsule("auv_06", "m", 180.0)
    assert classify_freshness(180.0, cap, pol).value == "aging"
    assert cgea_expected_freshness(180.0) == "aging"
    assert cgea_expected_freshness(120.0) == "fresh"


def test_ttl_infinity_matches_nofreshness_on_aging_reassign():
    world = build_pipeline_mission(n_auvs=12, workload="stress")
    planner, adapter = MissionPlanner(), ExecutionAdapter()
    nf = make_baseline("B4-NoFreshness", planner, adapter)
    fe = make_baseline("B4-FixedExpiry", planner, adapter)
    fe.lease_ttl_s = float("inf")
    auv = world.auvs["auv_06"]
    cap = issue_capsule(auv.auv_id, world.mission_id, 0.0)
    proposal = _reassign(world, auv.auv_id)
    kwargs = dict(
        connectivity=ConnectivityState.PARTITIONED,
        capsule=cap,
        last_authority_update=0.0,
        now=220.0,
        energy=auv.energy,
        supervisor_reachable=False,
        conditional_ok=True,
    )
    rnf = nf.decide(proposal, **kwargs)
    rfe = fe.decide(proposal, **kwargs)
    assert rnf.decision == rfe.decision == GovernorDecision.ALLOW
    assert rnf.reason_code == rfe.reason_code


def test_ttl_180_denies_reassign_at_and_after_ttl():
    world = build_pipeline_mission(n_auvs=12, workload="stress")
    fe = make_baseline("B4-FixedExpiry", MissionPlanner(), ExecutionAdapter())
    fe.lease_ttl_s = 180.0
    auv = world.auvs["auv_06"]
    cap = issue_capsule(auv.auv_id, world.mission_id, AUTHORITY_EPOCH_S)
    proposal = _reassign(world, auv.auv_id)
    r120 = fe.decide(
        proposal,
        ConnectivityState.PARTITIONED,
        cap,
        AUTHORITY_EPOCH_S,
        AUTHORITY_EPOCH_S + 120.0,
        auv.energy,
        supervisor_reachable=False,
        conditional_ok=True,
    )
    r180 = fe.decide(
        proposal,
        ConnectivityState.PARTITIONED,
        cap,
        AUTHORITY_EPOCH_S,
        AUTHORITY_EPOCH_S + 180.0,
        auv.energy,
        supervisor_reachable=False,
        conditional_ok=True,
    )
    assert r120.decision == GovernorDecision.ALLOW
    assert r180.decision == GovernorDecision.DENY
    assert r180.reason_code == ReasonCode.DENY_LEASE_EXPIRED


def test_fixed_expiry_still_denies_hard_safety():
    world = build_pipeline_mission(n_auvs=4, workload="stress")
    fe = make_baseline("B4-FixedExpiry", MissionPlanner(), ExecutionAdapter())
    fe.lease_ttl_s = 900.0
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
    r = fe.decide(
        proposal,
        ConnectivityState.CONNECTED,
        cap,
        0.0,
        10.0,
        auv.energy,
        supervisor_reachable=True,
        violates_frozen_risk=True,
    )
    assert r.decision == GovernorDecision.DENY
    assert r.reason_code == ReasonCode.DENY_FORBIDDEN


def test_equality_cells_excluded():
    assert context_status("recover_age_300", 300.0) == "equal"
    assert context_status("no_change", 120.0) == "valid"
    assert context_status("recover_age_150", 120.0) == "valid"
    assert context_status("recover_age_60", 120.0) == "obsolete"
