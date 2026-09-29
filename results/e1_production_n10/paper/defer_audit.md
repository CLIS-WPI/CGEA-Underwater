# DEFER audit (source + production logs)

## Is DEFER implemented?

Yes. `GovernorDecision.DEFER` exists. In `ExecutionGovernor.decide` (`src/cgea/governance/__init__.py`), the only production path that returns DEFER is:

- `connectivity == RECOVERING`
- proposal is consequential
- `violates_frozen_risk` is false  
  → `DEFER` / `DEFER_RECOVERING_PENDING_REAUTH`

If recovering **and** `violates_frozen_risk` is true → `DENY` / `DENY_RECOVERING_HARD_SAFETY` (checked first).

Other `ReasonCode` DEFER values (`DEFER_PARTITION`, `DEFER_DEGRADED`, `DEFER_AWAITING_SUPERVISOR`) are **declared but not returned** by `decide()` in this frozen governor.

B4 passes `violates_frozen_risk` and `conditional_ok` through to the governor (`src/cgea/baselines/__init__.py` `B4CGEA.decide`). Immediate-resume can remap RECOVERING→CONNECTED; production uses `immediate_resume: false`.

## Why production recorded 0 DEFER

B4 coverage counters: 0 DEFER in all freshness bins. `reason_code_counts` campaign-wide have `DENY_RECOVERING_HARD_SAFETY` = 2144 and **no** `DEFER_RECOVERING_PENDING_REAUTH`.

So every RECOVERING consequential proposal in this campaign was a **hard-safety** class (`violates_frozen_risk=True`). The only frozen consequential class that is oracle-safe (could DEFER while recovering) is `reassign_another_auv`. It was not proposed on RECOVERING ticks in these logs (the 48 aging reassigns are `PARTITIONED` at `t=220`, not RECOVERING).

## Expected vs miss

0 DEFER is **expected under this frozen workload and governor**, not a missing implementation: recovering ticks hit exclusion/reserve/abandon, which are denied as hard-safety; reassign did not land on RECOVERING.
