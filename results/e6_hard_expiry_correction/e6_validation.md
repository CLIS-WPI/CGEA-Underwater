# E6 pre-rerun validation

- status: PASS
- cell: {'environment_id': 'paper_ssp_200m_strong_v1', 'seed': 0, 'outage': '150'}
- trace_id: `tr_41578464d64c_gpu`
- forbid_trace_generation: true
- v1 first LOW_RISK@>=900: {'t_s': 900.0, 'auv_id': 'auv_00', 'action_type': 'bounded_path_correction', 'risk_class': 'low', 'decision': 'DENY', 'reason_code': 'DENY_FORBIDDEN', 'authority_freshness': 'hard_expired', 'authority_age_s': 900.0, 'connectivity': 'PARTITIONED', 'supervisor_reachable': False}
- v2 first LOW_RISK@>=900: {'t_s': 900.0, 'auv_id': 'auv_00', 'action_type': 'bounded_path_correction', 'risk_class': 'low', 'decision': 'ALLOW', 'reason_code': 'ALLOW_LOW_RISK', 'authority_freshness': 'hard_expired', 'authority_age_s': 900.0, 'connectivity': 'PARTITIONED', 'supervisor_reachable': False}
- v1 reassign@hard: None
- v2 reassign@hard: None

no reassign event at hard expiry in this cell; consequential DENY_HARD_EXPIRY covered by unit tests
