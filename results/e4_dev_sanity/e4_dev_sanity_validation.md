# E4 DEV-sanity validation

**Parent SHA:** `9dbac78a167ed8b4a488186a9c8c4ca2e8f297c6`  
**Verdict:** PASS  
**Runs:** 360 (15 DEV clusters × 6 cells × 4 methods)  
**DEV seeds:** 0–4 only  
**TEST seeds:** none  
**Mismatches:** 0  

Budgets were not retuned. No Pareto / superiority claim.

## S1–S10

| ID | Check | Status |
|----|--------|--------|
| S1 | B3 `authority_mode=evidence`; no CGEA contraction (cell A B3 ALLOW while B2 DENY_FORBIDDEN) | PASS |
| S2 | B3 never `DENY_HARD_EXPIRY` (cell F capsule age 960 still ALLOW_LOW_RISK) | PASS |
| S3 | B vs C: stale type PEER vs SEGMENT | PASS |
| S4 | Cell D: no peer-refresh packet; store still `failed=true` after global recovery | PASS |
| S5 | Cell D: B3 ALLOW + obsolete=1 (fresh evidence ≠ truth) | PASS |
| S6 | hard-safety violation count 0 all runs | PASS |
| S7 | B0/B1/B2 never emit evidence reason codes; A/F historical reasons match E2/E3 paths | PASS |
| S8 | exactly one controlled proposal per run | PASS |
| S9 | no seeds 5–9 | PASS |
| S10 | production GPU trace IDs reused (`forbid_trace_generation`) | PASS |

## Unresolved

- Held-out TEST not run.
- Cell F is secondary local-action architecture only.

