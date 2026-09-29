# E3 fixed-expiry challenge

- parent_sha: `fb37529d60815d33822aa71b030845a2b8ad1bbc`
- ttl_selection_commit: `41ac1632b2376f766a3380a44fe84164b4996c8d`
- git_commit: `d1fca3cb3b061c4b77a8ca766d5300ebde6a024b`
- campaign: `e3_fixed_expiry_challenge`
- n_dev_runs: 5040
- n_test_runs: 1890 (expect 1890 = 15 clusters × 6 contexts × 7 ages × 3 methods)
- selected_ttl: 400
- dev_seeds: [0, 1, 2, 3, 4]
- test_seeds: [5, 6, 7, 8, 9]
- policy_version: `paper_risk_bounded_v1_2026-09-28`
- utility_freeze_id: `utility_v2_frozen_2026-09-28`
- safe_useful_retention_freeze_id: `safe_useful_retention_v1_2026-09-28`
- bootstrap_seed: 20260929
- CGEA age 180: aging
- traces: `{"paper_ssp_200m_v1": {"5": "tr_15e068cb7ee2_gpu", "6": "tr_554048377072_gpu", "7": "tr_58d2b05ddd66_gpu", "8": "tr_ca1dfa6822d6_gpu", "9": "tr_f9d255d666ea_gpu"}, "paper_ssp_200m_moderate_v1": {"5": "tr_5d331a011f4e_gpu", "6": "tr_fb806e94e0f6_gpu", "7": "tr_0339ceb0e831_gpu", "8": "tr_6d424feb0439_gpu", "9": "tr_777f672e9af8_gpu"}, "paper_ssp_200m_strong_v1": {"5": "tr_656c2a3d7e5e_gpu", "6": "tr_59abcdc50842_gpu", "7": "tr_4e1f28b2f305_gpu", "8": "tr_cdd5f1950a3c_gpu", "9": "tr_b3e3101c8c2a_gpu"}}`
- CASE: E3-DIFFERENTIATED
