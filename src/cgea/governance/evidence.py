"""Action-dependent evidence authority (E4 / B3). Design SHA 4d2df2e.

Does not apply CGEA 180/400/900 contraction. Does not read MissionWorld.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf

from cgea.agent import ActionProposal
from cgea.types import CgeaBaseModel

EXPECTED_POLICY_ID = "action_evidence_policy_v1"
POLICY_PATH_DEFAULT = Path(__file__).resolve().parents[3] / "configs" / "governance" / "action_evidence_policy_v1.yaml"

# Frozen engineering constants (must match YAML).
FROZEN_BUDGETS_S = {
    "LOCAL_NAVIGATION": 40.0,
    "LOCAL_OBSERVATION": 60.0,
    "LOCAL_ENERGY": 60.0,
    "PEER_AVAILABILITY": 300.0,
    "SEGMENT_ASSIGNMENT": 600.0,
}


class EvidenceType(str, enum.Enum):
    LOCAL_OBSERVATION = "LOCAL_OBSERVATION"
    LOCAL_ENERGY = "LOCAL_ENERGY"
    LOCAL_NAVIGATION = "LOCAL_NAVIGATION"
    PEER_AVAILABILITY = "PEER_AVAILABILITY"
    SEGMENT_ASSIGNMENT = "SEGMENT_ASSIGNMENT"
    MISSION_OBJECTIVE = "MISSION_OBJECTIVE"
    SUPERVISOR_AUTHORITY = "SUPERVISOR_AUTHORITY"


LOCAL_TYPES = frozenset(
    {
        EvidenceType.LOCAL_OBSERVATION,
        EvidenceType.LOCAL_ENERGY,
        EvidenceType.LOCAL_NAVIGATION,
    }
)
REMOTE_TYPES = frozenset(
    {
        EvidenceType.PEER_AVAILABILITY,
        EvidenceType.SEGMENT_ASSIGNMENT,
        EvidenceType.MISSION_OBJECTIVE,
        EvidenceType.SUPERVISOR_AUTHORITY,
    }
)


class EvidenceRecord(CgeaBaseModel):
    evidence_type: EvidenceType
    value: dict[str, Any]
    source: str
    observed_at: float
    trusted_at: float
    object_id: str
    version: int
    confidence: float | None = None
    scope: str | None = None
    provenance_id: str | None = None


class EvidenceCheck(CgeaBaseModel):
    evidence_type: str
    object_id: str
    found: bool
    age_s: float | None = None
    max_age_s: float | None = None
    age_valid: bool = False
    predicate_valid: bool = False
    source: str | None = None
    version: int | None = None
    conflict: bool = False


class EvidenceEvaluation(CgeaBaseModel):
    passed: bool
    reason_code: str
    evidence_checks: list[EvidenceCheck] = []
    evidence_gaps: list[dict[str, Any]] = []
    evidence_ages: dict[str, float] = {}
    predicates_evaluated: dict[str, bool] = {}


@dataclass
class ActionEvidencePolicy:
    policy_id: str
    budgets_s: dict[str, float | None]
    actions: dict[str, Any]
    apply_cgea_multistage_contraction: bool
    apply_capsule_hard_expiry_deny: bool
    age_reference: str = "trusted_at"


def evidence_age(record: EvidenceRecord, now: float) -> float:
    return float(now) - float(record.trusted_at)


def _truthy(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return bool(val)


def eval_value_predicate(expr: str, value: dict[str, Any]) -> bool:
    """Restricted predicates: value.FIELD == true/false, optionally AND-chained."""
    parts = [p.strip() for p in expr.split(" and ")]
    for part in parts:
        if "==" not in part:
            return False
        lhs, rhs = [x.strip() for x in part.split("==", 1)]
        if not lhs.startswith("value."):
            return False
        key = lhs[len("value.") :]
        want = rhs.lower() == "true"
        if _truthy(value.get(key)) != want:
            return False
    return True


class EvidenceStore:
    """Per-AUV local store. Keyed by (evidence_type, object_id)."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], EvidenceRecord] = {}
        self._conflicts: set[tuple[str, str]] = set()
        self._invalid_reasons: dict[tuple[str, str], str] = {}

    def put(self, record: EvidenceRecord) -> None:
        key = (record.evidence_type.value, record.object_id)
        existing = self._records.get(key)
        if existing is None:
            self._records[key] = record
            self._conflicts.discard(key)
            self._invalid_reasons.pop(key, None)
            return
        if record.version > existing.version:
            self._records[key] = record
            self._conflicts.discard(key)
            self._invalid_reasons.pop(key, None)
            return
        if record.version < existing.version:
            return
        if record.trusted_at > existing.trusted_at:
            self._records[key] = record
            self._conflicts.discard(key)
            self._invalid_reasons.pop(key, None)
            return
        if record.trusted_at < existing.trusted_at:
            return
        self._conflicts.add(key)
        self._invalid_reasons[key] = "version_trusted_at_tie"

    def get_latest(self, evidence_type: EvidenceType | str, object_id: str) -> EvidenceRecord | None:
        et = evidence_type.value if isinstance(evidence_type, EvidenceType) else str(evidence_type)
        return self._records.get((et, object_id))

    def age(self, record: EvidenceRecord, now: float) -> float:
        return evidence_age(record, now)

    def is_conflict(self, evidence_type: EvidenceType | str, object_id: str) -> bool:
        et = evidence_type.value if isinstance(evidence_type, EvidenceType) else str(evidence_type)
        return (et, object_id) in self._conflicts

    def is_valid(self, requirement: dict[str, Any], now: float) -> EvidenceCheck:
        et = str(requirement["evidence_type"])
        oid = str(requirement["object_id"])
        max_age = requirement.get("max_age_s")
        pred = requirement.get("predicate")
        rec = self.get_latest(et, oid)
        if rec is None:
            return EvidenceCheck(
                evidence_type=et,
                object_id=oid,
                found=False,
                max_age_s=max_age,
            )
        conflict = self.is_conflict(et, oid)
        age = self.age(rec, now)
        age_ok = True if max_age is None else age <= float(max_age)
        pred_ok = True
        if pred:
            pred_ok = eval_value_predicate(str(pred), rec.value)
        if conflict:
            pred_ok = False
        return EvidenceCheck(
            evidence_type=et,
            object_id=oid,
            found=True,
            age_s=age,
            max_age_s=max_age,
            age_valid=age_ok and not conflict,
            predicate_valid=pred_ok,
            source=rec.source,
            version=rec.version,
            conflict=conflict,
        )

    def invalidate(self, evidence_type: EvidenceType | str, object_id: str, reason: str) -> None:
        et = evidence_type.value if isinstance(evidence_type, EvidenceType) else str(evidence_type)
        key = (et, object_id)
        self._records.pop(key, None)
        self._conflicts.discard(key)
        self._invalid_reasons[key] = reason

    def snapshot(self) -> dict[str, Any]:
        return {
            "records": [r.model_dump(mode="json") for r in self._records.values()],
            "conflicts": [list(k) for k in sorted(self._conflicts)],
            "invalid_reasons": {f"{k[0]}:{k[1]}": v for k, v in self._invalid_reasons.items()},
        }


