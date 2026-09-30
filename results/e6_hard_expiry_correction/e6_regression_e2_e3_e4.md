# E2 / E3 / E4 targeted regression

Default `configs/governance/cgea.yaml` remains `paper_risk_bounded_v1_2026-09-28`.
v2 is selected only when `governance.policy_version` is the v2 identifier.
Consequential HARD_EXPIRED still returns DENY_HARD_EXPIRY under both versions (unit tests T7–T9).

## E2 primary reassignment
Not rerun. Primary outcome is CONSEQUENTIAL reassignment; HARD_EXPIRED denial is unchanged.
Pytest: `tests/test_e2_authority_age_sanity.py` passed after the v2 patch.

## E2-F primary event
Not rerun. Pytest: `tests/test_e2f_early_state_change.py` passed.

## E3 primary reassignment rates
Not rerun. Frozen TEST CGEA useful_valid=0.25 obsolete≈0.04545 remains the E3 record.
Pytest: `tests/test_e3_fixed_expiry.py` passed.

## E4 primary reassignment rates
Not rerun. Frozen TEST primary table unchanged on disk.
Pytest: `tests/test_e4_integration.py` and `tests/test_evidence_authority.py` passed.

## E4 local secondary (retired)
Option B: retire the claim that CGEA denies collision_avoidance at capsule age 960 while evidence allows it.
That comparison used v1 HARD_EXPIRED fallback-only contraction. Under corrected v2, LOW_RISK local actions remain allowed.
Do not use the old local-secondary result in the manuscript.

## Lease-180 equivalence (E3 existing data, not retuned)
From `results/e3_fixed_expiry_challenge/e3_test_pareto.csv`:
- FE_DEV_180: useful_valid = 0.25, obsolete = 0.045454545454545456
- CGEA_TEST: useful_valid = 0.25, obsolete = 0.045454545454545456

For the evaluated reassignment action, CGEA is behaviorally equivalent to a 180-s binary lease.
This is characterization of the aging-band reassignment strip, not a threshold change.
