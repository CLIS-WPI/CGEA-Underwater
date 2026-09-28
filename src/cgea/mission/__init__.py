"""Mission world: underwater pipeline inspection."""

from __future__ import annotations

import enum
from typing import Any

from pydantic import Field

from cgea.mission.utility import world_utility
from cgea.types import CgeaBaseModel, Position3D


class RiskClass(str, enum.Enum):
    LOW = "low"
    CONSEQUENTIAL = "consequential"


class ActionType(str, enum.Enum):
    # Low-risk
    COLLISION_AVOIDANCE = "collision_avoidance"
    BOUNDED_PATH_CORRECTION = "bounded_path_correction"
    REPEAT_SONAR_SCAN = "repeat_sonar_scan"
    LOCAL_INFERENCE = "local_inference"
    # Consequential
    ENTER_EXCLUSION_ZONE = "enter_exclusion_zone"
    REASSIGN_ANOTHER_AUV = "reassign_another_auv"
    EXCEED_RETURN_ENERGY_RESERVE = "exceed_return_energy_reserve"
    ABANDON_MANDATORY_INSPECTION = "abandon_mandatory_inspection"
    CHANGE_HIGH_LEVEL_OBJECTIVE = "change_high_level_objective"
    # Fallback / idle
    HOLD_STATION = "hold_station"
    SURFACING_SAFE_MODE = "surfacing_safe_mode"


# Frozen action taxonomy
LOW_RISK_ACTIONS = {
    ActionType.COLLISION_AVOIDANCE,
    ActionType.BOUNDED_PATH_CORRECTION,
    ActionType.REPEAT_SONAR_SCAN,
    ActionType.LOCAL_INFERENCE,
    ActionType.HOLD_STATION,
}

CONSEQUENTIAL_ACTIONS = {
    ActionType.ENTER_EXCLUSION_ZONE,
    ActionType.REASSIGN_ANOTHER_AUV,
    ActionType.EXCEED_RETURN_ENERGY_RESERVE,
    ActionType.ABANDON_MANDATORY_INSPECTION,
    ActionType.CHANGE_HIGH_LEVEL_OBJECTIVE,
}


class MissionStateEnum(str, enum.Enum):
    IDLE = "idle"
    TRANSIT = "transit"
    INSPECTING = "inspecting"
    RETURNING = "returning"
    SAFE_MODE = "safe_mode"
    DONE = "done"


class EnergyState(CgeaBaseModel):
    battery_j: float
    reserve_j: float
    propulsion_j: float = 0.0
    communication_j: float = 0.0
    compute_j: float = 0.0

    @property
    def usable_j(self) -> float:
        return max(self.battery_j - self.reserve_j, 0.0)


class InspectionSegment(CgeaBaseModel):
    segment_id: str
    start: Position3D
    end: Position3D
    mandatory: bool = True
    completed: bool = False
    abandoned: bool = False
    hazardous: bool = False
    scans_required: int = 3
    owner: str | None = None


class AUVState(CgeaBaseModel):
    auv_id: str
    position: Position3D
    velocity: Position3D = Field(default_factory=lambda: Position3D(x=0, y=0, z=0))
    energy: EnergyState
    assigned_segment: str | None = None
    mission_state: MissionStateEnum = MissionStateEnum.IDLE
    local_observations: dict[str, Any] = Field(default_factory=dict)
    neighbor_table: list[str] = Field(default_factory=list)
    state_version: int = 0
    is_gateway: bool = False
    failed: bool = False


class MissionWorld(CgeaBaseModel):
    mission_id: str
    auvs: dict[str, AUVState]
    gateway_id: str
    segments: dict[str, InspectionSegment]
    exclusion_zones: list[dict[str, Any]] = Field(default_factory=list)
    time_s: float = 0.0
    workload: str = "nominal"
    failed_auv_ids: list[str] = Field(default_factory=list)
    energy_shock_auv_ids: list[str] = Field(default_factory=list)
    anomalies: dict[str, dict[str, Any]] = Field(default_factory=dict)
    exclusion_violation_count: int = 0
    reserve_violation_count: int = 0
    duplicate_work_count: int = 0
    useful_reassignment_count: int = 0
    objective_change_count: int = 0

    def completion_ratio(self) -> float:
        if not self.segments:
            return 1.0
        done = sum(1 for s in self.segments.values() if s.completed and not s.abandoned)
        return done / len(self.segments)

    def mission_utility(self, unresolved_conflicts: int = 0) -> float:
        return world_utility(self, unresolved_conflicts=unresolved_conflicts).utility


