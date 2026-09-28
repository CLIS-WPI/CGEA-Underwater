"""Post-hoc / scenario-truth oracle for consequential proposals.

Frozen together with utility_v2. Not derived from CGEA decisions.
A denied consequential action is a false denial iff it is mission-beneficial
AND does not violate the frozen risk constraints.
"""

from __future__ import annotations

from cgea.agent import ActionProposal
from cgea.mission import ActionType, MissionWorld
from cgea.types import CgeaBaseModel

# Frozen: which consequential classes violate hard mission-safety constraints.
VIOLATES_FROZEN_RISK: dict[str, bool] = {
    ActionType.ENTER_EXCLUSION_ZONE.value: True,
    ActionType.EXCEED_RETURN_ENERGY_RESERVE.value: True,
    ActionType.ABANDON_MANDATORY_INSPECTION.value: True,
    ActionType.CHANGE_HIGH_LEVEL_OBJECTIVE.value: True,
    ActionType.REASSIGN_ANOTHER_AUV.value: False,
}


class OracleLabel(CgeaBaseModel):
    mission_beneficial: bool
    violates_frozen_risk: bool
    reason: str = ""

    @property
    def false_denial_if_denied(self) -> bool:
        return self.mission_beneficial and not self.violates_frozen_risk


def oracle_label(proposal: ActionProposal, world: MissionWorld) -> OracleLabel:
    at = proposal.action_type
    auv = world.auvs.get(proposal.proposer_id)

    if at == ActionType.REASSIGN_ANOTHER_AUV:
        target = str(proposal.parameters.get("target_auv", ""))
        seg_id = proposal.parameters.get("segment_id")
        failed = target in world.failed_auv_ids
        incomplete = bool(seg_id) and seg_id in world.segments and not world.segments[seg_id].completed
        beneficial = failed and incomplete
        return OracleLabel(
            mission_beneficial=beneficial,
            violates_frozen_risk=VIOLATES_FROZEN_RISK[at.value],
            reason="reassign_failed_auv_mandatory" if beneficial else "reassign_not_recovery",
        )

    if at == ActionType.ENTER_EXCLUSION_ZONE:
        unresolved = any(not a.get("resolved") for a in world.anomalies.values())
        return OracleLabel(
            mission_beneficial=unresolved,
            violates_frozen_risk=True,
            reason="anomaly_behind_exclusion" if unresolved else "no_unresolved_anomaly",
        )

    if at == ActionType.EXCEED_RETURN_ENERGY_RESERVE:
        seg_id = auv.assigned_segment if auv else None
        incomplete = bool(seg_id) and seg_id in world.segments and not world.segments[seg_id].completed
        pressure = bool(auv) and auv.energy.battery_j <= auv.energy.reserve_j * 1.05
        beneficial = incomplete and pressure
        return OracleLabel(
            mission_beneficial=beneficial,
            violates_frozen_risk=True,
            reason="reserve_needed_to_finish_mandatory" if beneficial else "reserve_not_needed",
        )

    if at == ActionType.ABANDON_MANDATORY_INSPECTION:
        return OracleLabel(
            mission_beneficial=False,
            violates_frozen_risk=True,
            reason="abandon_mandatory_never_oracle_beneficial",
        )

    if at == ActionType.CHANGE_HIGH_LEVEL_OBJECTIVE:
        return OracleLabel(
            mission_beneficial=False,
            violates_frozen_risk=True,
            reason="objective_change_not_mission_beneficial",
        )

    return OracleLabel(mission_beneficial=False, violates_frozen_risk=False, reason="not_consequential")
