"""Deterministic 300-vs-600 B3 integration (no campaign)."""

from __future__ import annotations

from cgea.agent import ActionProposal, ExecutionAdapter, MissionPlanner
from cgea.baselines import make_baseline
from cgea.governance import ConnectivityState, GovernorDecision, ReasonCode, issue_capsule
from cgea.governance.evidence import (
    EvidenceStore,
    apply_trusted_remote_update,
    load_action_evidence_policy,
)
from cgea.mission import ActionType, EnergyState, RiskClass

POLICY = load_action_evidence_policy()
TRACE: list[dict] = []


def _decide(store, now: float, issued_at: float = 180.0):
    ctrl = make_baseline("B4-Evidence", MissionPlanner(), ExecutionAdapter())
    cap = issue_capsule("auv_06", "m", issued_at)
    proposal = ActionProposal(
        proposer_id="auv_06",
        action_type=ActionType.REASSIGN_ANOTHER_AUV,
        parameters={"target_auv": "auv_05", "segment_id": "seg_x"},
        expected_energy_cost_j=10.0,
        risk_class=RiskClass.CONSEQUENTIAL,
        confidence=0.9,
        mission_state_version=0,
    )
    r = ctrl.decide(
        proposal,
        ConnectivityState.PARTITIONED,
        cap,
        last_authority_update=issued_at,
        now=now,
        energy=EnergyState(battery_j=1e6, reserve_j=1e5),
        supervisor_reachable=False,
        position={"x": 0.0, "y": 0.0},
        conditional_ok=True,
        evidence_store=store,
        evidence_policy=POLICY,
    )
    TRACE.append(
        {
            "now": now,
            "decision": r.decision.value,
            "reason_code": r.reason_code.value,
            "evidence_ages": (r.evidence_evaluation or {}).get("evidence_ages"),
            "gaps": (r.evidence_evaluation or {}).get("evidence_gaps"),
            "denied_hard_expiry": r.reason_code == ReasonCode.DENY_HARD_EXPIRY,
        }
    )
    return r


def test_independent_300_vs_600_no_peer_refresh():
    TRACE.clear()
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
        source="snapshot",
    )
    r400 = _decide(store, 400.0)
    assert r400.decision == GovernorDecision.ALLOW
    assert r400.reason_code != ReasonCode.DENY_HARD_EXPIRY
    r500 = _decide(store, 500.0)
    assert r500.decision == GovernorDecision.DENY
    assert r500.reason_code == ReasonCode.DENY_STALE_EVIDENCE
    ages = (r500.evidence_evaluation or {}).get("evidence_ages", {})
    peer_age = ages.get("PEER_AVAILABILITY:auv_05")
    seg_age = ages.get("SEGMENT_ASSIGNMENT:seg_x")
    assert peer_age == 320.0
    assert seg_age == 320.0


def test_independent_clocks_after_peer_refresh():
    TRACE.clear()
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
        source="snapshot",
    )
    apply_trusted_remote_update(
        store,
        target_auv="auv_05",
        segment_id="seg_x",
        peer_failed=True,
        segment_mandatory=True,
        segment_incomplete=True,
        segment_owner="auv_05",
        trusted_at=450.0,
        version=2,
        source="packet",
    )
    # Assignment still trusted_at=180 because apply_trusted updates BOTH.
    # Re-put assignment only at 180 by writing peer-only then assignment-only.
    # The last apply set both to 450. Rebuild with split timestamps.
    store = EvidenceStore()
    from cgea.governance.evidence import EvidenceRecord, EvidenceType

    store.put(
        EvidenceRecord(
            evidence_type=EvidenceType.PEER_AVAILABILITY,
            value={"failed": True},
            source="packet",
            observed_at=450.0,
            trusted_at=450.0,
            object_id="auv_05",
            version=2,
        )
    )
    store.put(
        EvidenceRecord(
            evidence_type=EvidenceType.SEGMENT_ASSIGNMENT,
            value={"mandatory": True, "incomplete": True, "owner": "auv_05"},
            source="snapshot",
            observed_at=180.0,
            trusted_at=180.0,
            object_id="seg_x",
            version=1,
        )
    )
    r700 = _decide(store, 700.0)
    assert r700.decision == GovernorDecision.ALLOW
    ages700 = (r700.evidence_evaluation or {}).get("evidence_ages", {})
    assert ages700["PEER_AVAILABILITY:auv_05"] == 250.0
    assert ages700["SEGMENT_ASSIGNMENT:seg_x"] == 520.0
    r800 = _decide(store, 800.0)
    assert r800.decision == GovernorDecision.DENY
    assert r800.reason_code == ReasonCode.DENY_STALE_EVIDENCE
    ages800 = (r800.evidence_evaluation or {}).get("evidence_ages", {})
    assert ages800["PEER_AVAILABILITY:auv_05"] == 350.0
    assert ages800["SEGMENT_ASSIGNMENT:seg_x"] == 620.0
    gaps = (r800.evidence_evaluation or {}).get("evidence_gaps", [])
    reasons = {g.get("evidence_type"): g.get("reason") for g in gaps}
    assert reasons.get("PEER_AVAILABILITY") == "stale"
