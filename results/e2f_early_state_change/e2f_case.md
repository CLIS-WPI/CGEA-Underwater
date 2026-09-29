# E2-F case

CASE F1

Supported claim:
Fresh authority does not guarantee fresh mission state. In the controlled early-change condition, both B4 and the same governor without freshness execute the reassignment because the authority remains within its Fresh window even though the local mission snapshot is already obsolete.

Limitation:
Freshness bounds the lifetime of delegated execution authority; it does not detect arbitrary mission-state changes within that lifetime.

B4 obsolete_reassignment_execution: 1.0000
NoFreshness obsolete_reassignment_execution: 1.0000
paired Δ obsolete (B4 − NF): 0.0000  95% CI [0.0000, 0.0000]
all_clusters_identical: True

B4 ownership_override: 1.0000
NoFreshness ownership_override: 1.0000

B4 decisions: ['ALLOW|ALLOW_AUTHORIZED']
NoFreshness decisions: ['ALLOW|ALLOW_AUTHORIZED']

Hard-safety violation count (mean): 0.000000
Mission utility paired B4 − NoFreshness: 0.0014 [0.0006, 0.0025]

n_runs=60
bootstrap_seed=20260929
