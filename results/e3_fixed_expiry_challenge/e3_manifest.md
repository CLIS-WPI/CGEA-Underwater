# E3 split (recorded before TTL selection)

- parent_sha: `fb37529d60815d33822aa71b030845a2b8ad1bbc`
- git_commit_at_split_write: `fb37529d60815d33822aa71b030845a2b8ad1bbc`
- campaign: `e3_fixed_expiry_challenge`
- dev_seeds: [0, 1, 2, 3, 4]
- test_seeds: [5, 6, 7, 8, 9]
- ttl_candidates_s: ['60', '120', '180', '240', '400', '600', '900', 'infinity']
- proposal_ages_s: [120.0, 180.0, 240.0, 360.0, 520.0, 760.0, 960.0]
- contexts: ['no_change', 'recover_age_60', 'recover_age_150', 'recover_age_300', 'recover_age_450', 'recover_age_750']
- CGEA age 180 classification: aging (`age_s >= 180`)
- bootstrap_seed: 20260929
- selection_rule: minimize obsolete_execution_rate s.t. useful_valid >= 0.8; ties higher useful_valid then longer TTL
- selected_ttl: 400
- n_dev_runs: 5040
- status: TTL frozen; TEST not yet run
