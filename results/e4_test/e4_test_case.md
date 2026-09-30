# E4 TEST case

**Classification:** E4-INTERIOR

Primary analysis is `reassign_another_auv` only. Local slice is secondary.

## Primary rates

| method | useful-valid | obsolete |
|---|---:|---:|
| B0 | 1.0000 | 1.0000 |
| B1 | 0.8000 | 0.3636 |
| B2 | 0.2500 | 0.0455 |
| B3 | 0.6500 | 0.2273 |

## Supported claim

B3 occupies another useful-valid vs obsolete tradeoff point on held-out reassignment TEST and does not improve the primary frontier versus both B1 and B2.

## Regions (reassignment only)

| region | B3 useful-valid | B3 obsolete | note |
|---|---:|---:|---|
| R1 globally valid, PEER≤300 | 1.00 | — | matches B1; above B2 0.38 |
| R2 recovered, PEER≤300 | — | 1.00 | E2-F: same as B0/B1; B2 0.20 |
| R3 PEER>300, assign≤600 | 0.00 | 0.00 | matches B2; B1 still 0.60 / 0.43 |
| R4 both expired | 0.00 | 0.00 | matches B1 and B2 |

On this primary grid PEER and SEGMENT share `trusted_at=180`, so the 600 s assignment clock is not independently refreshed (unlike DEV cell C).

## Unsupported

B3 is not shown to dominate lease-400 or CGEA on the preregistered primary criterion.
Fresh evidence is not truth; evidence budgets are not shown to be optimal; exclusive ownership is not solved; field deployment is not validated.

