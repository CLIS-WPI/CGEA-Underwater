"""E2 pre-production sanity. Does not retune thresholds or touch E1 results."""

from __future__ import annotations

from pathlib import Path

from cgea.agent import ActionProposal
from cgea.baselines import make_baseline
from cgea.experiments.e2_authority_age import (
    AGE_CONDITIONS,
    AUTHORITY_EPOCH_S,
    CHANGE_AGE_S,
    capture_snapshot,
    expected_freshness_band,
    fail_target,
    local_conditional_ok,
    recover_target,
    resolve_target_segment,
    unfail_stress_targets,
    validate_freshness_table,
)
from cgea.governance import ConnectivityState, GovernorDecision, ReasonCode, issue_capsule
from cgea.mission import ActionType, RiskClass, build_pipeline_mission
from cgea.mission.oracle import oracle_label


def test_freshness_table_matches_predeclared_ages():
    validate_freshness_table()
    issued = AUTHORITY_EPOCH_S
    hard_h = 900.0
    assert expected_freshness_band(120.0, 300.0, issued, hard_h) == "fresh"
    assert expected_freshness_band(240.0, 420.0, issued, hard_h) == "aging"
    assert expected_freshness_band(520.0, 700.0, issued, hard_h) == "stale"
    assert expected_freshness_band(960.0, 1140.0, issued, hard_h) == "hard_expired"


def test_recovery_time_is_not_on_a_freshness_threshold():
    t_change = AUTHORITY_EPOCH_S + CHANGE_AGE_S
    assert t_change == 480.0
    assert CHANGE_AGE_S not in (180.0, 400.0, 900.0)
    for spec in AGE_CONDITIONS.values():
        assert spec["authority_age_s"] not in (180.0, 400.0, 900.0)


def test_local_snapshot_does_not_follow_global_recovery():
    world = build_pipeline_mission(n_auvs=12, workload="stress")
    unfail_stress_targets(world, keep="")
    target = "auv_05"
    proposer = "auv_06"
    seg = resolve_target_segment(world, target)
    fail_target(world, target)
    snap = capture_snapshot(
        world, proposer_id=proposer, target_auv=target, segment_id=seg, t=AUTHORITY_EPOCH_S
    )
    assert snap["target_failed"] is True
    recover_target(world, target)
    assert target not in world.failed_auv_ids
    assert world.auvs[target].failed is False
    assert snap["target_failed"] is True
    assert local_conditional_ok(snap, 0, 1) is True
    proposal = ActionProposal(
        proposer_id=proposer,
        action_type=ActionType.REASSIGN_ANOTHER_AUV,
        parameters={"target_auv": target, "segment_id": seg},
        expected_energy_cost_j=1.0,
        risk_class=RiskClass.CONSEQUENTIAL,
        confidence=0.7,
        mission_state_version=0,
    )
    label = oracle_label(proposal, world)
    assert label.mission_beneficial is False
    assert label.violates_frozen_risk is False


def test_oracle_beneficial_before_and_after_recovery():
    world = build_pipeline_mission(n_auvs=12, workload="stress")
    unfail_stress_targets(world, keep="")
    target = "auv_05"
    proposer = "auv_06"
    seg = resolve_target_segment(world, target)
    fail_target(world, target)
    proposal = ActionProposal(
        proposer_id=proposer,
        action_type=ActionType.REASSIGN_ANOTHER_AUV,
        parameters={"target_auv": target, "segment_id": seg},
        expected_energy_cost_j=1.0,
        risk_class=RiskClass.CONSEQUENTIAL,
        confidence=0.7,
        mission_state_version=0,
    )
    before = oracle_label(proposal, world)
    assert before.mission_beneficial is True
    assert before.violates_frozen_risk is False
    recover_target(world, target)
    after = oracle_label(proposal, world)
    assert after.mission_beneficial is False
    assert after.violates_frozen_risk is False


def test_b4_and_nofreshness_same_governor_path_except_freshness_mode():
    world = build_pipeline_mission(n_auvs=12, workload="stress")
    from cgea.agent import ExecutionAdapter, MissionPlanner

    planner = MissionPlanner()
    adapter = ExecutionAdapter()
    b4 = make_baseline("B4", planner, adapter)
    nf = make_baseline("B4-NoFreshness", planner, adapter)
    auv = world.auvs["auv_06"]
    cap = issue_capsule(auv.auv_id, world.mission_id, 180.0)
    proposal = ActionProposal(
        proposer_id=auv.auv_id,
        action_type=ActionType.REASSIGN_ANOTHER_AUV,
        parameters={"target_auv": "auv_05", "segment_id": auv.assigned_segment},
        expected_energy_cost_j=1.0,
        risk_class=RiskClass.CONSEQUENTIAL,
        confidence=0.7,
        mission_state_version=0,
    )
    kwargs = dict(
        connectivity=ConnectivityState.PARTITIONED,
        capsule=cap,
        last_authority_update=180.0,
        now=420.0,
        energy=auv.energy,
        supervisor_reachable=False,
        conditional_ok=True,
    )
    r4 = b4.decide(proposal, **kwargs)
    rnf = nf.decide(proposal, **kwargs)
    assert r4.decision == GovernorDecision.DENY
    assert r4.reason_code == ReasonCode.DENY_FORBIDDEN
    assert rnf.decision == GovernorDecision.ALLOW


def test_e1_results_untouched():
    root = Path(__file__).resolve().parents[1]
    p = root / "results" / "e1_production_n10" / "production_manifest.md"
    assert p.is_file()
    text = p.read_text()
    assert "n_runs: 1260" in text
    assert "paper_ssp_200m_v1" in text
