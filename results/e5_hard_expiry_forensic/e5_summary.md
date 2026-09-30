# E5 hard-expiry / LOW_RISK forensic summary

FORENSIC REPLAY — NOT NEW SCIENTIFIC EXPERIMENT

- audit parent SHA: `c40bc4b1da9bd97498d41cca8bcac3bad25b9f4c`
- production code SHA: `85424607cd80fc940be0d3f9bf743dcc9e509f91`
- production results SHA: `b3466232232ec9863f0c7fd55f25e54ed48e1ab8`
- production artifacts sufficient for event-level attribution: no
- replay required: yes
- replay validation: PASS
- HISTORICAL_HARD_EXPIRY_SEMANTICS: SAME
- CASE: H2

## Taxonomy
- LOW_RISK_ACTIONS: ['bounded_path_correction', 'collision_avoidance', 'hold_station', 'local_inference', 'repeat_sonar_scan']
- surfacing_safe_mode in LOW_RISK_ACTIONS: False

## Required counts (B4 forensic replay, validated against frozen headlines)
1. total proposals: 151200
2. total LOW_RISK proposals: 88136
3. total proposals at authority_age >= 900: 4776
4. total LOW_RISK proposals at authority_age >= 900: 2352
5. number ALLOW (all proposals): 86384
6. number DENY (all proposals): 64816
7. number DEFER (all proposals): 0

- LOW_RISK ALLOW at age>=900: 0
- LOW_RISK DENY at age>=900: 2352
- freshness vs age>=900 disagreements: 0
- LOW_RISK under HARD_EXPIRED label or age>=900: 2352
- contraction denials (A): 2352

## Denied LOW_RISK at hard expiry by action_type
{
  "hold_station": 496,
  "bounded_path_correction": 1856
}

## Denied LOW_RISK at hard expiry by reason_code
{
  "DENY_FORBIDDEN": 2352
}

## Attribution
{
  "A_hard_expiry_capsule_contraction": 2352
}

## Action-type table
[
  {
    "action_type": "collision_avoidance",
    "in_LOW_RISK_ACTIONS": true,
    "n_proposed": 0,
    "n_age_ge_900": 0,
    "n_hard_expired_label": 0,
    "ALLOW": 0,
    "DENY": 0,
    "DEFER": 0,
    "DENY_at_age_ge_900": 0,
    "contraction_denials": 0
  },
  {
    "action_type": "bounded_path_correction",
    "in_LOW_RISK_ACTIONS": true,
    "n_proposed": 70796,
    "n_age_ge_900": 1856,
    "n_hard_expired_label": 1856,
    "ALLOW": 68940,
    "DENY": 1856,
    "DEFER": 0,
    "DENY_at_age_ge_900": 1856,
    "contraction_denials": 1856
  },
  {
    "action_type": "repeat_sonar_scan",
    "in_LOW_RISK_ACTIONS": true,
    "n_proposed": 4740,
    "n_age_ge_900": 0,
    "n_hard_expired_label": 0,
    "ALLOW": 4740,
    "DENY": 0,
    "DEFER": 0,
    "DENY_at_age_ge_900": 0,
    "contraction_denials": 0
  },
  {
    "action_type": "local_inference",
    "in_LOW_RISK_ACTIONS": true,
    "n_proposed": 0,
    "n_age_ge_900": 0,
    "n_hard_expired_label": 0,
    "ALLOW": 0,
    "DENY": 0,
    "DEFER": 0,
    "DENY_at_age_ge_900": 0,
    "contraction_denials": 0
  },
  {
    "action_type": "hold_station",
    "in_LOW_RISK_ACTIONS": true,
    "n_proposed": 12600,
    "n_age_ge_900": 496,
    "n_hard_expired_label": 496,
    "ALLOW": 12104,
    "DENY": 496,
    "DEFER": 0,
    "DENY_at_age_ge_900": 496,
    "contraction_denials": 496
  },
  {
    "action_type": "surfacing_safe_mode",
    "in_LOW_RISK_ACTIONS": false,
    "n_proposed": 0,
    "n_age_ge_900": 0,
    "n_hard_expired_label": 0,
    "ALLOW": 0,
    "DENY": 0,
    "DEFER": 0,
    "DENY_at_age_ge_900": 0,
    "contraction_denials": 0
  }
]

## Collision-avoidance check
No production B4 collision_avoidance proposal occurred at authority_age >= 900.
- collision_avoidance proposals at age>=900: 0
- decisions: Counter()
- reason codes: Counter()

