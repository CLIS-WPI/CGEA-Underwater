# E2 validation PASSED

- n_runs: 480
- exactly one controlled reassignment proposal per run
- B4 and B4-NoFreshness local snapshots match
- B4 and B4-NoFreshness global target/owner match
- freshness_mode differs (continuous vs disabled)
- local snapshot captured at t=180
- target_recovers_age300: global recovery at t=480; local_target_failed remains true
- freshness bands: 120 fresh, 240 aging, 520 stale, 960 hard_expired
- oracle beneficial as predeclared
- reassignment violates_frozen_risk is false
- production traces reused and matched the E1 manifest
- CASE A
