"""Five governance baselines B1–B5 over identical channel traces.

B5 is NOT a single policy: three fixed variants are defined *a priori*
(before examining CGEA results):

  - B5_conservative: consequential only while CONNECTED + supervisor reachable
  - B5_nominal: connectivity-aware adaptive (allow under partition)
  - B5_permissive: allow consequential in essentially all connectivity states

These are adaptive-autonomy policies without capsules / independent governor /
provenance-gated reauthorization.
"""

from __future__ import annotations

import enum

from cgea.agent import ActionProposal, ExecutionAdapter, MissionPlanner
from cgea.governance import (
    AuthorityCapsule,
    ConnectivityClassifier,
    ConnectivityState,
    ExecutionGovernor,
    FreshnessPolicy,
    GovernorDecision,
    GovernorResult,
    ReasonCode,
)
from cgea.mission import RiskClass, EnergyState


class BaselineId(str, enum.Enum):
    B1_CENTRALIZED = "B1"
    B2_UNRESTRICTED = "B2"
    B3_STATIC_BOUNDED = "B3"
    B4_CGEA = "B4"
    B4_NO_FRESHNESS = "B4-NoFreshness"
    B4_FIXED_EXPIRY = "B4-FixedExpiry"
    B5_ADAPTIVE = "B5"  # alias → nominal
    B5_CONSERVATIVE = "B5_conservative"
    B5_NOMINAL = "B5_nominal"
    B5_PERMISSIVE = "B5_permissive"


class BaselineController:
    """Shared interface. Planner is identical; governance policy differs."""

    baseline_id: BaselineId

    def __init__(
        self,
        planner: MissionPlanner,
        adapter: ExecutionAdapter,
        freshness_policy: FreshnessPolicy | None = None,
    ):
        self.planner = planner
        self.adapter = adapter
        self.governor = ExecutionGovernor(freshness_policy=freshness_policy)
        self.classifier = ConnectivityClassifier()

    def decide(
        self,
        proposal: ActionProposal,
        connectivity: ConnectivityState,
        capsule: AuthorityCapsule,
        last_authority_update: float,
        now: float,
        energy: EnergyState,
        supervisor_reachable: bool,
        position: dict[str, float] | None = None,
        freshness_mode: str = "continuous",
        immediate_resume: bool = False,
        violates_frozen_risk: bool = False,
        conditional_ok: bool = False,
    ) -> GovernorResult:
        raise NotImplementedError


class B1Centralized(BaselineController):
    """Consequential actions require reachable supervisor."""

    baseline_id = BaselineId.B1_CENTRALIZED

    def decide(self, proposal, connectivity, capsule, last_authority_update, now, energy,
               supervisor_reachable, position=None, freshness_mode="continuous", immediate_resume=False,
               violates_frozen_risk=False, conditional_ok=False):
        if proposal.risk_class == RiskClass.LOW:
            return self.governor.decide(
                proposal, connectivity, capsule, last_authority_update, now, energy, position, freshness_mode
            )
        if not supervisor_reachable:
            return self.governor._log(
                GovernorDecision.DENY,
                ReasonCode.DENY_SUPERVISOR_UNREACHABLE,
                proposal,
                connectivity,
                None,
                capsule.capsule_id,
            )
        if connectivity == ConnectivityState.RECOVERING and not immediate_resume:
            return self.governor._log(
                GovernorDecision.DENY,
                ReasonCode.DENY_RECOVERING_CONSEQUENTIAL,
                proposal,
                connectivity,
                None,
                capsule.capsule_id,
            )
        return self.governor._log(
            GovernorDecision.ALLOW,
            ReasonCode.ALLOW_AUTHORIZED,
            proposal,
            connectivity,
            None,
            capsule.capsule_id,
        )


class B2Unrestricted(BaselineController):
    """Planner decisions execute directly during disconnection."""

    baseline_id = BaselineId.B2_UNRESTRICTED

    def decide(self, proposal, connectivity, capsule, last_authority_update, now, energy,
               supervisor_reachable, position=None, freshness_mode="continuous", immediate_resume=False,
               violates_frozen_risk=False, conditional_ok=False):
        return self.governor._log(
            GovernorDecision.ALLOW,
            ReasonCode.ALLOW_AUTHORIZED,
            proposal,
            connectivity,
            None,
            None,
        )


