"""Predeclared E4 DEV-sanity cells. Frozen budgets. Not a TEST campaign."""

from __future__ import annotations

from cgea.agent import ActionProposal
from cgea.experiments.e2_authority_age import AUTHORITY_EPOCH_S
from cgea.mission import ActionType, RiskClass

E4_SANITY_CAMPAIGN = "e4_dev_sanity"
DEV_SEEDS = [0, 1, 2, 3, 4]
TEST_SEEDS_FORBIDDEN = [5, 6, 7, 8, 9]
METHODS = (
    ("B4-NoFreshness", None, "B0"),
    ("B4-FixedExpiry", 400.0, "B1"),
    ("B4", None, "B2"),
    ("B4-Evidence", None, "B3"),
)

# All times are 20 s tick-aligned. Remote snapshot trusted_at = 180.
# Cell C: peer-only comms refresh at 560 so (800-560)=240 < 300 while (800-180)=620 > 600.
CELLS: dict[str, dict] = {
    "A": {
        "name": "VALID_REMOTE_FRESH",
        "challenge_time_s": 400.0,
        "authority_age_s": 220.0,
        "expected_freshness": "aging",
        "context_mode": "benign_static",
        "change_age_s": 1e18,
        "peer_refresh_s": None,
        "controlled_action": "reassign_another_auv",
    },
    "B": {
        "name": "PEER_STALE_ASSIGNMENT_VALID",
        "challenge_time_s": 500.0,
        "authority_age_s": 320.0,
        "expected_freshness": "aging",
        "context_mode": "benign_static",
        "change_age_s": 1e18,
        "peer_refresh_s": None,
        "controlled_action": "reassign_another_auv",
    },
    "C": {
        "name": "PEER_VALID_ASSIGNMENT_STALE",
        "challenge_time_s": 800.0,
        "authority_age_s": 620.0,
        "expected_freshness": "stale",
        "context_mode": "benign_static",
        "change_age_s": 1e18,
        "peer_refresh_s": 560.0,
        "controlled_action": "reassign_another_auv",
    },
    "D": {
        "name": "BOTH_VALID_BUT_GLOBAL_CHANGED",
        "challenge_time_s": 300.0,
        "authority_age_s": 120.0,
        "expected_freshness": "fresh",
        "context_mode": "target_recovers_age60",
        "change_age_s": 60.0,
        "peer_refresh_s": None,
        "controlled_action": "reassign_another_auv",
    },
    "E": {
        "name": "BOTH_STALE",
        "challenge_time_s": 800.0,
        "authority_age_s": 620.0,
        "expected_freshness": "stale",
        "context_mode": "benign_static",
        "change_age_s": 1e18,
        "peer_refresh_s": None,
        "controlled_action": "reassign_another_auv",
    },
    "F": {
        "name": "LOCAL_ACTION_OLD_CAPSULE_FRESH_LOCAL_EVIDENCE",
        "challenge_time_s": 1140.0,
        "authority_age_s": 960.0,
        "expected_freshness": "hard_expired",
        "context_mode": "benign_static",
        "change_age_s": 1e18,
        "peer_refresh_s": None,
        "controlled_action": "collision_avoidance",
    },
}


def controlled_local_proposal(world, snapshot: dict, action: ActionType) -> ActionProposal:
    proposer = snapshot["proposer_id"]
    auv = world.auvs[proposer]
    return ActionProposal(
        proposer_id=proposer,
        action_type=action,
        parameters={},
        expected_energy_cost_j=1.0,
        risk_class=RiskClass.LOW,
        confidence=0.9,
        mission_state_version=auv.state_version,
        rationale="e4_controlled_local",
    )


def first_stale_type(evidence_evaluation: dict | None) -> str | None:
    if not evidence_evaluation:
        return None
    for gap in evidence_evaluation.get("evidence_gaps") or []:
        if gap.get("reason") == "stale":
            return str(gap.get("evidence_type"))
    return None
