"""B3 evidence authority unit tests T1–T16 (no E4 DEV/TEST campaign)."""

from __future__ import annotations

import inspect

from cgea.agent import ActionProposal, ExecutionAdapter, MissionPlanner
from cgea.baselines import make_baseline
from cgea.governance import ConnectivityState, GovernorDecision, ReasonCode, issue_capsule
from cgea.governance.evidence import (
    EvidenceRecord,
    EvidenceStore,
    EvidenceType,
    apply_trusted_remote_update,
    evaluate_requirements,
    load_action_evidence_policy,
    populate_from_trusted_snapshot,
    put_local_self,
    refresh_known_remote_from_supervisor_view,
)
from cgea.mission import ActionType, EnergyState, RiskClass, build_pipeline_mission
from cgea.experiments.e2_authority_age import (
    AUTHORITY_EPOCH_S,
    capture_snapshot,
    fail_target,
    recover_target,
    resolve_target_segment,
    unfail_stress_targets,
)


POLICY = load_action_evidence_policy()


def _energy() -> EnergyState:
    return EnergyState(battery_j=1e6, reserve_j=1e5)


def _pos() -> dict:
    class P:
        x, y, z = 0.0, 0.0, 80.0

    return P()


def _reassign(proposer="auv_06", target="auv_05", segment="seg_x") -> ActionProposal:
    return ActionProposal(
        proposer_id=proposer,
        action_type=ActionType.REASSIGN_ANOTHER_AUV,
        parameters={"target_auv": target, "segment_id": segment},
        expected_energy_cost_j=10.0,
        risk_class=RiskClass.CONSEQUENTIAL,
        confidence=0.9,
        mission_state_version=0,
    )


def _local_action(action: ActionType, proposer="auv_06") -> ActionProposal:
    return ActionProposal(
        proposer_id=proposer,
        action_type=action,
        parameters={},
        expected_energy_cost_j=1.0,
        risk_class=RiskClass.LOW,
        confidence=0.9,
        mission_state_version=0,
    )


def _seed_local(store: EvidenceStore, proposer="auv_06", trusted_at=180.0, version=1) -> None:
    put_local_self(
        store,
        auv_id=proposer,
        now=trusted_at,
        energy=_energy(),
        position=_pos(),
        observations={"sonar": True},
        version=version,
    )


def _seed_reassign(
    store: EvidenceStore,
    *,
    trusted_at: float,
    peer_failed: bool = True,
    mandatory: bool = True,
    incomplete: bool = True,
    target="auv_05",
    segment="seg_x",
    version: int = 1,
) -> None:
    apply_trusted_remote_update(
        store,
        target_auv=target,
        segment_id=segment,
        peer_failed=peer_failed,
        segment_mandatory=mandatory,
        segment_incomplete=incomplete,
        segment_owner=target,
        trusted_at=trusted_at,
        version=version,
        source="test",
    )


def _b3_decide(store, proposal, now, issued_at=180.0, conditional_ok=True):
    ctrl = make_baseline("B4-Evidence", MissionPlanner(), ExecutionAdapter())
    cap = issue_capsule(proposal.proposer_id, "m", issued_at)
    return ctrl.decide(
        proposal,
        ConnectivityState.PARTITIONED,
        cap,
        last_authority_update=issued_at,
        now=now,
        energy=_energy(),
        supervisor_reachable=False,
        position={"x": 0.0, "y": 0.0},
        conditional_ok=conditional_ok,
        evidence_store=store,
        evidence_policy=POLICY,
    )


def test_t1_fresh_local_nav_energy_passes_evidence_stage():
    store = EvidenceStore()
    _seed_local(store, trusted_at=180.0)
    now = 200.0  # nav age 20 <= 40; energy 20 <= 60
    ev = evaluate_requirements(_local_action(ActionType.COLLISION_AVOIDANCE), store, now, POLICY)
    assert ev.passed
    ev2 = evaluate_requirements(_local_action(ActionType.BOUNDED_PATH_CORRECTION), store, now, POLICY)
    assert ev2.passed
    r = _b3_decide(store, _local_action(ActionType.COLLISION_AVOIDANCE), now)
    assert r.decision == GovernorDecision.ALLOW


def test_t2_stale_peer_denies_reassignment():
    store = EvidenceStore()
    _seed_reassign(store, trusted_at=180.0)
    r = _b3_decide(store, _reassign(), now=180.0 + 301.0)
    assert r.decision == GovernorDecision.DENY
    assert r.reason_code == ReasonCode.DENY_STALE_EVIDENCE


def test_t3_fresh_peer_and_assignment_passes():
    store = EvidenceStore()
    _seed_reassign(store, trusted_at=180.0)
    ev = evaluate_requirements(_reassign(), store, 180.0 + 220.0, POLICY)
    assert ev.passed
    r = _b3_decide(store, _reassign(), now=180.0 + 220.0)
    assert r.decision == GovernorDecision.ALLOW
    assert r.reason_code == ReasonCode.ALLOW_AUTHORIZED


