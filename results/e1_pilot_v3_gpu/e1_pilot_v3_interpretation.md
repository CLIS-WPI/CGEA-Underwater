# E1-v3 n=3 report

- checkpoint_v2: b0d804b
- n_runs: 378 (expect 378)
- traces: {"paper_ssp_200m_v1": {"0": "tr_54fb653caded_gpu", "1": "tr_3bf719402f6c_gpu", "2": "tr_0ab8a47a1225_gpu"}, "paper_ssp_200m_moderate_v1": {"0": "tr_e481a0d054ed_gpu", "1": "tr_a88224c7b787_gpu", "2": "tr_4370fea4680c_gpu"}, "paper_ssp_200m_strong_v1": {"0": "tr_41578464d64c_gpu", "1": "tr_6c0fce5b4aa8_gpu", "2": "tr_e18b2f198ce8_gpu"}}
- median GCO (outage=0): 0.7662
- RECOVERING consequential proposed (all rows sum): 1402
- B4 measured freshness gate (outage 1000): True
- paired delta_utility B4-B5_nominal mean: 52.266
- paired delta_utility_without_conflict mean: 38.932

## H. Advantage after removing capsule-circular conflicts
- Mean paired utility delta favors B4. Check `utility_without_conflict_penalty` and B4 freshness/reason codes before attributing this to freshness contraction.
- Do not start 10-seed production from this script.