def load_action_evidence_policy(path: Path | None = None) -> ActionEvidencePolicy:
    p = path or POLICY_PATH_DEFAULT
    raw = OmegaConf.to_container(OmegaConf.load(p), resolve=True)
    if not isinstance(raw, dict):
        raise ValueError("action evidence policy must be a mapping")
    pid = str(raw.get("id", ""))
    if pid != EXPECTED_POLICY_ID:
        raise ValueError(f"policy id {pid!r} != {EXPECTED_POLICY_ID!r}")
    contraction = bool(raw.get("apply_cgea_multistage_contraction", True))
    hard_deny = bool(raw.get("apply_capsule_hard_expiry_deny", True))
    if contraction is not False:
        raise ValueError("B3 requires apply_cgea_multistage_contraction == false")
    if hard_deny is not False:
        raise ValueError("B3 requires apply_capsule_hard_expiry_deny == false")
    budgets_raw = dict(raw.get("budgets_s") or {})
    budgets: dict[str, float | None] = {}
    for name in EvidenceType:
        if name.value not in budgets_raw:
            raise ValueError(f"missing budget for {name.value}")
        val = budgets_raw[name.value]
        if val is None:
            budgets[name.value] = None
        else:
            b = float(val)
            if b < 0:
                raise ValueError(f"negative budget {name.value}={b}")
            budgets[name.value] = b
    for k, expected in FROZEN_BUDGETS_S.items():
        got = budgets.get(k)
        if got is None or abs(float(got) - expected) > 1e-9:
            raise ValueError(f"frozen budget mismatch {k}: {got} != {expected}")
    return ActionEvidencePolicy(
        policy_id=pid,
        budgets_s=budgets,
        actions=dict(raw.get("actions") or {}),
        apply_cgea_multistage_contraction=False,
        apply_capsule_hard_expiry_deny=False,
        age_reference=str(raw.get("age_reference", "trusted_at")),
    )


