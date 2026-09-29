"""E3 fixed-expiry challenge constants. Does not change E2/E2-F defaults."""

from __future__ import annotations

import math

from cgea.experiments.e2_authority_age import AUTHORITY_EPOCH_S, expected_freshness_band

E3_CAMPAIGN = "e3_fixed_expiry_challenge"
E3_PARENT_DEFAULT = "fb37529"
DEV_SEEDS = [0, 1, 2, 3, 4]
TEST_SEEDS = [5, 6, 7, 8, 9]
TTL_CANDIDATES = [60.0, 120.0, 180.0, 240.0, 400.0, 600.0, 900.0, math.inf]
PROPOSAL_AGES = [120.0, 180.0, 240.0, 360.0, 520.0, 760.0, 960.0]
# recovery_age None = no_change. Equality with proposal age is excluded from primary metrics.
CONTEXTS: dict[str, float | None] = {
    "no_change": None,
    "recover_age_60": 60.0,
    "recover_age_150": 150.0,
    "recover_age_300": 300.0,
    "recover_age_450": 450.0,
    "recover_age_750": 750.0,
}
BOOT_SEED = 20260929
N_BOOT = 10_000
USEFUL_VALID_FLOOR = 0.80
# CGEA aging_age_s is 180 inclusive: age >= 180 → AGING (unchanged implementation).
CGEA_AGE_180_IS = "aging"


def challenge_time_s(authority_age_s: float) -> float:
    return AUTHORITY_EPOCH_S + float(authority_age_s)


def cgea_expected_freshness(authority_age_s: float) -> str:
    now = challenge_time_s(authority_age_s)
    return expected_freshness_band(float(authority_age_s), now, AUTHORITY_EPOCH_S, 900.0)


def context_status(context_id: str, proposal_age: float) -> str:
    rec = CONTEXTS[context_id]
    if rec is None:
        return "valid"
    if abs(float(proposal_age) - float(rec)) < 1e-9:
        return "equal"
    if float(proposal_age) < float(rec):
        return "valid"
    return "obsolete"


def ttl_label(ttl: float) -> str:
    if math.isinf(ttl):
        return "infinity"
    return str(int(ttl)) if float(ttl).is_integer() else str(ttl)


def parse_ttl(label: str) -> float:
    if str(label).lower() in ("inf", "infinity"):
        return math.inf
    return float(label)