class B3StaticBounded(BaselineController):
    """One fixed permission set for the entire mission."""

    baseline_id = BaselineId.B3_STATIC_BOUNDED

    def __init__(self, *args, static_allowed: set[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        from cgea.mission import LOW_RISK_ACTIONS

        self.static_allowed = static_allowed or {a.value for a in LOW_RISK_ACTIONS}

    def decide(self, proposal, connectivity, capsule, last_authority_update, now, energy,
               supervisor_reachable, position=None, freshness_mode="continuous", immediate_resume=False,
               violates_frozen_risk=False, conditional_ok=False):
        if proposal.action_type.value in self.static_allowed:
            return self.governor._log(
                GovernorDecision.ALLOW,
                ReasonCode.ALLOW_LOW_RISK,
                proposal,
                connectivity,
                None,
                capsule.capsule_id,
            )
        return self.governor._log(
            GovernorDecision.DENY,
            ReasonCode.DENY_STATIC_BOUND,
            proposal,
            connectivity,
            None,
            capsule.capsule_id,
        )


class B4CGEA(BaselineController):
    """Independent execution governor + dynamic authority + freshness + capsules + reconciliation."""

    baseline_id = BaselineId.B4_CGEA

    def decide(self, proposal, connectivity, capsule, last_authority_update, now, energy,
               supervisor_reachable, position=None, freshness_mode="continuous", immediate_resume=False,
               violates_frozen_risk=False, conditional_ok=False):
        if immediate_resume and connectivity == ConnectivityState.RECOVERING:
            connectivity = ConnectivityState.CONNECTED
        return self.governor.decide(
            proposal,
            connectivity,
            capsule,
            last_authority_update,
            now,
            energy,
            position,
            freshness_mode,
            violates_frozen_risk=violates_frozen_risk,
            conditional_ok=conditional_ok,
        )


class B4NoFreshness(B4CGEA):
    """Frozen ablation b4_no_freshness_v1: same B4 governor and risk taxonomy.

    Authority age does not contract permissions. Hard-safety forbids and
    conditional reassignment stay active. Does not bypass the governor.
    """

    baseline_id = BaselineId.B4_NO_FRESHNESS
    ablation_variant = "b4_no_freshness_v1"

    def decide(self, proposal, connectivity, capsule, last_authority_update, now, energy,
               supervisor_reachable, position=None, freshness_mode="continuous", immediate_resume=False,
               violates_frozen_risk=False, conditional_ok=False):
        return super().decide(
            proposal,
            connectivity,
            capsule,
            last_authority_update,
            now,
            energy,
            supervisor_reachable,
            position,
            freshness_mode="disabled",
            immediate_resume=immediate_resume,
            violates_frozen_risk=violates_frozen_risk,
            conditional_ok=conditional_ok,
        )


class B4FixedExpiry(B4NoFreshness):
    """fixed_expiry_v1: same governor as B4-NoFreshness; reassignment only while age < TTL."""

    baseline_id = BaselineId.B4_FIXED_EXPIRY
    ablation_variant = "fixed_expiry_v1"
    lease_ttl_s: float = float("inf")

    def decide(self, proposal, connectivity, capsule, last_authority_update, now, energy,
               supervisor_reachable, position=None, freshness_mode="continuous", immediate_resume=False,
               violates_frozen_risk=False, conditional_ok=False):
        from cgea.governance import authority_age
        from cgea.mission import ActionType

        age = authority_age(now, last_authority_update)
        if (
            proposal.action_type.value == ActionType.REASSIGN_ANOTHER_AUV.value
            and age >= float(self.lease_ttl_s)
        ):
            return self.governor._log(
                GovernorDecision.DENY,
                ReasonCode.DENY_LEASE_EXPIRED,
                proposal,
                connectivity,
                None,
                capsule.capsule_id,
            )
        return super().decide(
            proposal,
            connectivity,
            capsule,
            last_authority_update,
            now,
            energy,
            supervisor_reachable,
            position,
            freshness_mode="disabled",
            immediate_resume=immediate_resume,
            violates_frozen_risk=violates_frozen_risk,
            conditional_ok=conditional_ok,
        )


class B5AdaptiveAutonomy(BaselineController):
    """Connectivity-aware adaptive autonomy (no capsule / no independent governor / no provenance)."""

    baseline_id = BaselineId.B5_NOMINAL
    variant: str = "nominal"

    def __init__(self, *args, variant: str = "nominal", **kwargs):
        super().__init__(*args, **kwargs)
        self.variant = variant
        if variant == "conservative":
            self.baseline_id = BaselineId.B5_CONSERVATIVE
        elif variant == "permissive":
            self.baseline_id = BaselineId.B5_PERMISSIVE
        else:
            self.baseline_id = BaselineId.B5_NOMINAL

    def decide(self, proposal, connectivity, capsule, last_authority_update, now, energy,
               supervisor_reachable, position=None, freshness_mode="continuous", immediate_resume=False,
               violates_frozen_risk=False, conditional_ok=False):
        if proposal.risk_class == RiskClass.LOW:
            return self.governor._log(
                GovernorDecision.ALLOW,
                ReasonCode.ALLOW_LOW_RISK,
                proposal,
                connectivity,
                None,
                None,
            )

        v = self.variant

        # --- conservative: consequential only under CONNECTED + supervisor ---
        if v == "conservative":
            if connectivity == ConnectivityState.CONNECTED and supervisor_reachable:
                return self.governor._log(
                    GovernorDecision.ALLOW, ReasonCode.ALLOW_AUTHORIZED, proposal, connectivity, None, None
                )
            if connectivity == ConnectivityState.DEGRADED:
                return self.governor._log(
                    GovernorDecision.DEFER, ReasonCode.DEFER_DEGRADED, proposal, connectivity, None, None
                )
            return self.governor._log(
                GovernorDecision.DENY, ReasonCode.DENY_CONNECTIVITY_POLICY, proposal, connectivity, None, None
            )

        # --- permissive: allow consequential in essentially all states ---
        if v == "permissive":
            return self.governor._log(
                GovernorDecision.ALLOW, ReasonCode.ALLOW_AUTHORIZED, proposal, connectivity, None, None
            )

        # --- nominal (default adaptive autonomy) ---
        if connectivity == ConnectivityState.CONNECTED:
            return self.governor._log(
                GovernorDecision.ALLOW, ReasonCode.ALLOW_AUTHORIZED, proposal, connectivity, None, None
            )
        if connectivity == ConnectivityState.DEGRADED:
            if supervisor_reachable:
                return self.governor._log(
                    GovernorDecision.ALLOW, ReasonCode.ALLOW_AUTHORIZED, proposal, connectivity, None, None
                )
            return self.governor._log(
                GovernorDecision.DEFER, ReasonCode.DEFER_DEGRADED, proposal, connectivity, None, None
            )
        if connectivity in (ConnectivityState.PARTITIONED, ConnectivityState.ISOLATED):
            return self.governor._log(
                GovernorDecision.ALLOW, ReasonCode.ALLOW_AUTHORIZED, proposal, connectivity, None, None
            )
        if connectivity == ConnectivityState.RECOVERING:
            return self.governor._log(
                GovernorDecision.ALLOW, ReasonCode.ALLOW_AUTHORIZED, proposal, connectivity, None, None
            )
        return self.governor._log(
            GovernorDecision.DENY, ReasonCode.DENY_CONNECTIVITY_POLICY, proposal, connectivity, None, None
        )


def make_baseline(baseline: str | BaselineId, planner: MissionPlanner, adapter: ExecutionAdapter) -> BaselineController:
    key = baseline.value if isinstance(baseline, BaselineId) else str(baseline)
    if key in ("B5", "B5_nominal"):
        return B5AdaptiveAutonomy(planner, adapter, variant="nominal")
    if key == "B5_conservative":
        return B5AdaptiveAutonomy(planner, adapter, variant="conservative")
    if key == "B5_permissive":
        return B5AdaptiveAutonomy(planner, adapter, variant="permissive")

    if key in ("B4-NoFreshness", "B4_NoFreshness", "B4_no_freshness"):
        return B4NoFreshness(planner, adapter)
    if key in ("B4-FixedExpiry", "B4_FixedExpiry", "B4_fixed_expiry"):
        return B4FixedExpiry(planner, adapter)

    mapping = {
        "B1": B1Centralized,
        "B2": B2Unrestricted,
        "B3": B3StaticBounded,
        "B4": B4CGEA,
    }
    if key not in mapping:
        raise KeyError(f"Unknown baseline {key}")
    return mapping[key](planner, adapter)