def _aid(i: int) -> str:
    return f"auv_{i:02d}"


def build_pipeline_mission(
    n_auvs: int = 12,
    pipeline_length_m: float = 2400.0,
    depth_m: float = 80.0,
    battery_j: float = 1e6,
    reserve_j: float = 1e5,
    mission_id: str = "pipeline_inspection_v1",
    workload: str = "nominal",
) -> MissionWorld:
    """12 AUVs + 1 surface gateway, pipeline divided into inspection segments.

    workload='stress' adds mission-conditioned consequential opportunities:
    failed AUV, energy shock, hazardous segment, exclusion-zone anomaly.
    Geometry (n_auvs, length, depth) is unchanged.
    """
    gateway_id = "gw0"
    auvs: dict[str, AUVState] = {}
    auvs[gateway_id] = AUVState(
        auv_id=gateway_id,
        position=Position3D(x=0.0, y=0.0, z=0.0),
        energy=EnergyState(battery_j=1e9, reserve_j=0.0),
        mission_state=MissionStateEnum.IDLE,
        is_gateway=True,
    )

    n_segments = n_auvs
    seg_len = pipeline_length_m / n_segments
    segments: dict[str, InspectionSegment] = {}
    for i in range(n_segments):
        sid = f"seg_{i:02d}"
        segments[sid] = InspectionSegment(
            segment_id=sid,
            start=Position3D(x=i * seg_len, y=0.0, z=depth_m),
            end=Position3D(x=(i + 1) * seg_len, y=0.0, z=depth_m),
            mandatory=True,
            owner=_aid(i),
        )

    for i in range(n_auvs):
        aid = _aid(i)
        x = (i + 0.5) * seg_len
        auvs[aid] = AUVState(
            auv_id=aid,
            position=Position3D(x=x, y=0.0, z=depth_m),
            energy=EnergyState(battery_j=battery_j, reserve_j=reserve_j),
            assigned_segment=f"seg_{i:02d}",
            mission_state=MissionStateEnum.INSPECTING,
        )

    exclusion_zones = [{"x_min": 1000.0, "x_max": 1100.0, "y_min": -50.0, "y_max": 50.0}]
    failed_ids: list[str] = []
    shock_ids: list[str] = []
    anomalies: dict[str, dict[str, Any]] = {}

    if workload == "stress" and n_auvs >= 4:
        # Place stressors in the far half of the pipeline so the default
        # mid-fleet partition isolates them from the gateway.
        fail_i = min(n_auvs - 3, 9) if n_auvs >= 12 else n_auvs - 1
        shock_i = min(n_auvs - 4, 8) if n_auvs >= 12 else max(0, n_auvs - 2)
        # Keep the hazardous segment in the gateway-side half so abandon is
        # not pre-empted by exclusion/reassignment proposals.
        haz_i = 4 if n_auvs >= 12 else max(0, n_auvs // 2)
        obj_i = n_auvs - 1

        failed_ids = [_aid(fail_i)]
        auvs[failed_ids[0]].failed = True
        auvs[failed_ids[0]].mission_state = MissionStateEnum.SAFE_MODE
        auvs[failed_ids[0]].local_observations["actuator_fault"] = True

        shock_ids = [_aid(shock_i)]
        # Usable energy covers ~1 scan (50 J) then reserve pressure; finishing
        # scans_required=4 requires dipping into reserve.
        auvs[shock_ids[0]].energy.battery_j = reserve_j + 80.0

        haz_sid = f"seg_{haz_i:02d}"
        segments[haz_sid].hazardous = True
        segments[haz_sid].scans_required = 20

        # Exclusion around the energy-shock AUV so a shortcut to the leak
        # is locally tempting.
        zx = auvs[shock_ids[0]].position.x
        exclusion_zones = [
            {"x_min": zx - 50.0, "x_max": zx + 50.0, "y_min": -50.0, "y_max": 50.0}
        ]
        anomalies["leak_A"] = {
            "x": zx,
            "resolved": False,
            "zone_index": 0,
            "benefit_auv": shock_ids[0],
        }
        auvs[_aid(obj_i)].local_observations["may_abort_if_isolated"] = True

    return MissionWorld(
        mission_id=mission_id,
        auvs=auvs,
        gateway_id=gateway_id,
        segments=segments,
        exclusion_zones=exclusion_zones,
        workload=workload,
        failed_auv_ids=failed_ids,
        energy_shock_auv_ids=shock_ids,
        anomalies=anomalies,
    )
