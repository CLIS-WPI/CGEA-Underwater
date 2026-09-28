# E1-v3 diagnostic checkpoint

**Role:** diagnostic, **not paper-candidate**.

All disconnected reconciliation attempts timed out (three DIGEST retries, then abort).
This campaign does not support an E3 / reauthorization-recovery claim.

## Provenance of the tables

- Campaign directory: `results/e1_pilot_v3_gpu/`
- Grid: 3 environments × 3 seeds × 6 outages × 7 baselines = **378** SimPy replays
- GPU traces prebuilt on CUDA device 1; SimPy workers = 24
- Frozen CGEA thresholds / B5 variants / utility weights: unchanged from E1-v2
- E1-v2 diagnostic parent: `b0d804b` (`results/e1_v2_checkpoint.json`)

The `git_commit` field inside the CSV/JSON rows is **`b0d804b41d6f708612dc345c3e165ef89bc8bd77`**.
Those files were produced from the **uncommitted working tree** that implemented E1-v3,
with `GIT_COMMIT` taken from `HEAD` at run time (`b0d804b`).

The git commit that adds this manifest is a **snapshot of that same working tree**
(plus this manifest / README note). It is not a re-run. Do not treat row-level
`git_commit=b0d804b` as “v3 source lives only in that parent commit.”

## Predeclared gates (measured)

- Median connected GCO (outage 0): 0.766 (sanity: must be < 0.99)
- B4 measured freshness gate at outage 1000: **pass**
- Reconciliation: **fail** (universal timeout)

Do not start a 10-seed production run from this checkpoint.
Do not retune aging/stale/hard expiry, B5, or utility weights from these numbers.
