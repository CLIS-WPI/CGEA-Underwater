"""Deterministic heuristic MissionPlanner — no LLM."""

from __future__ import annotations

from typing import Any

from cgea.mission import (
    CONSEQUENTIAL_ACTIONS,
    LOW_RISK_ACTIONS,
    ActionType,
    AUVState,
    MissionStateEnum,
    MissionWorld,
    RiskClass,
)
from cgea.types import CgeaBaseModel


class ActionProposal(CgeaBaseModel):
    """Planner output. MUST NOT directly access actuators."""

    proposer_id: str
    action_type: ActionType
    parameters: dict[str, Any]
    expected_energy_cost_j: float
    risk_class: RiskClass
    confidence: float
    mission_state_version: int
    rationale: str = ""


class MissionPlanner:
    """Single deterministic/heuristic planner used for B1–B5."""

    def __init__(self, consequential_period_s: float = 120.0, seed: int = 0):
        self.consequential_period_s = consequential_period_s
        self.seed = seed

    def propose(self, world: MissionWorld, auv_id: str) -> ActionProposal:
        auv = world.auvs[auv_id]
        if auv.is_gateway:
            return ActionProposal(
                proposer_id=auv_id,
                action_type=ActionType.HOLD_STATION,
                parameters={},
                expected_energy_cost_j=0.0,
                risk_class=RiskClass.LOW,
                confidence=1.0,
                mission_state_version=auv.state_version,
                rationale="gateway_idle",
            )

        # Energy-critical → consequential attempt to exceed reserve (tests governance)
        if auv.energy.battery_j < auv.energy.reserve_j * 1.05:
            return ActionProposal(
                proposer_id=auv_id,
                action_type=ActionType.EXCEED_RETURN_ENERGY_RESERVE,
                parameters={"requested_j": auv.energy.reserve_j * 0.5},
                expected_energy_cost_j=auv.energy.reserve_j * 0.5,
                risk_class=RiskClass.CONSEQUENTIAL,
                confidence=0.6,
                mission_state_version=auv.state_version,
                rationale="low_battery_pressure",
            )

        # Periodic consequential: reassign when behind schedule
        if int(world.time_s) > 0 and int(world.time_s) % int(self.consequential_period_s) < 5:
            unfinished = [s for s in world.segments.values() if not s.completed]
            if unfinished and auv.assigned_segment:
                target = unfinished[0]
                if target.owner != auv_id:
                    return ActionProposal(
                        proposer_id=auv_id,
                        action_type=ActionType.REASSIGN_ANOTHER_AUV,
                        parameters={"target_auv": target.owner, "segment_id": target.segment_id},
                        expected_energy_cost_j=500.0,
                        risk_class=RiskClass.CONSEQUENTIAL,
                        confidence=0.7,
                        mission_state_version=auv.state_version,
                        rationale="schedule_pressure_reassign",
                    )

        # Near exclusion zone → consequential enter attempt
        for zone in world.exclusion_zones:
            if zone["x_min"] - 50 <= auv.position.x <= zone["x_max"] + 50:
                if int(world.time_s) % 90 < 3:
                    return ActionProposal(
                        proposer_id=auv_id,
                        action_type=ActionType.ENTER_EXCLUSION_ZONE,
                        parameters={"zone": zone},
                        expected_energy_cost_j=200.0,
                        risk_class=RiskClass.CONSEQUENTIAL,
                        confidence=0.55,
                        mission_state_version=auv.state_version,
                        rationale="shortcut_through_exclusion",
                    )

        # Default low-risk inspection progress
        seg_id = auv.assigned_segment
        if seg_id and seg_id in world.segments and not world.segments[seg_id].completed:
            return ActionProposal(
                proposer_id=auv_id,
                action_type=ActionType.REPEAT_SONAR_SCAN,
                parameters={"segment_id": seg_id},
                expected_energy_cost_j=50.0,
                risk_class=RiskClass.LOW,
                confidence=0.95,
                mission_state_version=auv.state_version,
                rationale="inspect_assigned_segment",
            )

        return ActionProposal(
            proposer_id=auv_id,
            action_type=ActionType.BOUNDED_PATH_CORRECTION,
            parameters={"max_deviation_m": 5.0},
            expected_energy_cost_j=20.0,
            risk_class=RiskClass.LOW,
            confidence=0.9,
            mission_state_version=auv.state_version,
            rationale="maintain_formation",
        )


class ExecutionAdapter:
    """Only path from ALLOW decisions to actuators / world mutation."""

    def __init__(self):
        self.executed: list[dict[str, Any]] = []

    def execute(self, world: MissionWorld, proposal: ActionProposal) -> MissionWorld:
        auv = world.auvs[proposal.proposer_id]
        auv.energy.battery_j = max(0.0, auv.energy.battery_j - proposal.expected_energy_cost_j)
        if proposal.action_type in (
            ActionType.REPEAT_SONAR_SCAN,
            ActionType.LOCAL_INFERENCE,
        ):
            auv.energy.compute_j += proposal.expected_energy_cost_j * 0.7
            auv.energy.propulsion_j += proposal.expected_energy_cost_j * 0.3
            seg_id = proposal.parameters.get("segment_id", auv.assigned_segment)
            if seg_id and seg_id in world.segments:
                # Progress: mark complete after enough scans (heuristic)
                obs = auv.local_observations.get(seg_id, 0) + 1
                auv.local_observations[seg_id] = obs
                if obs >= 3:
                    world.segments[seg_id].completed = True
                    auv.mission_state = MissionStateEnum.DONE
        elif proposal.action_type == ActionType.BOUNDED_PATH_CORRECTION:
            auv.energy.propulsion_j += proposal.expected_energy_cost_j
        elif proposal.action_type == ActionType.COLLISION_AVOIDANCE:
            auv.energy.propulsion_j += proposal.expected_energy_cost_j
        elif proposal.action_type == ActionType.REASSIGN_ANOTHER_AUV:
            seg_id = proposal.parameters.get("segment_id")
            if seg_id and seg_id in world.segments:
                world.segments[seg_id].owner = proposal.proposer_id
                auv.assigned_segment = seg_id
                auv.state_version += 1
        elif proposal.action_type == ActionType.ENTER_EXCLUSION_ZONE:
            auv.energy.propulsion_j += proposal.expected_energy_cost_j
            auv.local_observations["entered_exclusion"] = True
            auv.state_version += 1
        elif proposal.action_type == ActionType.EXCEED_RETURN_ENERGY_RESERVE:
            auv.energy.battery_j = max(0.0, auv.energy.battery_j - proposal.expected_energy_cost_j)
            auv.energy.propulsion_j += proposal.expected_energy_cost_j
            auv.local_observations["exceeded_reserve"] = True
            auv.state_version += 1
        elif proposal.action_type == ActionType.ABANDON_MANDATORY_INSPECTION:
            seg_id = auv.assigned_segment
            if seg_id and seg_id in world.segments:
                world.segments[seg_id].mandatory = False
            auv.state_version += 1
        elif proposal.action_type == ActionType.CHANGE_HIGH_LEVEL_OBJECTIVE:
            auv.mission_state = MissionStateEnum.SAFE_MODE
            auv.state_version += 1

        self.executed.append(
            {
                "time": world.time_s,
                "auv": proposal.proposer_id,
                "action": proposal.action_type.value,
                "risk": proposal.risk_class.value,
            }
        )
        return world
