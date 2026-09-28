"""Action-relevant semantic conflicts. Capsule presence is ignored.

Mere state_version or ownership_hash disagreement across partitions is NOT a conflict.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from cgea.mission import ActionType


CONFLICT_KINDS = (
    "conflicting_task_ownership",
    "duplicate_execution",
    "contradictory_reassignment",
    "incompatible_mission_state",
    "inconsistent_anomaly_state",
)


def _latest_by_node(journals: dict[str, list[Any]]) -> dict[str, Any]:
    out = {}
    for nid, entries in journals.items():
        if entries:
            out[nid] = entries[-1]
    return out


def _reassign_events(journals: dict[str, list[Any]]) -> list[dict[str, Any]]:
    ev = []
    for nid, entries in journals.items():
        for e in entries:
            params = list(getattr(e, "executed_params", None) or [])
            acts = list(e.actions_executed or [])
            if params:
                for p in params:
                    if p.get("action") == ActionType.REASSIGN_ANOTHER_AUV.value:
                        ev.append(
                            {
                                "time": float(e.time),
                                "proposer": nid,
                                "segment_id": p.get("segment_id"),
                                "target_auv": p.get("target_auv"),
                            }
                        )
            else:
                for act in acts:
                    if act == ActionType.REASSIGN_ANOTHER_AUV.value:
                        ev.append(
                            {
                                "time": float(e.time),
                                "proposer": nid,
                                "segment_id": None,
                                "target_auv": None,
                            }
                        )
    ev.sort(key=lambda x: (x["time"], x["proposer"]))
    return ev


def detect_semantic_conflicts(
    journals: dict[str, list[Any]],
    *,
    failed_ids: list[str] | None = None,
    duplicate_work_count: int = 0,
) -> dict[str, Any]:
    """Return type breakdown, events, and total. Capsule_id is never consulted."""
    failed = set(failed_ids or [])
    events: list[dict[str, Any]] = []

    # --- contradictory reassignment: two ALLOW reassigns, same segment, no chain ---
    by_seg: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in _reassign_events(journals):
        seg = ev.get("segment_id") or "_unknown"
        by_seg[seg].append(ev)
    for seg, seq in by_seg.items():
        if len(seq) < 2:
            continue
        # Same-tick distinct proposers: incompatible
        by_t: dict[float, set[str]] = defaultdict(set)
        for ev in seq:
            by_t[ev["time"]].add(ev["proposer"])
        for t, props in by_t.items():
            if len(props) > 1:
                events.append(
                    {
                        "kind": "contradictory_reassignment",
                        "severity": "high",
                        "detail": f"segment {seg} reassigned by {sorted(props)} at t={t}",
                    }
                )
        # Distinct proposers without a later event from the previous winner covering the same segment
        proposers = [ev["proposer"] for ev in seq]
        if len(set(proposers)) > 1:
            # Valid chain: sequential times, each new proposer takes over (allowed).
            # Invalid: two live proposers both remaining claimants (handled in ownership).
            pass

    # --- conflicting ownership: multiple live self-claims without a unique last reassign ---
    latest = _latest_by_node(journals)
    claimants: dict[str, set[str]] = defaultdict(set)
    for nid, e in latest.items():
        if nid in failed:
            continue
        own = dict(e.task_ownership or {})
        for seg, owner in own.items():
            if owner == nid:
                claimants[seg].add(nid)
    last_reassign_winner: dict[str, str] = {}
    for seg, seq in by_seg.items():
        if seq:
            last_reassign_winner[seg] = seq[-1]["proposer"]
    for seg, nodes in claimants.items():
        if len(nodes) <= 1:
            continue
        winner = last_reassign_winner.get(seg)
        if winner is not None and nodes <= {winner}:
            continue
        if winner is not None and nodes == {winner} | (nodes & failed):
            continue
        # Multiple live self-claims and they are not a single valid winner
        if winner is None or len(nodes - {winner}) >= 1:
            events.append(
                {
                    "kind": "conflicting_task_ownership",
                    "severity": "high",
                    "detail": f"segment {seg} claimed by {sorted(nodes)} without a unique reassignment chain",
                }
            )

    # --- duplicate execution: work after completion or uncoordinated claim of same open unit ---
    scans_by_seg_node: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    completed_at: dict[str, float] = {}
    for nid, entries in journals.items():
        for e in entries:
            params = list(getattr(e, "executed_params", None) or [])
            for p in params:
                act = p.get("action")
                seg = p.get("segment_id")
                if act in (
                    ActionType.REPEAT_SONAR_SCAN.value,
                    ActionType.LOCAL_INFERENCE.value,
                ) and seg:
                    if seg in completed_at and float(e.time) >= completed_at[seg]:
                        events.append(
                            {
                                "kind": "duplicate_execution",
                                "severity": "low",
                                "detail": f"{nid} executed {act} on completed {seg} at t={e.time}",
                            }
                        )
                    scans_by_seg_node[seg][nid] += 1
            obs = dict(e.observations or {})
            if obs.get("seg_completed"):
                sid = obs.get("completed_segment")
                if sid and sid not in completed_at:
                    completed_at[sid] = float(e.time)
    has_params = any(
        getattr(e, "executed_params", None) for entries in journals.values() for e in entries
    )
    if not has_params:
        for _ in range(int(duplicate_work_count)):
            events.append(
                {
                    "kind": "duplicate_execution",
                    "severity": "low",
                    "detail": "adapter duplicate_work_count",
                }
            )

    # Uncoordinated dual scan of same open segment by two owners (no reassign between them)
    for seg, node_counts in scans_by_seg_node.items():
        live = {n for n, c in node_counts.items() if c > 0 and n not in failed}
        if len(live) > 1 and seg not in last_reassign_winner:
            events.append(
                {
                    "kind": "duplicate_execution",
                    "severity": "high",
                    "detail": f"uncoordinated scans of {seg} by {sorted(live)}",
                }
            )

    # --- incompatible mission-state: same node both abandons and later inspects without reassign ---
    per_node_seg: dict[tuple[str, str], set[str]] = defaultdict(set)
    for nid, entries in journals.items():
        for e in entries:
            for p in getattr(e, "executed_params", None) or []:
                seg = p.get("segment_id")
                act = p.get("action")
                if not seg:
                    continue
                if act == ActionType.ABANDON_MANDATORY_INSPECTION.value:
                    per_node_seg[(nid, seg)].add("abandoned")
                if act in (ActionType.REPEAT_SONAR_SCAN.value, ActionType.LOCAL_INFERENCE.value):
                    per_node_seg[(nid, seg)].add("inspected")
    for (nid, seg), fs in per_node_seg.items():
        if "abandoned" in fs and "inspected" in fs and last_reassign_winner.get(seg) != nid:
            events.append(
                {
                    "kind": "incompatible_mission_state",
                    "severity": "high",
                    "detail": f"{nid} abandoned and inspected {seg} without a valid reassignment chain",
                }
            )

    # --- inconsistent anomaly: resolved executed vs later enter_exclusion denying that ---
    resolved_at: dict[str, float] = {}
    for nid, entries in journals.items():
        for e in entries:
            obs = dict(e.observations or {})
            aid = obs.get("anomaly_resolved")
            if aid:
                resolved_at.setdefault(str(aid), float(e.time))
            for p in getattr(e, "executed_params", None) or []:
                if p.get("action") == ActionType.ENTER_EXCLUSION_ZONE.value:
                    anom = str(p.get("anomaly_id") or "")
                    if anom in resolved_at and float(e.time) > resolved_at[anom] + 1e-9:
                        events.append(
                            {
                                "kind": "inconsistent_anomaly_state",
                                "severity": "high",
                                "detail": f"{nid} entered exclusion for {anom} after it was resolved",
                            }
                        )

    by_kind = {k: 0 for k in CONFLICT_KINDS}
    for ev in events:
        by_kind[ev["kind"]] = by_kind.get(ev["kind"], 0) + 1
    total = int(sum(by_kind.values()))
    return {
        "semantic_conflicts_per_mission": total,
        "by_kind": by_kind,
        "events": events[:200],
    }
