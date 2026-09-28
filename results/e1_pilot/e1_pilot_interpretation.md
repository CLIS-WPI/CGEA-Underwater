# E1 Pilot — Critical Interpretation

- git_commit: `f36ecff734f06d3060c99f293402bac8c9ae532d`
- seeds: [0, 1, 2] (pilot only; n=3)
- B5 variants fixed a priori: conservative / nominal / permissive
- No CGEA threshold tuning after viewing results.

## Mean metrics (all outages pooled)
- **B1**: utility=11.976, highrisk=0.375, falsedeny=0.000, retention=1.000
- **B2**: utility=11.976, highrisk=0.375, falsedeny=0.000, retention=1.000
- **B3**: utility=11.978, highrisk=0.000, falsedeny=0.000, retention=1.000
- **B4**: utility=11.976, highrisk=0.308, falsedeny=0.000, retention=1.000
- **B5_conservative**: utility=11.977, highrisk=0.000, falsedeny=0.000, retention=1.000
- **B5_nominal**: utility=11.976, highrisk=0.375, falsedeny=0.000, retention=1.000
- **B5_permissive**: utility=11.976, highrisk=0.375, falsedeny=0.000, retention=1.000

## Mean metrics at long outage
- **B1**: utility=11.976, highrisk=0.625
- **B2**: utility=11.976, highrisk=0.625
- **B3**: utility=11.978, highrisk=0.000
- **B4**: utility=11.976, highrisk=0.625
- **B5_conservative**: utility=11.978, highrisk=0.000
- **B5_nominal**: utility=11.976, highrisk=0.625
- **B5_permissive**: utility=11.976, highrisk=0.625

## Does CGEA show a real risk–utility tradeoff?
- **Warning**: B4 ≈ B5_nominal on both utility and risk. Do **not** tune CGEA to win; this suggests current contribution may be insufficient or difference is policy-construction.

## Policy-construction caveat
- B1 denies consequential without supervisor → low risk, possibly lower utility under long outage.
- B2 always allows → high risk by construction under partition.
- B3 static low-risk only → low risk, may stall consequential mission repairs.
- B5_permissive ≈ B2 for consequential under outage; B5_conservative ≈ B1-like under partition. Compare B4 primarily to **B5_nominal**, not only extremes.
- Long-outage snapshot: B1(u=11.98,r=0.62), B2(u=11.98,r=0.62), B4(u=11.98,r=0.62), B5_nominal(u=11.98,r=0.62).

## Go / No-Go for 10-seed production
- Review raw table + frontier before any production sweep.
- Do not retune freshness/authority thresholds based on this pilot.