def test_t4_missing_peer_denies():
    store = EvidenceStore()
    apply_trusted_remote_update(
        store,
        target_auv="auv_05",
        segment_id="seg_x",
        peer_failed=True,
        segment_mandatory=True,
        segment_incomplete=True,
        segment_owner="auv_05",
        trusted_at=180.0,
        version=1,
    )
    store.invalidate(EvidenceType.PEER_AVAILABILITY, "auv_05", "missing")
    r = _b3_decide(store, _reassign(), now=200.0)
    assert r.reason_code == ReasonCode.DENY_MISSING_EVIDENCE


def test_t5_peer_failed_false_invalid():
    store = EvidenceStore()
    _seed_reassign(store, trusted_at=180.0, peer_failed=False)
    r = _b3_decide(store, _reassign(), now=200.0)
    assert r.reason_code == ReasonCode.DENY_INVALID_EVIDENCE


def test_t6_assignment_complete_invalid():
    store = EvidenceStore()
    _seed_reassign(store, trusted_at=180.0, incomplete=False)
    r = _b3_decide(store, _reassign(), now=200.0)
    assert r.reason_code == ReasonCode.DENY_INVALID_EVIDENCE


def test_t7_exclusion_fresh_evidence_still_forbidden():
    store = EvidenceStore()
    _seed_local(store, trusted_at=180.0)
    _seed_reassign(store, trusted_at=180.0)
    proposal = ActionProposal(
        proposer_id="auv_06",
        action_type=ActionType.ENTER_EXCLUSION_ZONE,
        parameters={},
        expected_energy_cost_j=10.0,
        risk_class=RiskClass.CONSEQUENTIAL,
        confidence=0.9,
        mission_state_version=0,
    )
    r = _b3_decide(store, proposal, now=200.0)
    assert r.decision == GovernorDecision.DENY
    assert r.reason_code == ReasonCode.DENY_FORBIDDEN


def test_t8_peer_stale_assignment_fresh():
    store = EvidenceStore()
    store.put(
        EvidenceRecord(
            evidence_type=EvidenceType.PEER_AVAILABILITY,
            value={"failed": True},
            source="t",
            observed_at=180.0,
            trusted_at=180.0,
            object_id="auv_05",
            version=1,
        )
    )
    store.put(
        EvidenceRecord(
            evidence_type=EvidenceType.SEGMENT_ASSIGNMENT,
            value={"mandatory": True, "incomplete": True, "owner": "auv_05"},
            source="t",
            observed_at=180.0,
            trusted_at=180.0,
            object_id="seg_x",
            version=1,
        )
    )
    # now=180+301: peer 301 stale; assignment 301 <= 600
    r = _b3_decide(store, _reassign(), now=481.0)
    assert r.reason_code == ReasonCode.DENY_STALE_EVIDENCE
    ages = (r.evidence_evaluation or {}).get("evidence_ages", {})
    assert any(v > 300 for v in ages.values())


def test_t9_assignment_stale_peer_fresh():
    store = EvidenceStore()
    store.put(
        EvidenceRecord(
            evidence_type=EvidenceType.PEER_AVAILABILITY,
            value={"failed": True},
            source="t",
            observed_at=501.0,
            trusted_at=501.0,
            object_id="auv_05",
            version=2,
        )
    )
    store.put(
        EvidenceRecord(
            evidence_type=EvidenceType.SEGMENT_ASSIGNMENT,
            value={"mandatory": True, "incomplete": True},
            source="t",
            observed_at=180.0,
            trusted_at=180.0,
            object_id="seg_x",
            version=1,
        )
    )
    # now=781: peer age 280 ok; assignment 601 stale
    r = _b3_decide(store, _reassign(), now=781.0)
    assert r.reason_code == ReasonCode.DENY_STALE_EVIDENCE


def test_t10_both_within_budget_pass():
    store = EvidenceStore()
    _seed_reassign(store, trusted_at=180.0)
    r = _b3_decide(store, _reassign(), now=180.0 + 299.0)
    assert r.decision == GovernorDecision.ALLOW


def test_t11_e2f_fresh_evidence_not_truth():
    world = build_pipeline_mission(n_auvs=12, workload="stress")
    unfail_stress_targets(world, keep="")
    target, proposer = "auv_05", "auv_06"
    seg = resolve_target_segment(world, target)
    fail_target(world, target)
    snap = capture_snapshot(world, proposer_id=proposer, target_auv=target, segment_id=seg, t=AUTHORITY_EPOCH_S)
    store = EvidenceStore()
    populate_from_trusted_snapshot(
        store,
        proposer_id=proposer,
        snapshot=snap,
        energy=world.auvs[proposer].energy,
        position=world.auvs[proposer].position,
        observations={},
        version=1,
    )
    recover_target(world, target)
    assert target not in world.failed_auv_ids
    proposal = _reassign(proposer, target, seg)
    ev = evaluate_requirements(proposal, store, AUTHORITY_EPOCH_S + 120.0, POLICY)
    assert ev.passed
    r = _b3_decide(store, proposal, now=AUTHORITY_EPOCH_S + 120.0)
    assert r.decision == GovernorDecision.ALLOW
    assert r.reason_code != ReasonCode.DENY_HARD_EXPIRY