## Affected runs (contraction denials)
- n_runs: 40 / 180
- outages: ['0', '1000', '150', '300', '500', '750']
- cells: [('paper_ssp_200m_moderate_v1', 0, '1000', 1000.0), ('paper_ssp_200m_moderate_v1', 1, '1000', 1000.0), ('paper_ssp_200m_moderate_v1', 2, '1000', 1000.0), ('paper_ssp_200m_moderate_v1', 3, '1000', 1000.0), ('paper_ssp_200m_moderate_v1', 4, '1000', 1000.0), ('paper_ssp_200m_moderate_v1', 5, '1000', 1000.0), ('paper_ssp_200m_moderate_v1', 6, '1000', 1000.0), ('paper_ssp_200m_moderate_v1', 7, '1000', 1000.0), ('paper_ssp_200m_moderate_v1', 8, '1000', 1000.0), ('paper_ssp_200m_moderate_v1', 9, '1000', 1000.0), ('paper_ssp_200m_strong_v1', 0, '0', 0.0), ('paper_ssp_200m_strong_v1', 0, '1000', 1000.0), ('paper_ssp_200m_strong_v1', 0, '150', 150.0), ('paper_ssp_200m_strong_v1', 0, '300', 300.0), ('paper_ssp_200m_strong_v1', 0, '500', 500.0), ('paper_ssp_200m_strong_v1', 0, '750', 750.0), ('paper_ssp_200m_strong_v1', 1, '1000', 1000.0), ('paper_ssp_200m_strong_v1', 2, '1000', 1000.0), ('paper_ssp_200m_strong_v1', 3, '1000', 1000.0), ('paper_ssp_200m_strong_v1', 4, '1000', 1000.0), ('paper_ssp_200m_strong_v1', 5, '1000', 1000.0), ('paper_ssp_200m_strong_v1', 6, '1000', 1000.0), ('paper_ssp_200m_strong_v1', 7, '1000', 1000.0), ('paper_ssp_200m_strong_v1', 8, '0', 0.0), ('paper_ssp_200m_strong_v1', 8, '1000', 1000.0), ('paper_ssp_200m_strong_v1', 8, '150', 150.0), ('paper_ssp_200m_strong_v1', 8, '300', 300.0), ('paper_ssp_200m_strong_v1', 8, '500', 500.0), ('paper_ssp_200m_strong_v1', 8, '750', 750.0), ('paper_ssp_200m_strong_v1', 9, '1000', 1000.0), ('paper_ssp_200m_v1', 0, '1000', 1000.0), ('paper_ssp_200m_v1', 1, '1000', 1000.0), ('paper_ssp_200m_v1', 2, '1000', 1000.0), ('paper_ssp_200m_v1', 3, '1000', 1000.0), ('paper_ssp_200m_v1', 4, '1000', 1000.0), ('paper_ssp_200m_v1', 5, '1000', 1000.0), ('paper_ssp_200m_v1', 6, '1000', 1000.0), ('paper_ssp_200m_v1', 7, '1000', 1000.0), ('paper_ssp_200m_v1', 8, '1000', 1000.0), ('paper_ssp_200m_v1', 9, '1000', 1000.0)]

## Possible affected manuscript metrics if LOW_RISK were preserved at hard expiry
- mission utility (local path/hold execution can change utility_breakdown terms)
- mission completion (if bounded_path_correction denials alter inspection progress)
- energy (propulsion if path-correction vs hold substitution)
- SUR: not expected from logged definitions (safe_useful_* counts consequential only)
- hard-safety: not expected (violation counters are consequential-class)
- semantic conflicts / governance TX / mission TX: no logged dependence on LOW_RISK authorization

## Production-only aggregates (insufficient event logs)
{
  "n_runs": 180,
  "n_prop": 151200,
  "n_low": 88136,
  "n_allow": 86384,
  "n_deny": 64816,
  "n_defer": 0,
  "low_deny": 2352,
  "collision_proposed": 0,
  "reason_tot": {
    "ALLOW_LOW_RISK": 85784,
    "DENY_FORBIDDEN": 60172,
    "ALLOW_AUTHORIZED": 600,
    "DENY_CONDITIONAL_UNMET": 140,
    "DENY_RECOVERING_HARD_SAFETY": 2144,
    "DENY_HARD_EXPIRY": 2360
  },
  "timeline_n": 12600,
  "timeline_low_hard": 0,
  "timeline_disagree": 0,
  "has_all_auv_decision_log": false,
  "has_consequential_decision_log": false
}

## Historical evidence
Historical B4 uses ExecutionGovernor.decide + contract_capsule at 85424607cd80fc940be0d3f9bf743dcc9e509f91. HARD_EXPIRED keeps only fallback_action and forbids every other ActionType; CONSEQUENTIAL is DENY_HARD_EXPIRY before the forbidden check; LOW_RISK then hits DENY_FORBIDDEN. Current B4 governor path is the same. Current B2Unrestricted always ALLOW_AUTHORIZED and does not implement hard-expiry contraction (comparison requested vs B2 fallback-only is therefore not applicable; governor semantics vs historical B4: SAME).

HISTORICAL snippet:
if now >= capsule.hard_expiry or freshness == AuthorityFreshness.HARD_EXPIRED:
        data["allowed_actions"] = [capsule.fallback_action]
        data["conditional_actions"] = []
        data["forbidden_actions"] = [a.value for a in ActionType if a.value != capsule.fallback_acti

CURRENT snippet:
if now >= capsule.hard_expiry or freshness == AuthorityFreshness.HARD_EXPIRED:
        data["allowed_actions"] = [capsule.fallback_action]
        data["conditional_actions"] = []
        data["forbidden_actions"] = [a.value for a in ActionType if a.value != capsule.fallback_acti

B2 unrestricted present: True
