"""Five governance baselines B1–B5 over identical channel traces."""

from __future__ import annotations

import enum
from typing import Any

from cgea.agent import ActionProposal, ExecutionAdapter, MissionPlanner
from cgea.governance import (
    AuthorityCapsule,
    AuthorityFreshness,
    ConnectivityClassifier,
    ConnectivityMetrics,
    ConnectivityState,
    ExecutionGovernor,
    FreshnessPolicy,
    GovernorDecision,
    GovernorResult,
    ReasonCode,
    contract_capsule,
    issue_capsule,
    classify_freshness,
    authority_age,
)
from cgea.mission import CONSEQUENTIAL_ACTIONS, RiskClass, EnergyState


class BaselineId(str, enum.Enum):
    B1_CENTRALIZED = "B1"
    B2_UNRESTRICTED = "B2"
    B3_STATIC_BOUNDED = "B3"
    B4_CGEA = "B4"
    B5_ADAPTIVE = "B5"


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
    ) -> GovernorResult:
        raise NotImplementedError


class B1Centralized(BaselineController):
    """Consequential actions require reachable supervisor."""

    baseline_id = BaselineId.B1_CENTRALIZED

    def decide(self, proposal, connectivity, capsule, last_authority_update, now, energy,
               supervisor_reachable, position=None, freshness_mode="continuous", immediate_resume=False):
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
               supervisor_reachable, position=None, freshness_mode="continuous", immediate_resume=False):
        # Always allow — no governor gating
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
               supervisor_reachable, position=None, freshness_mode="continuous", immediate_resume=False):
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
               supervisor_reachable, position=None, freshness_mode="continuous", immediate_resume=False):
        if immediate_resume and connectivity == ConnectivityState.RECOVERING:
            # Experimental ablation path
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
        )


class B5AdaptiveAutonomy(BaselineController):
    """Connectivity-aware adaptive autonomy WITHOUT independent governor / capsules / provenance reauth.

    Decision independence changes with connectivity, but:
      - no independent execution governor
      - no execution-authority capsule
      - no provenance-gated reauthorization
    """

    baseline_id = BaselineId.B5_ADAPTIVE

    def decide(self, proposal, connectivity, capsule, last_authority_update, now, energy,
               supervisor_reachable, position=None, freshness_mode="continuous", immediate_resume=False):
        # Adaptive policy table — NOT the CGEA governor path
        if proposal.risk_class == RiskClass.LOW:
            return self.governor._log(
                GovernorDecision.ALLOW,
                ReasonCode.ALLOW_LOW_RISK,
                proposal,
                connectivity,
                None,
                None,
            )

        if connectivity == ConnectivityState.CONNECTED:
            return self.governor._log(
                GovernorDecision.ALLOW,
                ReasonCode.ALLOW_AUTHORIZED,
                proposal,
                connectivity,
                None,
                None,
            )
        if connectivity == ConnectivityState.DEGRADED:
            # Allow consequential with lower confidence bar only if supervisor recently seen —
            # but no capsule / governor / provenance
            if supervisor_reachable:
                return self.governor._log(
                    GovernorDecision.ALLOW,
                    ReasonCode.ALLOW_AUTHORIZED,
                    proposal,
                    connectivity,
                    None,
                    None,
                )
            return self.governor._log(
                GovernorDecision.DEFER,
                ReasonCode.DEFER_DEGRADED,
                proposal,
                connectivity,
                None,
                None,
            )
        if connectivity in (ConnectivityState.PARTITIONED, ConnectivityState.ISOLATED):
            # Unrestricted local autonomy for consequential under partition (no capsule)
            return self.governor._log(
                GovernorDecision.ALLOW,
                ReasonCode.ALLOW_AUTHORIZED,
                proposal,
                connectivity,
                None,
                None,
            )
        if connectivity == ConnectivityState.RECOVERING:
            if immediate_resume:
                return self.governor._log(
                    GovernorDecision.ALLOW,
                    ReasonCode.ALLOW_AUTHORIZED,
                    proposal,
                    connectivity,
                    None,
                    None,
                )
            # B5 has no provenance-gated block — only soft defer
            return self.governor._log(
                GovernorDecision.ALLOW,
                ReasonCode.ALLOW_AUTHORIZED,
                proposal,
                connectivity,
                None,
                None,
            )
        return self.governor._log(
            GovernorDecision.DENY,
            ReasonCode.DENY_CONNECTIVITY_POLICY,
            proposal,
            connectivity,
            None,
            None,
        )


def make_baseline(baseline: str | BaselineId, planner: MissionPlanner, adapter: ExecutionAdapter) -> BaselineController:
    bid = BaselineId(baseline) if not isinstance(baseline, BaselineId) else baseline
    mapping = {
        BaselineId.B1_CENTRALIZED: B1Centralized,
        BaselineId.B2_UNRESTRICTED: B2Unrestricted,
        BaselineId.B3_STATIC_BOUNDED: B3StaticBounded,
        BaselineId.B4_CGEA: B4CGEA,
        BaselineId.B5_ADAPTIVE: B5AdaptiveAutonomy,
    }
    return mapping[bid](planner, adapter)
