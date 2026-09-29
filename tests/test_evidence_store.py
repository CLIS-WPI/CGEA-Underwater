"""EvidenceStore rules: latest version, trusted_at, exact-tie conflict."""

from __future__ import annotations

from cgea.governance.evidence import EvidenceRecord, EvidenceStore, EvidenceType, evidence_age


def _rec(*, version: int, trusted_at: float, failed: bool = True) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_type=EvidenceType.PEER_AVAILABILITY,
        value={"failed": failed},
        source="test",
        observed_at=trusted_at,
        trusted_at=trusted_at,
        object_id="auv_05",
        version=version,
    )


def test_latest_version_wins():
    store = EvidenceStore()
    store.put(_rec(version=1, trusted_at=10.0, failed=True))
    store.put(_rec(version=2, trusted_at=5.0, failed=False))
    got = store.get_latest(EvidenceType.PEER_AVAILABILITY, "auv_05")
    assert got is not None
    assert got.version == 2
    assert got.value["failed"] is False


def test_equal_version_higher_trusted_at_wins():
    store = EvidenceStore()
    store.put(_rec(version=1, trusted_at=10.0, failed=True))
    store.put(_rec(version=1, trusted_at=20.0, failed=False))
    got = store.get_latest(EvidenceType.PEER_AVAILABILITY, "auv_05")
    assert got is not None
    assert got.trusted_at == 20.0
    assert got.value["failed"] is False
    assert not store.is_conflict(EvidenceType.PEER_AVAILABILITY, "auv_05")


def test_exact_tie_marks_conflict_no_merge():
    store = EvidenceStore()
    store.put(_rec(version=1, trusted_at=10.0, failed=True))
    store.put(_rec(version=1, trusted_at=10.0, failed=False))
    assert store.is_conflict(EvidenceType.PEER_AVAILABILITY, "auv_05")
    chk = store.is_valid(
        {
            "evidence_type": "PEER_AVAILABILITY",
            "object_id": "auv_05",
            "max_age_s": 300.0,
            "predicate": "value.failed == true",
        },
        now=10.0,
    )
    assert chk.conflict is True
    assert chk.predicate_valid is False


def test_age_is_now_minus_trusted_at():
    rec = _rec(version=1, trusted_at=180.0)
    assert evidence_age(rec, 400.0) == 220.0
    store = EvidenceStore()
    store.put(rec)
    assert store.age(rec, 400.0) == 220.0


def test_invalidate_removes_record():
    store = EvidenceStore()
    store.put(_rec(version=1, trusted_at=1.0))
    store.invalidate(EvidenceType.PEER_AVAILABILITY, "auv_05", "test")
    assert store.get_latest(EvidenceType.PEER_AVAILABILITY, "auv_05") is None
    snap = store.snapshot()
    assert "PEER_AVAILABILITY:auv_05" in snap["invalid_reasons"]
