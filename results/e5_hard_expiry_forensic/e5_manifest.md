# E5-FORENSIC manifest

FORENSIC REPLAY — NOT NEW SCIENTIFIC EXPERIMENT

- audit parent SHA (pre-commit): `c40bc4b1da9bd97498d41cca8bcac3bad25b9f4c`
- original production provenance SHA: `85424607cd80fc940be0d3f9bf743dcc9e509f91`
- production results commit: `b3466232232ec9863f0c7fd55f25e54ed48e1ab8`
- policy_version: `paper_risk_bounded_v1_2026-09-28` (unchanged)
- scope: B4 only, 3 env × 10 seeds × 6 outages = 180
- production campaign: e1_production_n10 (not modified)
- E2/E2-F/E3/E4 artifacts: not modified
- policy/governor/thresholds: not modified except forensic_log_all_decisions logging
- manuscript: not modified
- production event artifacts sufficient: no
- missing: ['per-AUV proposal logs for all 12 AUVs (authority_timeline is auv_08 only)', 'proposal time, connectivity, and authority_age for every LOW_RISK decision', 'reason_code on each LOW_RISK event (only aggregate reason_code_counts)', 'freshness label on each LOW_RISK event', 'consequential_decision_log is empty / unused in production extra']
- replay required: yes
- replay validation: PASS
- HISTORICAL_HARD_EXPIRY_SEMANTICS: SAME
- CASE: H2
