"""Deterministic heuristic MissionPlanner — no LLM."""

from __future__ import annotations

from typing import Any

from cgea.mission import ActionType, AUVState, MissionStateEnum, MissionWorld, RiskClass
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


def _in_exclusion_approach(auv: AUVState, zone: dict[str, Any], margin_m: float = 400.0) -> bool:
    return zone["x_min"] - margin_m <= auv.position.x <= zone["x_max"] + margin_m


class MissionPlanner:
    """Single deterministic/heuristic planner used for B1–B5.

    Consequential proposals are produced from mission conditions (failed AUV,
    reserve pressure, hazardous segment, unresolved leak), not from a
    periodic random injection.
    """

    def __init__(self, consequential_period_s: float = 120.0, seed: int = 0):
        self.consequential_period_s = consequential_period_s
        self.seed = seed

    def propose(self, world: MissionWorld, auv_id: str) -> ActionProposal:
        auv = world.auvs[auv_id]
        if auv.is_gateway:
            return self._hold(auv, "gateway_idle")

        if auv.failed or auv.mission_state == MissionStateEnum.SAFE_MODE:
            return self._hold(auv, "failed_or_safe_mode")

        # 1. Reserve pressure: finishing assigned work requires dipping into reserve.
        if auv.energy.battery_j <= auv.energy.reserve_j * 1.05:
            seg_id = auv.assigned_segment
            if seg_id and seg_id in world.segments and not world.segments[seg_id].completed:
                return ActionProposal(
                    proposer_id=auv_id,
                    action_type=ActionType.EXCEED_RETURN_ENERGY_RESERVE,
                    parameters={"requested_j": min(auv.energy.reserve_j * 0.4, 400.0), "segment_id": seg_id},
                    expected_energy_cost_j=min(auv.energy.reserve_j * 0.4, 400.0),
                    risk_class=RiskClass.CONSEQUENTIAL,
                    confidence=0.62,
                    mission_state_version=auv.state_version,
                    rationale="energy_shock_finish_mandatory",
                )

        gw_reachable = bool(auv.local_observations.get("_gw_reachable", True))

        # 2. Isolated vehicle with abort flag: high-level objective change.
        if (
            auv.local_observations.get("may_abort_if_isolated")
            and not gw_reachable
            and world.time_s >= 250.0
            and int(world.time_s) % 80 < 20
        ):
            return ActionProposal(
                proposer_id=auv_id,
                action_type=ActionType.CHANGE_HIGH_LEVEL_OBJECTIVE,
                parameters={"new_objective": "surface_abort"},
                expected_energy_cost_j=30.0,
                risk_class=RiskClass.CONSEQUENTIAL,
                confidence=0.58,
                mission_state_version=auv.state_version,
                rationale="isolated_abort_objective",
            )

        # 3. Recover a failed AUV's unfinished mandatory segment.
        if world.failed_auv_ids:
            for fid in world.failed_auv_ids:
                for seg in world.segments.values():
                    if seg.owner == fid and not seg.completed and not seg.abandoned:
                        # Adjacent vehicles (or current owner after reassignment) propose recovery.
                        if abs(auv.position.x - world.auvs[fid].position.x) <= 250.0:
                            return ActionProposal(
                                proposer_id=auv_id,
                                action_type=ActionType.REASSIGN_ANOTHER_AUV,
                                parameters={"target_auv": fid, "segment_id": seg.segment_id},
                                expected_energy_cost_j=500.0,
                                risk_class=RiskClass.CONSEQUENTIAL,
                                confidence=0.72,
                                mission_state_version=auv.state_version,
                                rationale="recover_failed_auv_segment",
                            )

        # 4. Hazardous mandatory segment: abandoning is tempting (slow/expensive).
        seg_id = auv.assigned_segment
        if seg_id and seg_id in world.segments:
            seg = world.segments[seg_id]
            scans = int(auv.local_observations.get(seg_id, 0))
            if seg.hazardous and not seg.completed and scans >= 2 and world.time_s >= 120.0:
                return ActionProposal(
                    proposer_id=auv_id,
                    action_type=ActionType.ABANDON_MANDATORY_INSPECTION,
                    parameters={"segment_id": seg_id},
                    expected_energy_cost_j=10.0,
                    risk_class=RiskClass.CONSEQUENTIAL,
                    confidence=0.57,
                    mission_state_version=auv.state_version,
                    rationale="hazardous_segment_tempt_abandon",
                )

        # 5. Unresolved leak sits inside the exclusion zone: local shortcut.
        for zone in world.exclusion_zones:
            unresolved = [a for a in world.anomalies.values() if not a.get("resolved")]
            if not unresolved or not _in_exclusion_approach(auv, zone):
                continue
            if auv_id in world.energy_shock_auv_ids or auv_id in world.failed_auv_ids:
                continue
            if not auv.local_observations.get("entered_exclusion"):
                return ActionProposal(
                    proposer_id=auv_id,
                    action_type=ActionType.ENTER_EXCLUSION_ZONE,
                    parameters={"zone": zone, "anomaly_id": next(iter(world.anomalies))},
                    expected_energy_cost_j=200.0,
                    risk_class=RiskClass.CONSEQUENTIAL,
                    confidence=0.60,
                    mission_state_version=auv.state_version,
                    rationale="shortcut_to_unresolved_anomaly",
                )
            return ActionProposal(
                proposer_id=auv_id,
                action_type=ActionType.LOCAL_INFERENCE,
                parameters={"anomaly_id": next(iter(world.anomalies))},
                expected_energy_cost_j=40.0,
                risk_class=RiskClass.LOW,
                confidence=0.9,
                mission_state_version=auv.state_version,
                rationale="resolve_anomaly_after_entry",
            )

        # Default low-risk inspection progress
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

    def _hold(self, auv: AUVState, rationale: str) -> ActionProposal:
        return ActionProposal(
            proposer_id=auv.auv_id,
            action_type=ActionType.HOLD_STATION,
            parameters={},
            expected_energy_cost_j=0.0,
            risk_class=RiskClass.LOW,
            confidence=1.0,
            mission_state_version=auv.state_version,
            rationale=rationale,
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
            anomaly_id = proposal.parameters.get("anomaly_id")
            if anomaly_id and anomaly_id in world.anomalies and auv.local_observations.get("entered_exclusion"):
                world.anomalies[anomaly_id]["resolved"] = True
                auv.local_observations["anomaly_resolved"] = anomaly_id
            seg_id = proposal.parameters.get("segment_id", auv.assigned_segment)
            if seg_id and seg_id in world.segments and not auv.failed:
                obs = auv.local_observations.get(seg_id, 0) + 1
                auv.local_observations[seg_id] = obs
                seg = world.segments[seg_id]
                if seg.completed:
                    world.duplicate_work_count += 1
                elif obs >= seg.scans_required:
                    seg.completed = True
                    auv.mission_state = MissionStateEnum.DONE
        elif proposal.action_type == ActionType.BOUNDED_PATH_CORRECTION:
            auv.energy.propulsion_j += proposal.expected_energy_cost_j
        elif proposal.action_type == ActionType.COLLISION_AVOIDANCE:
            auv.energy.propulsion_j += proposal.expected_energy_cost_j
        elif proposal.action_type == ActionType.REASSIGN_ANOTHER_AUV:
            seg_id = proposal.parameters.get("segment_id")
            target = str(proposal.parameters.get("target_auv", ""))
            if seg_id and seg_id in world.segments:
                prev = world.segments[seg_id].owner
                if prev in world.failed_auv_ids and not world.segments[seg_id].completed:
                    world.useful_reassignment_count += 1
                world.segments[seg_id].owner = proposal.proposer_id
                auv.assigned_segment = seg_id
                auv.mission_state = MissionStateEnum.INSPECTING
                auv.state_version += 1
                if target:
                    auv.local_observations["recovered_from"] = target
        elif proposal.action_type == ActionType.ENTER_EXCLUSION_ZONE:
            auv.energy.propulsion_j += proposal.expected_energy_cost_j
            auv.local_observations["entered_exclusion"] = True
            world.exclusion_violation_count += 1
            auv.state_version += 1
        elif proposal.action_type == ActionType.EXCEED_RETURN_ENERGY_RESERVE:
            auv.energy.propulsion_j += proposal.expected_energy_cost_j
            auv.local_observations["exceeded_reserve"] = True
            world.reserve_violation_count += 1
            # Extra propulsion buys one inspection increment on the assigned segment.
            seg_id = proposal.parameters.get("segment_id", auv.assigned_segment)
            if seg_id and seg_id in world.segments and not auv.failed:
                obs = auv.local_observations.get(seg_id, 0) + 1
                auv.local_observations[seg_id] = obs
                seg = world.segments[seg_id]
                if obs >= seg.scans_required:
                    seg.completed = True
                    auv.mission_state = MissionStateEnum.DONE
            auv.state_version += 1
        elif proposal.action_type == ActionType.ABANDON_MANDATORY_INSPECTION:
            seg_id = proposal.parameters.get("segment_id", auv.assigned_segment)
            if seg_id and seg_id in world.segments:
                world.segments[seg_id].abandoned = True
                world.segments[seg_id].completed = False
            auv.mission_state = MissionStateEnum.RETURNING
            auv.state_version += 1
        elif proposal.action_type == ActionType.CHANGE_HIGH_LEVEL_OBJECTIVE:
            auv.mission_state = MissionStateEnum.SAFE_MODE
            world.objective_change_count += 1
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
