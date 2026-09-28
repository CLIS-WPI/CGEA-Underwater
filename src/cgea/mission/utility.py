"""Frozen mission utility (locked before examining B4 results)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from cgea.types import CgeaBaseModel

UTILITY_FREEZE_ID = "utility_v2_frozen_2026-09-28"

# Locked weights. Do not edit after the E1-v2 campaign starts.
FROZEN_WEIGHTS: dict[str, float] = {
    "completed_mandatory": 10.0,
    "anomaly_resolved": 18.0,
    "useful_reassignment": 12.0,
    "missed_mandatory": 15.0,
    "exclusion_violation": 8.0,
    "reserve_violation": 10.0,
    "duplicate_work": 5.0,
    "energy_per_1e5_j": 0.5,
    "unresolved_conflict": 6.0,
}


class UtilityBreakdown(CgeaBaseModel):
    freeze_id: str = UTILITY_FREEZE_ID
    completed_mandatory: int = 0
    anomalies_resolved: int = 0
    useful_reassignments: int = 0
    missed_mandatory: int = 0
    exclusion_violations: int = 0
    reserve_violations: int = 0
    duplicate_work: int = 0
    total_energy_j: float = 0.0
    unresolved_conflicts: int = 0
    terms: dict[str, float] = Field(default_factory=dict)
    utility: float = 0.0


def compute_mission_utility(
    *,
    completed_mandatory: int,
    anomalies_resolved: int,
    useful_reassignments: int,
    missed_mandatory: int,
    exclusion_violations: int,
    reserve_violations: int,
    duplicate_work: int,
    total_energy_j: float,
    unresolved_conflicts: int,
    weights: dict[str, float] | None = None,
) -> UtilityBreakdown:
    w = weights or FROZEN_WEIGHTS
    terms = {
        "completed_mandatory": w["completed_mandatory"] * completed_mandatory,
        "anomaly_resolved": w["anomaly_resolved"] * anomalies_resolved,
        "useful_reassignment": w["useful_reassignment"] * useful_reassignments,
        "missed_mandatory": -w["missed_mandatory"] * missed_mandatory,
        "exclusion_violation": -w["exclusion_violation"] * exclusion_violations,
        "reserve_violation": -w["reserve_violation"] * reserve_violations,
        "duplicate_work": -w["duplicate_work"] * duplicate_work,
        "energy": -w["energy_per_1e5_j"] * (total_energy_j / 1e5),
        "unresolved_conflict": -w["unresolved_conflict"] * unresolved_conflicts,
    }
    return UtilityBreakdown(
        completed_mandatory=completed_mandatory,
        anomalies_resolved=anomalies_resolved,
        useful_reassignments=useful_reassignments,
        missed_mandatory=missed_mandatory,
        exclusion_violations=exclusion_violations,
        reserve_violations=reserve_violations,
        duplicate_work=duplicate_work,
        total_energy_j=total_energy_j,
        unresolved_conflicts=unresolved_conflicts,
        terms=terms,
        utility=float(sum(terms.values())),
    )


def world_utility(world: Any, unresolved_conflicts: int = 0) -> UtilityBreakdown:
    completed_mandatory = sum(
        1 for s in world.segments.values() if s.mandatory and s.completed and not s.abandoned
    )
    missed_mandatory = sum(
        1 for s in world.segments.values() if s.mandatory and (not s.completed or s.abandoned)
    )
    total_energy = sum(
        a.energy.propulsion_j + a.energy.communication_j + a.energy.compute_j
        for a in world.auvs.values()
        if not a.is_gateway
    )
    anomalies_resolved = sum(1 for a in world.anomalies.values() if a.get("resolved"))
    return compute_mission_utility(
        completed_mandatory=completed_mandatory,
        anomalies_resolved=anomalies_resolved,
        useful_reassignments=int(world.useful_reassignment_count),
        missed_mandatory=missed_mandatory,
        exclusion_violations=int(world.exclusion_violation_count),
        reserve_violations=int(world.reserve_violation_count),
        duplicate_work=int(world.duplicate_work_count),
        total_energy_j=total_energy,
        unresolved_conflicts=unresolved_conflicts,
    )
