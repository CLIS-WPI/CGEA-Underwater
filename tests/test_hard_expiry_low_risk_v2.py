"""E6: HARD_EXPIRED LOW_RISK preservation (v2) vs historical fallback-only (v1)."""

from __future__ import annotations

from cgea.agent import ActionProposal
from cgea.baselines import make_baseline
from cgea.governance import (
    AuthorityFreshness,
    ConnectivityState,
    FreshnessPolicy,
    GovernorDecision,
    PAPER_POLICY_VERSION,
    PAPER_POLICY_VERSION_V2,
    ReasonCode,
    contract_capsule,
    freshness_policy_for_version,
    issue_capsule,
)
from cgea.mission import LOW_RISK_ACTIONS, ActionType, RiskClass, build_pipeline_mission


def _prop(auv_id: str, action: ActionType, risk: RiskClass) -> ActionProposal:
    return ActionProposal(
        proposer_id=auv_id,
        action_type=action,
        parameters={"target_auv": "auv_01", "segment_id": "seg_00"},
        expected_energy_cost_j=1.0,
        risk_class=risk,
        confidence=1.0,
        mission_state_version=0,
        rationale="e6_unit",
    )


def _decide(policy: FreshnessPolicy, action: ActionType, risk: RiskClass, now: float = 960.0):
    from cgea.agent import ExecutionAdapter, MissionPlanner

    world = build_pipeline_mission(n_auvs=2)
    b4 = make_baseline("B4", MissionPlanner(), ExecutionAdapter())
    b4.governor.freshness_policy = policy
    auv = [a for a in world.auvs.values() if not a.is_gateway][0]
    cap = issue_capsule(auv.auv_id, world.mission_id, 0.0)
    return b4.decide(
        _prop(auv.auv_id, action, risk),
        ConnectivityState.CONNECTED,
        cap,
        last_authority_update=0.0,
        now=now,
        energy=auv.energy,
        supervisor_reachable=False,
        position={"x": 0.0, "y": 0.0},
    )


def test_t1_t6_v2_low_risk_allow_at_hard_expiry():
    pol = freshness_policy_for_version(PAPER_POLICY_VERSION_V2)
    cases = [
        ActionType.COLLISION_AVOIDANCE,
        ActionType.BOUNDED_PATH_CORRECTION,
        ActionType.HOLD_STATION,
        ActionType.REPEAT_SONAR_SCAN,
        ActionType.LOCAL_INFERENCE,
        ActionType.SURFACING_SAFE_MODE,
    ]
    for act in cases:
        r = _decide(pol, act, RiskClass.LOW, now=960.0)
        assert r.decision == GovernorDecision.ALLOW, act
        assert r.reason_code == ReasonCode.ALLOW_LOW_RISK, (act, r.reason_code)


def test_t7_reassign_still_deny_hard_expiry_v2():
    pol = freshness_policy_for_version(PAPER_POLICY_VERSION_V2)
    r = _decide(pol, ActionType.REASSIGN_ANOTHER_AUV, RiskClass.CONSEQUENTIAL, now=960.0)
    assert r.decision == GovernorDecision.DENY
    assert r.reason_code == ReasonCode.DENY_HARD_EXPIRY


def test_t8_t9_static_hard_safety_still_denied_v2():
    pol = freshness_policy_for_version(PAPER_POLICY_VERSION_V2)
    for act in (
        ActionType.ENTER_EXCLUSION_ZONE,
        ActionType.EXCEED_RETURN_ENERGY_RESERVE,
    ):
        r = _decide(pol, act, RiskClass.CONSEQUENTIAL, now=960.0)
        assert r.decision == GovernorDecision.DENY, act
        assert r.reason_code == ReasonCode.DENY_HARD_EXPIRY, (act, r.reason_code)


def test_t10_v1_fallback_only_reproduces_historical_contraction():
    pol = freshness_policy_for_version(PAPER_POLICY_VERSION)
    assert pol.hard_expiry_keep_low_risk is False
    r = _decide(pol, ActionType.COLLISION_AVOIDANCE, RiskClass.LOW, now=960.0)
    assert r.decision == GovernorDecision.DENY
    assert r.reason_code == ReasonCode.DENY_FORBIDDEN
    r2 = _decide(pol, ActionType.BOUNDED_PATH_CORRECTION, RiskClass.LOW, now=960.0)
    assert r2.reason_code == ReasonCode.DENY_FORBIDDEN
    r3 = _decide(pol, ActionType.REASSIGN_ANOTHER_AUV, RiskClass.CONSEQUENTIAL, now=960.0)
    assert r3.reason_code == ReasonCode.DENY_HARD_EXPIRY


def test_v1_v2_identical_fresh_aging_stale():
    cap = issue_capsule("auv_00", "m", 0.0)
    v1 = freshness_policy_for_version(PAPER_POLICY_VERSION)
    v2 = freshness_policy_for_version(PAPER_POLICY_VERSION_V2)
    for fr, now in (
        (AuthorityFreshness.FRESH, 10.0),
        (AuthorityFreshness.AGING, 200.0),
        (AuthorityFreshness.STALE, 500.0),
    ):
        a = contract_capsule(cap, fr, v1, now)
        b = contract_capsule(cap, fr, v2, now)
        assert a.allowed_actions == b.allowed_actions, fr
        assert a.conditional_actions == b.conditional_actions, fr
        assert set(a.forbidden_actions) == set(b.forbidden_actions), fr
        assert a.risk_ceiling == b.risk_ceiling, fr


def test_v2_hard_expired_sets_keep_low_risk_not_consequential():
    cap = issue_capsule("auv_00", "m", 0.0)
    v2 = freshness_policy_for_version(PAPER_POLICY_VERSION_V2)
    eff = contract_capsule(cap, AuthorityFreshness.HARD_EXPIRED, v2, 960.0)
    for a in LOW_RISK_ACTIONS:
        assert a.value in eff.allowed_actions
    assert cap.fallback_action in eff.allowed_actions
    assert ActionType.REASSIGN_ANOTHER_AUV.value in eff.forbidden_actions
    assert ActionType.ENTER_EXCLUSION_ZONE.value in eff.forbidden_actions
    assert not eff.conditional_actions
    assert ActionType.COLLISION_AVOIDANCE.value not in eff.forbidden_actions
