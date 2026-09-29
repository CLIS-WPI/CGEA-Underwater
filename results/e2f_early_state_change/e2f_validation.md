# E2-F validation PASSED

- n_runs: 60
- authority_epoch_s = 180
- global recovery t = 240
- proposal t = 300, authority age = 120, B4 freshness = FRESH
- local_target_failed remains true; global_target_failed is false
- local snapshot used for conditional_ok; oracle uses global world
- exactly one controlled proposal per run
- paired variants share snapshot, global state, and trace
- E2 production artifacts unchanged
- CASE F1