def bind_object_id(evidence_type: str, proposal: ActionProposal) -> str | None:
    et = EvidenceType(evidence_type)
    if et in LOCAL_TYPES:
        return proposal.proposer_id
    if et is EvidenceType.PEER_AVAILABILITY:
        raw = proposal.parameters.get("target_auv")
        return str(raw) if raw else None
    if et is EvidenceType.SEGMENT_ASSIGNMENT:
        raw = proposal.parameters.get("segment_id")
        return str(raw) if raw else None
    if et in (EvidenceType.MISSION_OBJECTIVE, EvidenceType.SUPERVISOR_AUTHORITY):
        return proposal.proposer_id
    return None


def evaluate_requirements(
    proposal: ActionProposal,
    evidence_store: EvidenceStore,
    now: float,
    policy: ActionEvidencePolicy,
) -> EvidenceEvaluation:
    """Authorize from the local store only. Must not accept or read live global mission state."""
    action = proposal.action_type.value
    spec = policy.actions.get(action)
    if spec is None:
        return EvidenceEvaluation(
            passed=False,
            reason_code="DENY_INVALID_EVIDENCE",
            evidence_gaps=[{"action": action, "reason": "unknown_action"}],
        )
    required = list(spec.get("require") or [])
    checks: list[EvidenceCheck] = []
    gaps: list[dict[str, Any]] = []
    ages: dict[str, float] = {}
    predicates: dict[str, bool] = {}
    preds_cfg = dict(spec.get("predicates") or {})

    if not required:
        return EvidenceEvaluation(passed=True, reason_code="ALLOW_EVIDENCE")

    first_reason: str | None = None
    for et in required:
        oid = bind_object_id(str(et), proposal)
        max_age = policy.budgets_s.get(str(et))
        pred = preds_cfg.get(str(et))
        if oid is None:
            chk = EvidenceCheck(
                evidence_type=str(et),
                object_id="",
                found=False,
                max_age_s=max_age,
            )
            checks.append(chk)
            gaps.append({"evidence_type": et, "reason": "missing_object_binding"})
            if first_reason is None:
                first_reason = "DENY_MISSING_EVIDENCE"
            continue
        if max_age is None:
            chk = EvidenceCheck(
                evidence_type=str(et),
                object_id=oid,
                found=False,
                max_age_s=None,
            )
            checks.append(chk)
            gaps.append({"evidence_type": et, "object_id": oid, "reason": "budget_tbd"})
            if first_reason is None:
                first_reason = "DENY_MISSING_EVIDENCE"
            continue
        req = {
            "evidence_type": et,
            "object_id": oid,
            "max_age_s": max_age,
            "predicate": pred,
        }
        chk = evidence_store.is_valid(req, now)
        checks.append(chk)
        if chk.found and chk.age_s is not None:
            ages[f"{et}:{oid}"] = float(chk.age_s)
        if pred is not None:
            predicates[str(et)] = bool(chk.predicate_valid)
        if not chk.found:
            gaps.append({"evidence_type": et, "object_id": oid, "reason": "missing"})
            if first_reason is None:
                first_reason = "DENY_MISSING_EVIDENCE"
        elif chk.conflict:
            gaps.append({"evidence_type": et, "object_id": oid, "reason": "conflict"})
            if first_reason is None:
                first_reason = "DENY_INVALID_EVIDENCE"
        elif not chk.age_valid:
            gaps.append({"evidence_type": et, "object_id": oid, "reason": "stale", "age_s": chk.age_s})
            if first_reason is None:
                first_reason = "DENY_STALE_EVIDENCE"
        elif pred is not None and not chk.predicate_valid:
            gaps.append({"evidence_type": et, "object_id": oid, "reason": "predicate"})
            if first_reason is None:
                first_reason = "DENY_INVALID_EVIDENCE"

    if first_reason is None:
        return EvidenceEvaluation(
            passed=True,
            reason_code="ALLOW_EVIDENCE",
            evidence_checks=checks,
            evidence_gaps=gaps,
            evidence_ages=ages,
            predicates_evaluated=predicates,
        )
    return EvidenceEvaluation(
        passed=False,
        reason_code=first_reason,
        evidence_checks=checks,
        evidence_gaps=gaps,
        evidence_ages=ages,
        predicates_evaluated=predicates,
    )


