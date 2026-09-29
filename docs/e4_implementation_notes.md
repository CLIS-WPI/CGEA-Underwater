# E4 implementation notes (validation only; no DEV/TEST campaign)

**Parent design SHA:** `4d2df2e95636c9a314083a54f33aa4d1a9fccd73`  
**Implementation SHA:** `551c169e46e5652b0e3a698d713562504c180cfd`

## B3 decision pipeline

```
static hard-safety / PAPER forbid
    → capsule grants / action eligibility
    → action-specific evidence requirement evaluation
    → existing conditional predicate (recruits; B3 does not use live global mission state)
    → ALLOW / DENY / DEFER
```

B2 `contract_capsule` / `classify_freshness` aging-stale bands and `DENY_HARD_EXPIRY` are **not** used on `authority_mode=evidence`.

## Frozen flags

- `apply_cgea_multistage_contraction: false`
- `apply_capsule_hard_expiry_deny: false`

## Frozen budgets (from YAML; not duplicated as runtime knobs)

| Type | s |
|------|--:|
| LOCAL_NAVIGATION | 40 |
| LOCAL_OBSERVATION | 60 |
| LOCAL_ENERGY | 60 |
| PEER_AVAILABILITY | 300 |
| SEGMENT_ASSIGNMENT | 600 |
| MISSION_OBJECTIVE | TBD (null) |
| SUPERVISOR_AUTHORITY | TBD (null) |

## Global-truth firewall

`evaluate_requirements` takes `(proposal, evidence_store, now, policy)` only. Tests assert its source does not reference `failed_auv_ids` or `world.segments`.

Runner B3 `conditional_ok` is **recruits only**. Remote predicates are store-only.

Packet/recon refresh copies an **explicit supervisor payload map** built at the comms event. A live world mutation without a packet does not `put` remote records.

## Local / remote refresh

- **LOCAL_***: `put_local_self` each tick from own sensors (`trusted_at=now`).
- **Remote:** ages unless snapshot populate, authority packet, or recon `refresh_known_remote_from_supervisor_view`.
- **RECOVERING:** existing defer/deny for consequential remains; remote `trusted_at` advances only after recon/authority events.

## Conditional_ok (B3 vs historical)

| Mode | Source |
|------|--------|
| B0/B1/B2 E2 | `local_conditional_ok(snapshot)` |
| B0/B1/B2 non-E2 | `reassign_condition_satisfied(..., world.failed_auv_ids, live segment)` **unchanged** |
| B3 evidence | recruits vs `max_neighbor_recruits`; evidence predicates in the resolver |

## Files changed (implementation)

- `src/cgea/governance/evidence.py` (new)
- `src/cgea/governance/__init__.py`
- `src/cgea/baselines/__init__.py`
- `src/cgea/experiments/runner.py`
- `tests/test_evidence_store.py`
- `tests/test_evidence_authority.py`
- `tests/test_e4_integration.py`
- `docs/e4_implementation_notes.md`
- `results/e4_implementation_validation/*`

## Unresolved

- Full-mission E4 runner logging is wired but **not** exercised as a DEV/TEST campaign.
- `DEFER_EVIDENCE_REFRESH` is reserved; missing evidence currently DENYs.
- `MISSION_OBJECTIVE` / `SUPERVISOR_AUTHORITY` remain TBD (static forbid still wins).
