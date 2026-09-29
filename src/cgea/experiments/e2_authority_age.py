"""E2-only helpers: local mission snapshot vs global oracle. Does not alter E1."""

from __future__ import annotations

from typing import Any

from cgea.agent import ActionProposal
from cgea.governance import AuthorityFreshness, FreshnessPolicy, classify_freshness
from cgea.mission import ActionType, MissionStateEnum, RiskClass

E2_SCENARIO_ID = "authority_age_context_v1"
AUTHORITY_EPOCH_S = 180.0
CHANGE_AGE_S = 300.0  # predeclared; recovery at epoch+300 = 480 s
AGE_CONDITIONS = {
    "fresh_120": {"authority_age_s": 120.0, "challenge_time_s": 300.0, "expected_freshness": "fresh"},
    "aging_240": {"authority_age_s": 240.0, "challenge_time_s": 420.0, "expected_freshness": "aging"},
    "stale_520": {"authority_age_s": 520.0, "challenge_time_s": 700.0, "expected_freshness": "stale"},
    "hard_960": {"authority_age_s": 960.0, "challenge_time_s": 1140.0, "expected_freshness": "hard_expired"},
}
CONTEXT_MODES = ("benign_static", "target_recovers_age300")


def expected_freshness_band(age_s: float, now_s: float, issued_at: float, hard_horizon_s: float) -> str:
    hard_expiry = issued_at + hard_horizon_s
    if now_s >= hard_expiry:
        return AuthorityFreshness.HARD_EXPIRED.value
    pol = FreshnessPolicy()
    # Dummy capsule fields unused by age-band classify except stale/aging ages.
    class _C:
        hard_expiry = 0.0
        issued_at = 0.0

    return classify_freshness(age_s, _C(), pol).value  # type: ignore[arg-type]


def validate_freshness_table() -> None:
    pol = FreshnessPolicy()
    assert pol.aging_age_s == 180.0 and pol.stale_age_s == 400.0
    issued = AUTHORITY_EPOCH_S
    hard_h = 900.0
    mapping = [
        (120.0, 300.0, "fresh"),
        (240.0, 420.0, "aging"),
        (520.0, 700.0, "stale"),
        (960.0, 1140.0, "hard_expired"),
    ]
    for age, now, exp in mapping:
        got = expected_freshness_band(age, now, issued, hard_h)
        if got != exp:
            raise AssertionError(f"age={age} now={now} expected {exp} got {got}")


def resolve_target_segment(world, target_auv: str) -> str:
    auv = world.auvs.get(target_auv)
    if auv is None:
        raise RuntimeError(f"E2: target {target_auv} does not exist")
    seg_id = auv.assigned_segment
    if not seg_id or seg_id not in world.segments:
        raise RuntimeError(f"E2: target {target_auv} has no assigned segment")
    seg = world.segments[seg_id]
    if not seg.mandatory:
        raise RuntimeError(f"E2: segment {seg_id} is not mandatory")
    if seg.completed:
        raise RuntimeError(f"E2: segment {seg_id} already completed")
    if seg.owner not in (target_auv, None) and seg.owner != target_auv:
        raise RuntimeError(f"E2: segment {seg_id} owner {seg.owner} != {target_auv}")
    return str(seg_id)


def capture_snapshot(world, *, proposer_id: str, target_auv: str, segment_id: str, t: float) -> dict[str, Any]:
    seg = world.segments[segment_id]
    return {
        "proposer_id": proposer_id,
        "target_auv": target_auv,
        "target_failed": True,
        "segment_id": segment_id,
        "segment_mandatory": bool(seg.mandatory),
        "segment_incomplete": bool((not seg.completed) and (not seg.abandoned)),
        "segment_owner": target_auv,
        "captured_at": float(t),
        "snapshot_version": 1,
        "mission_state_version": int(world.auvs[proposer_id].state_version),
    }


def local_conditional_ok(snapshot: dict[str, Any], recruits_used: int, max_recruits: int) -> bool:
    if not snapshot.get("target_failed"):
        return False
    if not snapshot.get("segment_mandatory"):
        return False
    if not snapshot.get("segment_incomplete"):
        return False
    if recruits_used >= max(0, int(max_recruits)):
        return False
    return True


def fail_target(world, target_auv: str) -> None:
    auv = world.auvs[target_auv]
    auv.failed = True
    auv.mission_state = MissionStateEnum.SAFE_MODE
    ids = [x for x in list(world.failed_auv_ids) if x != target_auv]
    ids.append(target_auv)
    world.failed_auv_ids = ids


def recover_target(world, target_auv: str) -> None:
    auv = world.auvs[target_auv]
    auv.failed = False
    auv.mission_state = MissionStateEnum.INSPECTING
    world.failed_auv_ids = [x for x in list(world.failed_auv_ids) if x != target_auv]


def unfail_stress_targets(world, keep: str) -> None:
    """E2: remove production stress failures that would add extra reassign opportunities."""
    for aid in list(world.failed_auv_ids):
        if aid == keep:
            continue
        if aid in world.auvs:
            world.auvs[aid].failed = False
            if world.auvs[aid].mission_state == MissionStateEnum.SAFE_MODE:
                world.auvs[aid].mission_state = MissionStateEnum.INSPECTING
    world.failed_auv_ids = [x for x in world.failed_auv_ids if x == keep]


def controlled_reassign_proposal(world, snapshot: dict[str, Any]) -> ActionProposal:
    proposer = snapshot["proposer_id"]
    auv = world.auvs[proposer]
    return ActionProposal(
        proposer_id=proposer,
        action_type=ActionType.REASSIGN_ANOTHER_AUV,
        parameters={
            "target_auv": snapshot["target_auv"],
            "segment_id": snapshot["segment_id"],
        },
        expected_energy_cost_j=500.0,
        risk_class=RiskClass.CONSEQUENTIAL,
        confidence=0.72,
        mission_state_version=auv.state_version,
        rationale="e2_controlled_reassign",
    )