def put_local_self(store: EvidenceStore, *, auv_id: str, now: float, energy: Any, position: Any, observations: dict[str, Any] | None, version: int) -> None:
    pos = position
    store.put(
        EvidenceRecord(
            evidence_type=EvidenceType.LOCAL_NAVIGATION,
            value={"x": float(getattr(pos, "x", 0.0)), "y": float(getattr(pos, "y", 0.0)), "z": float(getattr(pos, "z", 0.0))},
            source="self",
            observed_at=now,
            trusted_at=now,
            object_id=auv_id,
            version=version,
        )
    )
    store.put(
        EvidenceRecord(
            evidence_type=EvidenceType.LOCAL_ENERGY,
            value={
                "battery_j": float(energy.battery_j),
                "reserve_j": float(energy.reserve_j),
            },
            source="self",
            observed_at=now,
            trusted_at=now,
            object_id=auv_id,
            version=version,
        )
    )
    obs = dict(observations or {})
    store.put(
        EvidenceRecord(
            evidence_type=EvidenceType.LOCAL_OBSERVATION,
            value=obs,
            source="self",
            observed_at=now,
            trusted_at=now,
            object_id=auv_id,
            version=version,
        )
    )


def populate_from_trusted_snapshot(
    store: EvidenceStore,
    *,
    proposer_id: str,
    snapshot: dict[str, Any],
    energy: Any,
    position: Any,
    observations: dict[str, Any] | None,
    version: int = 1,
) -> None:
    t = float(snapshot["captured_at"])
    put_local_self(
        store,
        auv_id=proposer_id,
        now=t,
        energy=energy,
        position=position,
        observations=observations,
        version=version,
    )
    apply_trusted_remote_update(
        store,
        target_auv=str(snapshot["target_auv"]),
        segment_id=str(snapshot["segment_id"]),
        peer_failed=bool(snapshot["target_failed"]),
        segment_mandatory=bool(snapshot["segment_mandatory"]),
        segment_incomplete=bool(snapshot["segment_incomplete"]),
        segment_owner=snapshot.get("segment_owner"),
        trusted_at=t,
        observed_at=t,
        version=version,
        source="snapshot",
    )


def apply_trusted_remote_update(
    store: EvidenceStore,
    *,
    target_auv: str,
    segment_id: str,
    peer_failed: bool,
    segment_mandatory: bool,
    segment_incomplete: bool,
    segment_owner: Any,
    trusted_at: float,
    observed_at: float | None = None,
    version: int,
    source: str = "packet",
) -> None:
    """Refresh remote facts after an actual trusted comms/recon event."""
    obs = trusted_at if observed_at is None else observed_at
    store.put(
        EvidenceRecord(
            evidence_type=EvidenceType.PEER_AVAILABILITY,
            value={"failed": bool(peer_failed)},
            source=source,
            observed_at=obs,
            trusted_at=trusted_at,
            object_id=target_auv,
            version=version,
        )
    )
    store.put(
        EvidenceRecord(
            evidence_type=EvidenceType.SEGMENT_ASSIGNMENT,
            value={
                "mandatory": bool(segment_mandatory),
                "incomplete": bool(segment_incomplete),
                "owner": segment_owner,
            },
            source=source,
            observed_at=obs,
            trusted_at=trusted_at,
            object_id=segment_id,
            version=version,
        )
    )


def next_remote_version(store: EvidenceStore, evidence_type: EvidenceType, object_id: str) -> int:
    rec = store.get_latest(evidence_type, object_id)
    return 1 if rec is None else int(rec.version) + 1


def refresh_known_remote_from_supervisor_view(
    store: EvidenceStore,
    *,
    peer_failed_by_id: dict[str, bool],
    segments: dict[str, dict[str, Any]],
    trusted_at: float,
    source: str = "packet",
) -> None:
    """Update already-known remote object IDs after a trusted comms event.

    `peer_failed_by_id` and `segments` are explicit payload maps, not a live world pointer.
    """
    snap = store.snapshot()
    for rec in snap["records"]:
        et = rec["evidence_type"]
        oid = rec["object_id"]
        if et == EvidenceType.PEER_AVAILABILITY.value and oid in peer_failed_by_id:
            store.put(
                EvidenceRecord(
                    evidence_type=EvidenceType.PEER_AVAILABILITY,
                    value={"failed": bool(peer_failed_by_id[oid])},
                    source=source,
                    observed_at=trusted_at,
                    trusted_at=trusted_at,
                    object_id=oid,
                    version=int(rec["version"]) + 1,
                )
            )
        if et == EvidenceType.SEGMENT_ASSIGNMENT.value and oid in segments:
            seg = segments[oid]
            store.put(
                EvidenceRecord(
                    evidence_type=EvidenceType.SEGMENT_ASSIGNMENT,
                    value={
                        "mandatory": bool(seg["mandatory"]),
                        "incomplete": bool(seg["incomplete"]),
                        "owner": seg.get("owner"),
                    },
                    source=source,
                    observed_at=trusted_at,
                    trusted_at=trusted_at,
                    object_id=oid,
                    version=int(rec["version"]) + 1,
                )
            )
