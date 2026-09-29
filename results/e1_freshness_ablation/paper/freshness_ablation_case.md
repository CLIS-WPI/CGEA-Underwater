# Freshness ablation case (`b4_no_freshness_v1`)

git_commit: `b0c80b8952954b057e2294eece3ac2f79c8c32a1`
n_runs: 120 (expect 120)
traces: `{"paper_ssp_200m_v1": {"0": "tr_54fb653caded_gpu", "1": "tr_3bf719402f6c_gpu", "2": "tr_0ab8a47a1225_gpu", "3": "tr_f9f4d2c6f6d1_gpu", "4": "tr_5bf96997e3af_gpu"}, "paper_ssp_200m_moderate_v1": {"0": "tr_e481a0d054ed_gpu", "1": "tr_a88224c7b787_gpu", "2": "tr_4370fea4680c_gpu", "3": "tr_20211a97cb5f_gpu", "4": "tr_6f36c0ce7c2d_gpu"}, "paper_ssp_200m_strong_v1": {"0": "tr_41578464d64c_gpu", "1": "tr_6c0fce5b4aa8_gpu", "2": "tr_e18b2f198ce8_gpu", "3": "tr_90c59e32088b_gpu", "4": "tr_d1ea9e6ed537_gpu"}}`

## CASE A

NoFreshness improves SUR but increases conflicts / duplicate work.

Paired B4 − B4-NoFreshness (n=60 cells):
- SUR: -0.04000000000000001 [-0.08000000000000002, -0.010000000000000002]
- hard-safety: 0.0 [0.0, 0.0]
- contradictory_reassignment: -0.06666666666666667 [-0.1303161443295523, -0.0030171890037810345]
- duplicate_work: 0.0 [0.0, 0.0]
- utility: -1.2655666666666667 [-2.4739758698293057, -0.05715746350402773]

Decision differences: 948
t=220 strong-env matched reassigns in this grid (seeds 0–4, not seed 8): n=80; B4 DENY=32; NF ALLOW=60; NF useful_delta>0=0; NF dup_delta>0=0; same-tick multi-allow on segment=16

Seed 8 from the production 48 is **outside** this predeclared 5-seed grid and was not added.

Do not retune 180/400/900 from these numbers.
