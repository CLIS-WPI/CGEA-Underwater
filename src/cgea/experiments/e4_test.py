"""E4 held-out TEST constants. Frozen budgets. No DEV retune."""

from __future__ import annotations

from cgea.experiments.e3_fixed_expiry import CONTEXTS, PROPOSAL_AGES, TEST_SEEDS

E4_TEST_CAMPAIGN = "e4_test"
PEER_BUDGET_S = 300.0
SEGMENT_BUDGET_S = 600.0
LOCAL_SLICE_CHALLENGE_S = 1140.0
LOCAL_SLICE_AGE_S = 960.0


def assign_region(peer_age: float, assign_age: float, cell_status: str) -> str:
    """Predeclared exclusive regions. Do not invent after TEST."""
    if peer_age > PEER_BUDGET_S and assign_age > SEGMENT_BUDGET_S:
        return "R4"
    if peer_age > PEER_BUDGET_S and assign_age <= SEGMENT_BUDGET_S:
        return "R3"
    if cell_status == "obsolete" and peer_age <= PEER_BUDGET_S:
        return "R2"
    return "R1"
