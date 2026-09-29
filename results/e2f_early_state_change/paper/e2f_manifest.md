# E2-F early-state-change falsification

- git_commit: `b020b05fd30ba074657a54f681c4b19a3085cdb2`
- parent_sha: `b020b05fd30ba074657a54f681c4b19a3085cdb2`
- campaign: `e2f_early_state_change`
- n_runs: 60 (expect 60 = 3 env × 10 seeds × 1 context × 1 age × 2 variants)
- policy_version: `paper_risk_bounded_v1_2026-09-28`
- utility_freeze_id: `utility_v2_frozen_2026-09-28`
- safe_useful_retention_freeze_id: `safe_useful_retention_v1_2026-09-28`
- outage: 200–1200 s
- authority_epoch_s: 180.0
- recovery_time_s: 240.0 (authority age 60.0 s)
- proposal_time_s: 300.0 (authority age 120.0 s)
- expected_freshness: fresh
- proposer: auv_06
- target: auv_05
- variants: B4, B4-NoFreshness (`b4_no_freshness_v1`)
- bootstrap: cluster-preserving over 30 (environment, seed) units, 10000 resamples, seed 20260929
- traces: `{"paper_ssp_200m_v1": {"0": "tr_54fb653caded_gpu", "1": "tr_3bf719402f6c_gpu", "2": "tr_0ab8a47a1225_gpu", "3": "tr_f9f4d2c6f6d1_gpu", "4": "tr_5bf96997e3af_gpu", "5": "tr_15e068cb7ee2_gpu", "6": "tr_554048377072_gpu", "7": "tr_58d2b05ddd66_gpu", "8": "tr_ca1dfa6822d6_gpu", "9": "tr_f9d255d666ea_gpu"}, "paper_ssp_200m_moderate_v1": {"0": "tr_e481a0d054ed_gpu", "1": "tr_a88224c7b787_gpu", "2": "tr_4370fea4680c_gpu", "3": "tr_20211a97cb5f_gpu", "4": "tr_6f36c0ce7c2d_gpu", "5": "tr_5d331a011f4e_gpu", "6": "tr_fb806e94e0f6_gpu", "7": "tr_0339ceb0e831_gpu", "8": "tr_6d424feb0439_gpu", "9": "tr_777f672e9af8_gpu"}, "paper_ssp_200m_strong_v1": {"0": "tr_41578464d64c_gpu", "1": "tr_6c0fce5b4aa8_gpu", "2": "tr_e18b2f198ce8_gpu", "3": "tr_90c59e32088b_gpu", "4": "tr_d1ea9e6ed537_gpu", "5": "tr_656c2a3d7e5e_gpu", "6": "tr_59abcdc50842_gpu", "7": "tr_4e1f28b2f305_gpu", "8": "tr_cdd5f1950a3c_gpu", "9": "tr_b3e3101c8c2a_gpu"}}`
- CASE: F1

Does not overwrite E1 or E2 production artifacts. Do not retune 180/400/900.
