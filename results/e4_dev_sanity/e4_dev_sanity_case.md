# E4 DEV-sanity case report

Not a scientific claim. Frozen budgets 40/60/60/300/600.

## Cell A: VALID_REMOTE_FRESH

- challenge=400.0 epoch=180 peer_refresh=None change_age=1e+18 action=reassign_another_auv

| method | decision (mode) | reason | failed type | obsolete |
|--------|-----------------|--------|-------------|----------|
| B0 | ('ALLOW', 'ALLOW_AUTHORIZED', None) |  |  | ['0'] |
| B1 | ('ALLOW', 'ALLOW_AUTHORIZED', None) |  |  | ['0'] |
| B2 | ('DENY', 'DENY_FORBIDDEN', None) |  |  | ['0'] |
| B3 | ('ALLOW', 'ALLOW_AUTHORIZED', None) |  |  | ['0'] |

B3 example evidence_ages: `{"PEER_AVAILABILITY:auv_05": 220.0, "SEGMENT_ASSIGNMENT:seg_05": 220.0}`
B3 n=15 decision=ALLOW reason=ALLOW_AUTHORIZED failed=None obsolete=0 oracle_failed=True

## Cell B: PEER_STALE_ASSIGNMENT_VALID

- challenge=500.0 epoch=180 peer_refresh=None change_age=1e+18 action=reassign_another_auv

| method | decision (mode) | reason | failed type | obsolete |
|--------|-----------------|--------|-------------|----------|
| B0 | ('ALLOW', 'ALLOW_AUTHORIZED', None) |  |  | ['0'] |
| B1 | ('ALLOW', 'ALLOW_AUTHORIZED', None) |  |  | ['0'] |
| B2 | ('DENY', 'DENY_FORBIDDEN', None) |  |  | ['0'] |
| B3 | ('DENY', 'DENY_STALE_EVIDENCE', 'PEER_AVAILABILITY') |  |  | ['0'] |

B3 example evidence_ages: `{"PEER_AVAILABILITY:auv_05": 320.0, "SEGMENT_ASSIGNMENT:seg_05": 320.0}`
B3 n=15 decision=DENY reason=DENY_STALE_EVIDENCE failed=PEER_AVAILABILITY obsolete=0 oracle_failed=True

## Cell C: PEER_VALID_ASSIGNMENT_STALE

- challenge=800.0 epoch=180 peer_refresh=560.0 change_age=1e+18 action=reassign_another_auv

| method | decision (mode) | reason | failed type | obsolete |
|--------|-----------------|--------|-------------|----------|
| B0 | ('ALLOW', 'ALLOW_AUTHORIZED', None) |  |  | ['0'] |
| B1 | ('DENY', 'DENY_LEASE_EXPIRED', None) |  |  | ['0'] |
| B2 | ('DENY', 'DENY_FORBIDDEN', None) |  |  | ['0'] |
| B3 | ('DENY', 'DENY_STALE_EVIDENCE', 'SEGMENT_ASSIGNMENT') |  |  | ['0'] |

B3 example evidence_ages: `{"PEER_AVAILABILITY:auv_05": 240.0, "SEGMENT_ASSIGNMENT:seg_05": 620.0}`
B3 n=15 decision=DENY reason=DENY_STALE_EVIDENCE failed=SEGMENT_ASSIGNMENT obsolete=0 oracle_failed=True

## Cell D: BOTH_VALID_BUT_GLOBAL_CHANGED

- challenge=300.0 epoch=180 peer_refresh=None change_age=60.0 action=reassign_another_auv

| method | decision (mode) | reason | failed type | obsolete |
|--------|-----------------|--------|-------------|----------|
| B0 | ('ALLOW', 'ALLOW_AUTHORIZED', None) |  |  | ['1'] |
| B1 | ('ALLOW', 'ALLOW_AUTHORIZED', None) |  |  | ['1'] |
| B2 | ('ALLOW', 'ALLOW_AUTHORIZED', None) |  |  | ['1'] |
| B3 | ('ALLOW', 'ALLOW_AUTHORIZED', None) |  |  | ['1'] |

B3 example evidence_ages: `{"PEER_AVAILABILITY:auv_05": 120.0, "SEGMENT_ASSIGNMENT:seg_05": 120.0}`
B3 n=15 decision=ALLOW reason=ALLOW_AUTHORIZED failed=None obsolete=1 oracle_failed=False

## Cell E: BOTH_STALE

- challenge=800.0 epoch=180 peer_refresh=None change_age=1e+18 action=reassign_another_auv

| method | decision (mode) | reason | failed type | obsolete |
|--------|-----------------|--------|-------------|----------|
| B0 | ('ALLOW', 'ALLOW_AUTHORIZED', None) |  |  | ['0'] |
| B1 | ('DENY', 'DENY_LEASE_EXPIRED', None) |  |  | ['0'] |
| B2 | ('DENY', 'DENY_FORBIDDEN', None) |  |  | ['0'] |
| B3 | ('DENY', 'DENY_STALE_EVIDENCE', 'PEER_AVAILABILITY') |  |  | ['0'] |

B3 example evidence_ages: `{"PEER_AVAILABILITY:auv_05": 620.0, "SEGMENT_ASSIGNMENT:seg_05": 620.0}`
B3 n=15 decision=DENY reason=DENY_STALE_EVIDENCE failed=PEER_AVAILABILITY obsolete=0 oracle_failed=True

## Cell F: LOCAL_ACTION_OLD_CAPSULE_FRESH_LOCAL_EVIDENCE

- challenge=1140.0 epoch=180 peer_refresh=None change_age=1e+18 action=collision_avoidance

| method | decision (mode) | reason | failed type | obsolete |
|--------|-----------------|--------|-------------|----------|
| B0 | ('ALLOW', 'ALLOW_LOW_RISK', None) |  |  | ['0'] |
| B1 | ('ALLOW', 'ALLOW_LOW_RISK', None) |  |  | ['0'] |
| B2 | ('DENY', 'DENY_FORBIDDEN', None) |  |  | ['0'] |
| B3 | ('ALLOW', 'ALLOW_LOW_RISK', None) |  |  | ['0'] |

B3 example evidence_ages: `{"LOCAL_ENERGY:auv_06": 0.0, "LOCAL_NAVIGATION:auv_06": 0.0}`
B3 n=15 decision=ALLOW reason=ALLOW_LOW_RISK failed=None obsolete=0 oracle_failed=True

