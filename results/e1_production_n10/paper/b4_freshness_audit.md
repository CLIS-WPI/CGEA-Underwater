# B4 freshness audit (existing production only)

Campaign: `e1_production_n10` (180 B4 runs). No new experiment. CASE A.

Policy: `paper_risk_bounded_v1_2026-09-28`. Aging strips `reassign_another_auv` (plus already-forbidden abandon/objective). Stale forbids all consequential. Hard expiry denies consequential with `DENY_HARD_EXPIRY`.

## Consequential proposals by freshness (all AUVs)

| freshness | proposed | ALLOW | DENY | DEFER | allow_rate |
|---|---:|---:|---:|---:|---:|
| fresh | 47228 | 600 | 46628 | 0 | 0.0127 |
| aging | 5612 | 0 | 5612 | 0 | 0 |
| stale | 7800 | 0 | 7800 | 0 | 0 |
| hard_expired | 2424 | 0 | 2424 | 0 | 0 |

HARD_EXPIRED is exercised: 2424 consequential proposals (0 ALLOW). `DENY_HARD_EXPIRY` count = 2360 (remaining hard-expired ticks are `DENY_RECOVERING_HARD_SAFETY` first). `DENY_STALE_AUTHORITY` = 0 (stale uses contracted forbidden set → `DENY_FORBIDDEN`).

## Same action type: `reassign_another_auv`

| | count |
|---|---:|
| proposed | 788 |
| ALLOW (`ALLOW_AUTHORIZED`) | 600 |
| DENY | 188 |
| DENY while FRESH (`DENY_CONDITIONAL_UNMET`, exact identity with reason counts) | 140 |
| DENY while AGING (inferred `DENY_FORBIDDEN` after aging strip) | 48 |
| DENY while STALE / HARD_EXPIRED | 0 |

All 600 ALLOWs occur in FRESH only (coverage). Zero consequential ALLOW in AGING/STALE/HARD_EXPIRED.

## Matched evidence (same environment, seed, outage, trace)

12 B4 runs have both FRESH `reassign` ALLOW and AGING `reassign` DENY. In those runs `DENY_CONDITIONAL_UNMET` is 0, so the aging denials are not the static conditional gate.

Matched runs: 12. Aging deny events in those runs: 48. Fresh allows in those runs: 12.

This is **matched within-run descriptive evidence**, not a counterfactual replay (no B4-NoFreshness). Decision change is ALLOW → DENY for `reassign_another_auv` from FRESH → AGING. Reason: aging contraction adds reassign to forbidden (`DENY_FORBIDDEN`), not `DENY_CONDITIONAL_UNMET`.

Statically forbidden classes (`enter_exclusion_zone`, `exceed_return_energy_reserve`, `abandon_mandatory_inspection`, `change_high_level_objective`) are DENY at every freshness. Freshness changes their **reason** at HARD_EXPIRED (`DENY_HARD_EXPIRY`) but not the ALLOW/DENY bit. Do not count those as execution-envelope changes.

## Action-mix caveat

The single-AUV `authority_timeline` (`auv_08`) is 100% `exceed_return_energy_reserve` and cannot show reassign. Campaign mix is dominated by always-forbidden classes. The freshness ALLOW-rate drop is **not** only mix: the only allowed consequential class is reassign, and that class itself goes from ALLOW (FRESH, conditions met) to DENY (AGING).

## DEFER

B4 recorded 0 DEFER on consequential coverage in this campaign.

## Case

**CASE A.** Do not run B4-NoFreshness.

Supported manuscript claim (narrow): freshness contracts the execution envelope for the conditional class. As trusted authority ages, `reassign_another_auv` proposals that are allowed while FRESH are denied under AGING without changing the static risk taxonomy.

Do **not** claim freshness improves mission utility from this audit.
