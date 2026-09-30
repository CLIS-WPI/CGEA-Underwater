# E6 policy diff

- v1: `paper_risk_bounded_v1_2026-09-28` (preserved)
- v2: `paper_risk_bounded_v2_2026-09-30`
- v2 differs from v1 ONLY in HARD_EXPIRED handling of LOW_RISK actions.
- aging/stale/hard thresholds unchanged (180 / 400 / 900).

## FRESH
- identical: True

## AGING
- identical: True

## STALE
- identical: True

## HARD_EXPIRED
- identical: False
- v1 allowed: ['surfacing_safe_mode']
- v2 allowed: ['bounded_path_correction', 'collision_avoidance', 'hold_station', 'local_inference', 'repeat_sonar_scan', 'surfacing_safe_mode']
- v1 forbidden: ['abandon_mandatory_inspection', 'bounded_path_correction', 'change_high_level_objective', 'collision_avoidance', 'enter_exclusion_zone', 'exceed_return_energy_reserve', 'hold_station', 'local_inference', 'reassign_another_auv', 'repeat_sonar_scan']
- v2 forbidden: ['abandon_mandatory_inspection', 'change_high_level_objective', 'enter_exclusion_zone', 'exceed_return_energy_reserve', 'reassign_another_auv']