def test_t12_world_change_without_packet_does_not_refresh():
    store = EvidenceStore()
    _seed_reassign(store, trusted_at=180.0, peer_failed=True)
    rec = store.get_latest(EvidenceType.PEER_AVAILABILITY, "auv_05")
    assert rec is not None
    trusted_before = rec.trusted_at
    version_before = rec.version
    world = build_pipeline_mission(n_auvs=12, workload="stress")
    recover_target(world, "auv_05") if "auv_05" in world.auvs else None
    rec2 = store.get_latest(EvidenceType.PEER_AVAILABILITY, "auv_05")
    assert rec2 is not None
    assert rec2.trusted_at == trusted_before
    assert rec2.version == version_before
    assert rec2.value["failed"] is True


def test_t13_trusted_update_refreshes_store():
    store = EvidenceStore()
    _seed_reassign(store, trusted_at=180.0, peer_failed=True, version=1)
    apply_trusted_remote_update(
        store,
        target_auv="auv_05",
        segment_id="seg_x",
        peer_failed=False,
        segment_mandatory=True,
        segment_incomplete=True,
        segment_owner="auv_05",
        trusted_at=450.0,
        version=2,
        source="packet",
    )
    rec = store.get_latest(EvidenceType.PEER_AVAILABILITY, "auv_05")
    assert rec is not None
    assert rec.trusted_at == 450.0
    assert rec.version == 2
    assert rec.value["failed"] is False


def test_t14_recon_update_restores_eligibility():
    store = EvidenceStore()
    _seed_reassign(store, trusted_at=180.0)
    r_stale = _b3_decide(store, _reassign(), now=500.0)
    assert r_stale.reason_code == ReasonCode.DENY_STALE_EVIDENCE
    refresh_known_remote_from_supervisor_view(
        store,
        peer_failed_by_id={"auv_05": True},
        segments={"seg_x": {"mandatory": True, "incomplete": True, "owner": "auv_05"}},
        trusted_at=500.0,
        source="recon",
    )
    r_ok = _b3_decide(store, _reassign(), now=500.0)
    assert r_ok.decision == GovernorDecision.ALLOW


def test_t15_b3_age_over_900_not_hard_expiry():
    store = EvidenceStore()
    _seed_reassign(store, trusted_at=1000.0)
    cap_issued = 0.0
    r = _b3_decide(store, _reassign(), now=1000.0, issued_at=cap_issued)
    assert r.reason_code != ReasonCode.DENY_HARD_EXPIRY
    assert r.decision == GovernorDecision.ALLOW


def test_t16_b2_age_over_900_legacy_hard_expiry():
    b4 = make_baseline("B4", MissionPlanner(), ExecutionAdapter())
    cap = issue_capsule("auv_06", "m", 0.0)
    proposal = _reassign()
    r = b4.decide(
        proposal,
        ConnectivityState.CONNECTED,
        cap,
        last_authority_update=0.0,
        now=1000.0,
        energy=_energy(),
        supervisor_reachable=True,
        conditional_ok=True,
    )
    assert r.reason_code == ReasonCode.DENY_HARD_EXPIRY


def test_firewall_evaluate_requirements_has_no_global_world():
    src = inspect.getsource(evaluate_requirements)
    assert "failed_auv_ids" not in src
    assert "world.segments" not in src


def test_policy_loader_frozen_flags_and_budgets():
    assert POLICY.apply_cgea_multistage_contraction is False
    assert POLICY.apply_capsule_hard_expiry_deny is False
    assert POLICY.budgets_s["PEER_AVAILABILITY"] == 300.0
    assert POLICY.budgets_s["SEGMENT_ASSIGNMENT"] == 600.0
    assert POLICY.budgets_s["LOCAL_NAVIGATION"] == 40.0


def test_missing_target_parameter_missing_evidence():
    store = EvidenceStore()
    _seed_reassign(store, trusted_at=180.0)
    proposal = ActionProposal(
        proposer_id="auv_06",
        action_type=ActionType.REASSIGN_ANOTHER_AUV,
        parameters={"segment_id": "seg_x"},
        expected_energy_cost_j=10.0,
        risk_class=RiskClass.CONSEQUENTIAL,
        confidence=0.9,
        mission_state_version=0,
    )
    ev = evaluate_requirements(proposal, store, 200.0, POLICY)
    assert ev.passed is False
    assert ev.reason_code == "DENY_MISSING_EVIDENCE"
