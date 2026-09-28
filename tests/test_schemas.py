"""Action taxonomy freeze + capsule schema checks."""

from cgea.governance import AuthorityCapsule, issue_capsule
from cgea.mission import CONSEQUENTIAL_ACTIONS, LOW_RISK_ACTIONS, ActionType


def test_action_taxonomy_frozen():
    assert ActionType.COLLISION_AVOIDANCE in LOW_RISK_ACTIONS
    assert ActionType.ENTER_EXCLUSION_ZONE in CONSEQUENTIAL_ACTIONS
    assert LOW_RISK_ACTIONS.isdisjoint(CONSEQUENTIAL_ACTIONS)


def test_capsule_schema_and_hash():
    cap = issue_capsule("auv_00", "m1", now=0.0)
    assert cap.capsule_id.startswith("cap_")
    assert cap.soft_expiry < cap.hard_expiry
    assert cap.allowed_actions
    assert cap.forbidden_actions
    assert cap.fallback_action
    h1 = cap.deterministic_hash()
    h2 = cap.deterministic_hash()
    assert h1 == h2
