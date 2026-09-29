"""CGEA governance: capsules, connectivity, freshness, governor, reconciliation."""

from __future__ import annotations

import enum
import hashlib
from typing import Any

from pydantic import Field

from cgea.agent import ActionProposal
from cgea.governance.semantic_conflicts import detect_semantic_conflicts
from cgea.mission import (
    CONSEQUENTIAL_ACTIONS,
    LOW_RISK_ACTIONS,
    ActionType,
    EnergyState,
    RiskClass,
)
from cgea.types import CgeaBaseModel


class ConnectivityState(str, enum.Enum):
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    PARTITIONED = "PARTITIONED"
    ISOLATED = "ISOLATED"
    RECOVERING = "RECOVERING"


class GovernorDecision(str, enum.Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    DEFER = "DEFER"


class ReasonCode(str, enum.Enum):
    ALLOW_LOW_RISK = "ALLOW_LOW_RISK"
    ALLOW_AUTHORIZED = "ALLOW_AUTHORIZED"
    DENY_FORBIDDEN = "DENY_FORBIDDEN"
    DENY_RISK_CEILING = "DENY_RISK_CEILING"
    DENY_CONFIDENCE = "DENY_CONFIDENCE"
    DENY_ENERGY = "DENY_ENERGY"
    DENY_GEOFENCE = "DENY_GEOFENCE"
    DENY_STALE_AUTHORITY = "DENY_STALE_AUTHORITY"
    DENY_HARD_EXPIRY = "DENY_HARD_EXPIRY"
    DENY_RECOVERING_CONSEQUENTIAL = "DENY_RECOVERING_CONSEQUENTIAL"
    DENY_RECOVERING_HARD_SAFETY = "DENY_RECOVERING_HARD_SAFETY"
    DEFER_RECOVERING_PENDING_REAUTH = "DEFER_RECOVERING_PENDING_REAUTH"
    DENY_SUPERVISOR_UNREACHABLE = "DENY_SUPERVISOR_UNREACHABLE"
    DENY_STATIC_BOUND = "DENY_STATIC_BOUND"
    DENY_CONNECTIVITY_POLICY = "DENY_CONNECTIVITY_POLICY"
    DEFER_PARTITION = "DEFER_PARTITION"
    DENY_CONDITIONAL_UNMET = "DENY_CONDITIONAL_UNMET"
    DENY_LEASE_EXPIRED = "DENY_LEASE_EXPIRED"
    DEFER_DEGRADED = "DEFER_DEGRADED"
    DEFER_AWAITING_SUPERVISOR = "DEFER_AWAITING_SUPERVISOR"


class AuthorityCapsule(CgeaBaseModel):
    capsule_id: str
    issuer: str
    subject: str
    mission_id: str
    issued_at: float

    soft_expiry: float
    hard_expiry: float

    allowed_actions: list[str]
    conditional_actions: list[str]
    forbidden_actions: list[str]

    geofence: dict[str, float] = Field(default_factory=dict)

    max_extra_energy: float = 1e4
    max_path_deviation: float = 20.0
    max_neighbor_recruits: int = 1

    risk_ceiling: str = "consequential"
    minimum_confidence: float = 0.5

    fallback_action: str = ActionType.HOLD_STATION.value

    def deterministic_hash(self) -> str:
        raw = self.model_dump_json()
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def reassign_condition_satisfied(
    proposal: ActionProposal,
    failed_auv_ids: list[str] | set[str],
    segment_mandatory: bool,
    segment_incomplete: bool,
    recruits_used: int,
    max_recruits: int,
) -> bool:
    """Frozen capsule condition for CONDITIONAL reassign. Not the evaluation oracle."""
    if proposal.action_type != ActionType.REASSIGN_ANOTHER_AUV:
        return False
    target = str(proposal.parameters.get("target_auv", ""))
    if target not in set(failed_auv_ids):
        return False
    if not segment_mandatory or not segment_incomplete:
        return False
    if recruits_used >= max(0, int(max_recruits)):
        return False
    return True


# Predeclared 2026-09-28. Not derived from the evaluation oracle.
# Replaces implicit broad=True issuance used in E1-v3 diagnostic.
PAPER_POLICY_VERSION = "paper_risk_bounded_v1_2026-09-28"
PAPER_CONDITIONAL_ACTIONS = (ActionType.REASSIGN_ANOTHER_AUV.value,)
PAPER_FORBIDDEN_ACTIONS = (
    ActionType.ENTER_EXCLUSION_ZONE.value,
    ActionType.EXCEED_RETURN_ENERGY_RESERVE.value,
    ActionType.ABANDON_MANDATORY_INSPECTION.value,
    ActionType.CHANGE_HIGH_LEVEL_OBJECTIVE.value,
)


def issue_capsule(
    subject: str,
    mission_id: str,
    now: float,
    issuer: str = "gw0",
    soft_horizon_s: float = 300.0,
    hard_horizon_s: float = 900.0,
    broad: bool = False,
    grants: dict[str, list[str]] | None = None,
) -> AuthorityCapsule:
    low = [a.value for a in LOW_RISK_ACTIONS]
    cons = [a.value for a in CONSEQUENTIAL_ACTIONS]
    if broad:
        allowed = low + cons
        conditional: list[str] = []
        forbidden: list[str] = []
    else:
        g = grants or {
            "conditional": list(PAPER_CONDITIONAL_ACTIONS),
            "forbid": list(PAPER_FORBIDDEN_ACTIONS),
        }
        forbidden = list(g.get("forbid", list(PAPER_FORBIDDEN_ACTIONS)))
        conditional = list(g.get("conditional", list(PAPER_CONDITIONAL_ACTIONS)))
        allowed = list(low) + [a for a in conditional if a not in forbidden]

    cap = AuthorityCapsule(
        capsule_id="",
        issuer=issuer,
        subject=subject,
        mission_id=mission_id,
        issued_at=now,
        soft_expiry=now + soft_horizon_s,
        hard_expiry=now + hard_horizon_s,
        allowed_actions=allowed,
        conditional_actions=conditional,
        forbidden_actions=forbidden,
        geofence={"x_min": -100.0, "x_max": 3000.0, "y_min": -200.0, "y_max": 200.0},
        fallback_action=ActionType.SURFACING_SAFE_MODE.value,
    )
    cap.capsule_id = f"cap_{cap.deterministic_hash()}"
    return cap


class AuthorityFreshness(str, enum.Enum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    HARD_EXPIRED = "hard_expired"


class FreshnessPolicy(CgeaBaseModel):
    """Configurable authority contraction — shared across experiments."""

    aging_age_s: float = 180.0
    stale_age_s: float = 400.0
    # When aging: strip high-impact actions
    aging_remove_actions: list[str] = Field(
        default_factory=lambda: [
            ActionType.CHANGE_HIGH_LEVEL_OBJECTIVE.value,
            ActionType.ABANDON_MANDATORY_INSPECTION.value,
            ActionType.REASSIGN_ANOTHER_AUV.value,
        ]
    )
    # When stale: mission-safe only
    stale_allowed_actions: list[str] = Field(
        default_factory=lambda: [a.value for a in LOW_RISK_ACTIONS]
    )


def authority_age(now: float, last_trusted_authority_update: float) -> float:
    return now - last_trusted_authority_update


def classify_freshness(age_s: float, capsule: AuthorityCapsule, policy: FreshnessPolicy) -> AuthorityFreshness:
    if age_s >= (capsule.hard_expiry - capsule.issued_at) or (capsule.issued_at + age_s) >= capsule.hard_expiry:
        # Compare absolute time
        pass
    # Use absolute now = issued_at + age for expiry checks in caller; here age-based bands:
    if age_s >= policy.stale_age_s:
        # hard expiry checked separately via absolute time
        return AuthorityFreshness.STALE
    if age_s >= policy.aging_age_s:
        return AuthorityFreshness.AGING
    return AuthorityFreshness.FRESH


def contract_capsule(
    capsule: AuthorityCapsule,
    freshness: AuthorityFreshness,
    policy: FreshnessPolicy,
    now: float,
) -> AuthorityCapsule:
    """Apply contraction policy. Does not mutate original."""
    data = capsule.model_dump()
    if now >= capsule.hard_expiry or freshness == AuthorityFreshness.HARD_EXPIRED:
        data["allowed_actions"] = [capsule.fallback_action]
        data["conditional_actions"] = []
        data["forbidden_actions"] = [a.value for a in ActionType if a.value != capsule.fallback_action]
        data["risk_ceiling"] = "low"
        out = AuthorityCapsule(**data)
        return out
    if freshness == AuthorityFreshness.STALE:
        data["allowed_actions"] = list(policy.stale_allowed_actions)
        data["conditional_actions"] = []
        data["forbidden_actions"] = [a for a in data["allowed_actions"] and []]
        # Forbid all consequential
        data["forbidden_actions"] = [a.value for a in CONSEQUENTIAL_ACTIONS]
        data["risk_ceiling"] = "low"
        return AuthorityCapsule(**data)
    if freshness == AuthorityFreshness.AGING:
        remove = set(policy.aging_remove_actions)
        data["allowed_actions"] = [a for a in capsule.allowed_actions if a not in remove]
        data["forbidden_actions"] = list(set(capsule.forbidden_actions) | remove)
        return AuthorityCapsule(**data)
    return capsule


class ConnectivityMetrics(CgeaBaseModel):
    packet_delivery_ratio: float = 1.0
    mean_delay_s: float = 0.0
    available_throughput_bps: float = 0.0
    time_since_supervisor_s: float = 0.0
    time_since_authority_update_s: float = 0.0
    neighbor_count: int = 0
    gateway_reachable: bool = True
    partition_size: int = 1
    local_population: int = 1


class ConnectivityClassifier(CgeaBaseModel):
    """Connectivity state from history — NOT SNR alone."""

    pdr_degraded: float = 0.7
    pdr_partition: float = 0.3
    supervisor_timeout_s: float = 120.0
    authority_timeout_s: float = 300.0
    min_throughput_bps: float = 100.0

    def classify(self, m: ConnectivityMetrics, recovering: bool = False) -> ConnectivityState:
        if recovering:
            return ConnectivityState.RECOVERING
        if m.neighbor_count == 0 and not m.gateway_reachable:
            return ConnectivityState.ISOLATED
        if not m.gateway_reachable or m.partition_size < m.local_population:
            return ConnectivityState.PARTITIONED
        if (
            m.packet_delivery_ratio < self.pdr_degraded
            or m.available_throughput_bps < self.min_throughput_bps
            or m.time_since_supervisor_s > self.supervisor_timeout_s
            or m.time_since_authority_update_s > self.authority_timeout_s * 0.5
        ):
            return ConnectivityState.DEGRADED
        return ConnectivityState.CONNECTED


class GovernorResult(CgeaBaseModel):
    decision: GovernorDecision
    reason_code: ReasonCode
    proposal: ActionProposal
    effective_capsule_id: str | None = None
    connectivity: ConnectivityState
    freshness: AuthorityFreshness | None = None
    detail: str = ""


class ExecutionGovernor:
    """Deterministic governor. No LLM. Agent cannot bypass this."""

    def __init__(self, freshness_policy: FreshnessPolicy | None = None):
        self.freshness_policy = freshness_policy or FreshnessPolicy()
        self.decision_log: list[GovernorResult] = []

    def decide(
        self,
        proposal: ActionProposal,
        connectivity: ConnectivityState,
        capsule: AuthorityCapsule,
        last_authority_update: float,
        now: float,
        energy: EnergyState,
        position: dict[str, float] | None = None,
        freshness_mode: str = "continuous",  # continuous | binary | disabled
        violates_frozen_risk: bool = False,
        conditional_ok: bool = False,
    ) -> GovernorResult:
        age = authority_age(now, last_authority_update)

        # Ablation b4_no_freshness_v1: skip all age/expiry contraction.
        # B4 continuous mode is unchanged (hard expiry still applies).
        if freshness_mode == "disabled":
            freshness = AuthorityFreshness.FRESH
        elif now >= capsule.hard_expiry:
            freshness = AuthorityFreshness.HARD_EXPIRED
        elif freshness_mode == "binary":
            freshness = (
                AuthorityFreshness.HARD_EXPIRED
                if now >= capsule.hard_expiry
                else AuthorityFreshness.FRESH
            )
        else:
            freshness = classify_freshness(age, capsule, self.freshness_policy)
            if now >= capsule.hard_expiry:
                freshness = AuthorityFreshness.HARD_EXPIRED

        eff = (
            capsule
            if freshness_mode == "disabled"
            else contract_capsule(capsule, freshness, self.freshness_policy, now)
        )

        action = proposal.action_type.value

        # RECOVERING: DENY hard-safety; DEFER useful pending reauthorization
        if connectivity == ConnectivityState.RECOVERING and proposal.risk_class == RiskClass.CONSEQUENTIAL:
            if violates_frozen_risk:
                return self._log(
                    GovernorDecision.DENY,
                    ReasonCode.DENY_RECOVERING_HARD_SAFETY,
                    proposal,
                    connectivity,
                    freshness,
                    eff.capsule_id,
                )
            return self._log(
                GovernorDecision.DEFER,
                ReasonCode.DEFER_RECOVERING_PENDING_REAUTH,
                proposal,
                connectivity,
                freshness,
                eff.capsule_id,
            )

        if freshness == AuthorityFreshness.HARD_EXPIRED and proposal.risk_class == RiskClass.CONSEQUENTIAL:
            return self._log(
                GovernorDecision.DENY,
                ReasonCode.DENY_HARD_EXPIRY,
                proposal,
                connectivity,
                freshness,
                eff.capsule_id,
            )

        if action in eff.forbidden_actions:
            return self._log(
                GovernorDecision.DENY,
                ReasonCode.DENY_FORBIDDEN,
                proposal,
                connectivity,
                freshness,
                eff.capsule_id,
            )

        if proposal.confidence < eff.minimum_confidence:
            return self._log(
                GovernorDecision.DENY,
                ReasonCode.DENY_CONFIDENCE,
                proposal,
                connectivity,
                freshness,
                eff.capsule_id,
            )

        if proposal.expected_energy_cost_j > eff.max_extra_energy:
            return self._log(
                GovernorDecision.DENY,
                ReasonCode.DENY_ENERGY,
                proposal,
                connectivity,
                freshness,
                eff.capsule_id,
            )

        if position and eff.geofence:
            x, y = position.get("x", 0.0), position.get("y", 0.0)
            if not (
                eff.geofence.get("x_min", -1e9) <= x <= eff.geofence.get("x_max", 1e9)
                and eff.geofence.get("y_min", -1e9) <= y <= eff.geofence.get("y_max", 1e9)
            ):
                return self._log(
                    GovernorDecision.DENY,
                    ReasonCode.DENY_GEOFENCE,
                    proposal,
                    connectivity,
                    freshness,
                    eff.capsule_id,
                )

        if proposal.risk_class == RiskClass.LOW:
            return self._log(
                GovernorDecision.ALLOW,
                ReasonCode.ALLOW_LOW_RISK,
                proposal,
                connectivity,
                freshness,
                eff.capsule_id,
            )

        # Consequential must be in allowed or conditional
        if action in eff.conditional_actions:
            if not conditional_ok:
                return self._log(
                    GovernorDecision.DENY,
                    ReasonCode.DENY_CONDITIONAL_UNMET,
                    proposal,
                    connectivity,
                    freshness,
                    eff.capsule_id,
                )
            return self._log(
                GovernorDecision.ALLOW,
                ReasonCode.ALLOW_AUTHORIZED,
                proposal,
                connectivity,
                freshness,
                eff.capsule_id,
            )
        if action in eff.allowed_actions:
            return self._log(
                GovernorDecision.ALLOW,
                ReasonCode.ALLOW_AUTHORIZED,
                proposal,
                connectivity,
                freshness,
                eff.capsule_id,
            )

        return self._log(
            GovernorDecision.DENY,
            ReasonCode.DENY_FORBIDDEN,
            proposal,
            connectivity,
            freshness,
            eff.capsule_id,
        )

    def _log(
        self,
        decision: GovernorDecision,
        reason: ReasonCode,
        proposal: ActionProposal,
        connectivity: ConnectivityState,
        freshness: AuthorityFreshness | None,
        capsule_id: str | None,
    ) -> GovernorResult:
        result = GovernorResult(
            decision=decision,
            reason_code=reason,
            proposal=proposal,
            effective_capsule_id=capsule_id,
            connectivity=connectivity,
            freshness=freshness,
        )
        self.decision_log.append(result)
        return result


# --- Reconciliation ---


class NodeJournalEntry(CgeaBaseModel):
    time: float
    node_id: str
    actions_proposed: list[str] = Field(default_factory=list)
    actions_executed: list[str] = Field(default_factory=list)
    executed_params: list[dict[str, Any]] = Field(default_factory=list)
    capsule_id: str | None = None
    energy_battery_j: float = 0.0
    task_ownership: dict[str, str] = Field(default_factory=dict)
    observations: dict[str, Any] = Field(default_factory=dict)
    state_version: int = 0


class Digest(CgeaBaseModel):
    node_id: str
    state_version: int
    ownership_hash: str
    energy_hash: str
    action_count: int


def make_digest(node_id: str, entries: list[NodeJournalEntry]) -> Digest:
    latest = entries[-1] if entries else None
    ownership = latest.task_ownership if latest else {}
    energy = latest.energy_battery_j if latest else 0.0
    return Digest(
        node_id=node_id,
        state_version=latest.state_version if latest else 0,
        ownership_hash=hashlib.sha256(str(sorted(ownership.items())).encode()).hexdigest()[:12],
        energy_hash=hashlib.sha256(f"{energy:.3f}".encode()).hexdigest()[:12],
        action_count=sum(len(e.actions_executed) for e in entries),
    )


class ReconciliationResult(CgeaBaseModel):
    conflicts: int
    validated_actions: int
    rejected_actions: int
    latency_s: float
    new_capsule_ids: list[str] = Field(default_factory=list)
    immediate_resume: bool = False
    by_kind: dict[str, int] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)


def reconcile_partitions(
    digests: list[Digest],
    journals: dict[str, list[NodeJournalEntry]],
    now: float,
    immediate_resume: bool = False,
    failed_ids: list[str] | None = None,
    duplicate_work_count: int = 0,
) -> ReconciliationResult:
    """Semantic validation. Capsule presence is not a conflict."""
    if immediate_resume:
        return ReconciliationResult(
            conflicts=0,
            validated_actions=sum(d.action_count for d in digests),
            rejected_actions=0,
            latency_s=0.0,
            immediate_resume=True,
        )

    sem = detect_semantic_conflicts(
        journals,
        failed_ids=failed_ids,
        duplicate_work_count=duplicate_work_count,
    )
    validated = 0
    rejected = 0
    for _nid, entries in journals.items():
        for e in entries:
            for _act in e.actions_executed:
                validated += 1
    rejected = int(sem["semantic_conflicts_per_mission"])
    return ReconciliationResult(
        conflicts=int(sem["semantic_conflicts_per_mission"]),
        validated_actions=validated,
        rejected_actions=rejected,
        latency_s=0.0,
        immediate_resume=False,
        by_kind=dict(sem["by_kind"]),
        events=list(sem["events"]),
    )
