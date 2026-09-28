"""Semantic conflicts ignore capsule_id; version skew alone is not a conflict."""

from __future__ import annotations

from cgea.governance import NodeJournalEntry, reconcile_partitions
from cgea.governance.semantic_conflicts import detect_semantic_conflicts
from cgea.mission import ActionType


def _entry(**kwargs):
    base = dict(
        time=0.0,
        node_id="auv_00",
        actions_proposed=[],
        actions_executed=[],
        executed_params=[],
        capsule_id=None,
        energy_battery_j=1e6,
        task_ownership={"seg_00": "auv_00"},
        observations={},
        state_version=1,
    )
    base.update(kwargs)
    return NodeJournalEntry(**base)


def test_capsule_presence_does_not_change_conflicts():
    j_none = {
        "a": [_entry(node_id="a", capsule_id=None, task_ownership={"s": "a"})],
        "b": [_entry(node_id="b", capsule_id=None, task_ownership={"s": "b"})],
    }
    j_cap = {
        "a": [_entry(node_id="a", capsule_id="cap_x", task_ownership={"s": "a"})],
        "b": [_entry(node_id="b", capsule_id="cap_y", task_ownership={"s": "b"})],
    }
    a = detect_semantic_conflicts(j_none)
    b = detect_semantic_conflicts(j_cap)
    assert a["semantic_conflicts_per_mission"] == b["semantic_conflicts_per_mission"]
    assert a["by_kind"] == b["by_kind"]
    r1 = reconcile_partitions([], j_none, 0.0)
    r2 = reconcile_partitions([], j_cap, 0.0)
    assert r1.conflicts == r2.conflicts


def test_state_version_skew_is_not_a_conflict():
    j = {
        "a": [_entry(node_id="a", state_version=1, task_ownership={"s": "a"})],
        "b": [_entry(node_id="b", state_version=99, task_ownership={"s": "a"})],
    }
    sem = detect_semantic_conflicts(j)
    assert sem["by_kind"]["conflicting_task_ownership"] == 0
    assert sem["semantic_conflicts_per_mission"] == 0


def test_dual_live_owners_without_chain_is_conflict():
    j = {
        "a": [_entry(node_id="a", task_ownership={"s": "a"})],
        "b": [_entry(node_id="b", task_ownership={"s": "b"})],
    }
    sem = detect_semantic_conflicts(j)
    assert sem["by_kind"]["conflicting_task_ownership"] >= 1


def test_duplicate_after_complete():
    j = {
        "a": [
            _entry(
                node_id="a",
                time=1.0,
                observations={"seg_completed": True, "completed_segment": "s"},
            ),
            _entry(
                node_id="a",
                time=2.0,
                actions_executed=[ActionType.REPEAT_SONAR_SCAN.value],
                executed_params=[{"action": ActionType.REPEAT_SONAR_SCAN.value, "segment_id": "s"}],
            ),
        ]
    }
    sem = detect_semantic_conflicts(j)
    assert sem["by_kind"]["duplicate_execution"] >= 1
