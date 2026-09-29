"""E2-F timing and local/global split. Does not retune B4 or overwrite E2."""

from __future__ import annotations

from pathlib import Path

from cgea.experiments.e2_authority_age import (
    AUTHORITY_EPOCH_S,
    CHANGE_AGE_S,
    E2F_CHANGE_AGE_S,
    E2F_CHALLENGE_TIME_S,
    E2F_CONTEXT,
    E2F_RECOVERY_TIME_S,
    e2_recovers_globally,
    expected_freshness_band,
)


def test_e2f_timings_are_predeclared_and_inside_fresh():
    assert E2F_CONTEXT == "target_recovers_age60"
    assert E2F_CHANGE_AGE_S == 60.0
    assert E2F_RECOVERY_TIME_S == 240.0
    assert E2F_CHALLENGE_TIME_S == 300.0
    assert E2F_CHALLENGE_TIME_S - AUTHORITY_EPOCH_S == 120.0
    assert E2F_CHANGE_AGE_S < 180.0
    assert expected_freshness_band(120.0, 300.0, AUTHORITY_EPOCH_S, 900.0) == "fresh"
    assert e2_recovers_globally("target_recovers_age60")
    assert e2_recovers_globally("target_recovers_age300")
    assert not e2_recovers_globally("benign_static")


def test_e2_change_age_unchanged():
    assert CHANGE_AGE_S == 300.0


def test_e2_artifacts_untouched():
    root = Path(__file__).resolve().parents[1]
    p = root / "results" / "e2_authority_age_context" / "e2_manifest.md"
    assert p.is_file()
    text = p.read_text()
    assert "n_runs: 480" in text
    assert "authority_age_context_v1" in text or "CASE: A" in text
