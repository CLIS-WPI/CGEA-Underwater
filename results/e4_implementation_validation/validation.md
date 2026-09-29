# E4 implementation validation

**Parent design SHA:** `4d2df2e95636c9a314083a54f33aa4d1a9fccd73`  
**Implementation SHA:** `48a34aef572c3d0b653ae523bb1c00260a5ebec3`

**Campaign:** none (no E4 DEV/TEST).

## Tests

- New evidence/store/authority/integration tests: **26 collected cases** (5 store + 19 authority + 2 integration), all passed.
- Combined with historical suites listed in `regression_status.md`: **53 passed** in the validation run.

## Mechanism checks

| Check | Result |
|-------|--------|
| 180/400/900 contraction on B3 | disabled (`apply_cgea_multistage_contraction: false`) |
| Legacy 900 s `DENY_HARD_EXPIRY` on B3 | disabled (T15 ALLOW at capsule age >900) |
| T16 B2 `now=1000` | `DENY_HARD_EXPIRY` unchanged |
| E2-F T11 | ALLOW with local `failed=true` after global recovery |
| 300 vs 600 | t=400 ALLOW (ages 220); t=500 DENY_STALE peer age 320 |
| After peer refresh at 450 | t=700 ALLOW (250/520); t=800 DENY (350/620) |

Budgets were not tuned.
