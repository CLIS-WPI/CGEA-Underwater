# E1 production n=10

- git_commit: `85424607cd80fc940be0d3f9bf743dcc9e509f91`
- campaign: `e1_production_n10`
- n_runs: 1260 (expect 1260 = 3 env × 10 seeds × 6 outages × 7 baselines)
- policy_version: `paper_risk_bounded_v1_2026-09-28`
- utility_freeze_id: `utility_v2_frozen_2026-09-28`
- safe_useful_retention_freeze_id: `safe_useful_retention_v1_2026-09-28`
- workers: 24 SimPy CPU; GPU traces prebuilt per (environment, seed)
- paired: B4−B5_nominal (primary), B4−B3
- B5_conservative / B5_permissive: robustness bounds, not the main manuscript pair
- Observed equivalences under this frozen workload (not retuning): B5_nominal == B5_permissive == B2 in every tested cell; B5_conservative == B3 in every tested cell.
- Bounded 95% CIs (hard_safety_violation_rate, safe_useful_retention, auv_reauth_fraction, auv_timeout_fraction) are percentile bootstrap over the same 30 paired cells (10 000 resamples, seed 20260928). Means and raw rows are unchanged. Unbounded metrics keep t-based CIs.
- traces: `{"paper_ssp_200m_v1": {"0": "tr_54fb653caded_gpu", "1": "tr_3bf719402f6c_gpu", "2": "tr_0ab8a47a1225_gpu", "3": "tr_f9f4d2c6f6d1_gpu", "4": "tr_5bf96997e3af_gpu", "5": "tr_15e068cb7ee2_gpu", "6": "tr_554048377072_gpu", "7": "tr_58d2b05ddd66_gpu", "8": "tr_ca1dfa6822d6_gpu", "9": "tr_f9d255d666ea_gpu"}, "paper_ssp_200m_moderate_v1": {"0": "tr_e481a0d054ed_gpu", "1": "tr_a88224c7b787_gpu", "2": "tr_4370fea4680c_gpu", "3": "tr_20211a97cb5f_gpu", "4": "tr_6f36c0ce7c2d_gpu", "5": "tr_5d331a011f4e_gpu", "6": "tr_fb806e94e0f6_gpu", "7": "tr_0339ceb0e831_gpu", "8": "tr_6d424feb0439_gpu", "9": "tr_777f672e9af8_gpu"}, "paper_ssp_200m_strong_v1": {"0": "tr_41578464d64c_gpu", "1": "tr_6c0fce5b4aa8_gpu", "2": "tr_e18b2f198ce8_gpu", "3": "tr_90c59e32088b_gpu", "4": "tr_d1ea9e6ed537_gpu", "5": "tr_656c2a3d7e5e_gpu", "6": "tr_59abcdc50842_gpu", "7": "tr_4e1f28b2f305_gpu", "8": "tr_cdd5f1950a3c_gpu", "9": "tr_b3e3101c8c2a_gpu"}}`

Do not retune policy from these numbers. Diagnostic campaigns remain in results/.
