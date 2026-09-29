# E4-DESIGN — Action-Dependent Evidence Authority

**Status:** frozen specification. Design only. Not implemented. Not evaluated.  
**Parent:** `3b0bc8c19fb2b1f724c745a34a1024cbcab266a6`  
**Policy object:** `action_evidence_policy_v1` (separate from `paper_risk_bounded_v1_2026-09-28`)  
**Does not retune:** CGEA 180/400/900, E3 lease TTL 400 s, utility, oracle, PHY.

---

## 1. Problem statement

CGEA currently contracts *execution authority* as a function of **authorization age**: one capsule clock, four bands (fresh / aging / stale / hard-expired). For `reassign_another_auv`, aging immediately removes the action. E3 showed that this is **not** a better Pareto policy than a tuned **400 s binary lease** on the same governor, snapshot, and traces.

The next hypothesis is architectural, not a retune of that clock:

> Execution authority for a proposed action should depend on whether the **evidence that action needs** is still present and still within a declared validity budget—not only on how old the capsule is.

That is a different question from “how old is the whole authorization?”

---

## 2. Current E3 limitation (must be preserved)

On held-out E3 TEST (15 clusters):

| Method | useful-valid | obsolete |
|--------|-------------:|---------:|
| CGEA (B4) | 0.25 | 0.045 |
| Fixed-expiry 400 s | 0.80 | 0.364 |
| NoFreshness | 1.00 | 1.00 |

CGEA does **not** dominate the 400 s lease. E4 must **not** be designed to force dominance on that same grid. E4 exists because **reassignment, local motion, and supervisory actions do not depend on the same facts**.

E2-F remains in force: **fresh authority (or fresh evidence) does not imply correct mission state.** If the last trusted peer-availability record still says `failed=true` after the target has recovered, a budgeted policy can still authorize an obsolete reassignment.

---

## 3. Evidence model

### 3.1 Evidence types (minimal)

| ID | Meaning | Typical source |
|----|---------|----------------|
| `LOCAL_OBSERVATION` | Sonar / local anomaly observation | own sensors |
| `LOCAL_ENERGY` | Battery and return-reserve estimate | own energy model |
| `LOCAL_NAVIGATION` | Position, collision flag, geofence-relative pose | own nav |
| `PEER_AVAILABILITY` | Whether a named AUV is failed / unavailable | last trusted report (not live global `failed_auv_ids`) |
| `SEGMENT_ASSIGNMENT` | Owner, mandatory, incomplete for a named segment | last trusted mission snapshot |
| `MISSION_OBJECTIVE` | High-level objective / mandatory policy text | last trusted supervisor digest |
| `SUPERVISOR_AUTHORITY` | Explicit capsule / approval for protected acts | last trusted authority capsule |

`recruitment_budget` is **not** an evidence type. It is a local operational counter already enforced by `max_neighbor_recruits`. Keep it in the existing conditional rule.

### 3.2 EvidenceRecord (minimal)

Fields the current simulator can populate without inventing sensors:

| Field | Type | Required | Notes |
|-------|------|----------|--------|
| `evidence_type` | enum | yes | above IDs |
| `value` | JSON-compatible | yes | e.g. `{failed: true}`, `{owner: auv_05, incomplete: true}` |
| `source` | string | yes | `self`, `peer:<id>`, `supervisor`, `snapshot:<id>` |
| `observed_at` | float s | yes | when the fact was observed |
| `trusted_at` | float s | yes | when it entered the local trusted store |
| `object_id` | string | yes | AUV id, segment id, or `self` |
| `version` | int | yes | monotonic per `(type, object_id)` on that AUV |
| `confidence` | float | no | default 1.0 if unused |
| `scope` | string | no | `local` / `remote` |
| `provenance_id` | string | no | capsule_id or digest id |

**Age for policy:** `now - trusted_at` unless a requirement explicitly uses `observed_at`. Default is `trusted_at`: that is when the disconnected AUV was last allowed to treat the record as authorized input.

### 3.3 Local vs global

```
Local EvidenceStore  →  planner proposal  →  requirement resolver
        →  ExecutionGovernor  →  ALLOW / DENY / DEFER

MissionWorld (global)  →  oracle / metrics only
```

The governor **must not** read `world.failed_auv_ids` or current global segment ownership to satisfy E4 requirements. That leak is forbidden (E2/E2-F already established the split).

---

## 4. Action requirement model

```
ActionEvidenceRequirement:
  action_type: str
  required_evidence: list[{type, object_binding}]
  freshness_budget_s: dict[evidence_type, float | null]
  missing_behavior: DENY | DEFER
  invalid_behavior: DENY | DEFER
```

`object_binding` is `self`, `proposal.parameters.target_auv`, or `proposal.parameters.segment_id`.

**Executability (conceptual):**

```
Executable(action) =
    StaticHardSafety(action)                 # never weakened
    AND StaticCapsuleForbid/Allow(action)    # paper grants, existing
    AND RequirementsSatisfied(action, Store) # NEW
```

Existing multi-stage capsule contraction and the locked 400 s lease remain **baselines**, not inputs that E4 is allowed to retune.

---

## 5. Capsule / governor integration

### 5.1 Capsule choice: **B — separate policy object**

Requirements live in `configs/governance/action_evidence_policy_v1.yaml`, referenced by `evidence_policy_id`. The 256-byte authority capsule stays a capability + grants object. Embedding the full matrix would grow capsules and couple PHY byte accounting to policy text.

The capsule may carry only `evidence_policy_id` (string) if a future implementation needs binding. Until then the runner loads the frozen YAML by campaign config.

### 5.2 Governor: smallest addition

Do **not** rewrite `ExecutionGovernor.decide`. Add one stage **after** static forbid / hard-safety and **before** conditional reassignment / age contraction used by B2/B3 baselines:

```
decide(..., evidence_store=None, now, proposal, ...):
    # existing RECOVERING / hard-safety / forbidden
    if evidence_store is not None and policy enabled:
        ev = evaluate_requirements(proposal, evidence_store, now, policy)
        if ev.fail:
            return DENY_* or DEFER_EVIDENCE_REFRESH
    # existing freshness / lease / conditional_ok
```

New reason codes (audit-only; do not reuse `DENY_FORBIDDEN` or `DENY_LEASE_EXPIRED`):

| Code | Meaning |
|------|---------|
| `DENY_MISSING_EVIDENCE` | required record absent |
| `DENY_STALE_EVIDENCE` | record age exceeds budget |
| `DENY_INVALID_EVIDENCE` | record present but fails a declared predicate (e.g. incomplete=false) |
| `DEFER_EVIDENCE_REFRESH` | reserved for CONNECTED/RECOVERING when a refresh is in flight |

**Planner feedback (interface only, not implemented):** governor result may later include `evidence_gaps: [{type, object_id, reason}]`. Planner stays deterministic; no adaptation in E4-DESIGN.

---

## 6. Connectivity interaction

Connectivity does **not** map to a permission. It maps to **refresh opportunity**:

| State | Refresh | Store behavior |
|-------|---------|----------------|
| CONNECTED | remote types may update when packets succeed | `trusted_at` advances on accept |
| DEGRADED | same, lower success probability | ages if updates fail |
| PARTITIONED | no supervisor/peer path for remote types | remote records age; local types still `put` from self |
| ISOLATED | no neighbors | only `source=self` |
| RECOVERING | packets may exist | remote records stay **untrusted** until digest/recon succeeds; then `trusted_at` updates |

---

## 7. Reconciliation interaction

Minimum model (no consensus, no locks):

1. Reconnect / RECOVERING  
2. DIGEST → PROVENANCE → RECONCILE → AUTHORITY (existing)  
3. On successful recon: supervisor or peer payload may `put` `PEER_AVAILABILITY`, `SEGMENT_ASSIGNMENT`, `MISSION_OBJECTIVE`, `SUPERVISOR_AUTHORITY`  
4. Re-evaluate the next proposal against the store  

**Refreshable by recon:** `PEER_AVAILABILITY`, `SEGMENT_ASSIGNMENT`, `MISSION_OBJECTIVE`, `SUPERVISOR_AUTHORITY`.  
**Not replaced by recon:** `LOCAL_*` (self sensors remain authoritative for those types).

---

## 8. Reason codes

See §5.2. Existing codes (`DENY_FORBIDDEN`, `DENY_CONDITIONAL_UNMET`, `DENY_LEASE_EXPIRED`, `DENY_HARD_EXPIRY`, …) stay for B0–B2.

---

## 9. Failure modes

| Mode | What happens | Honest bound |
|------|----------------|--------------|
| Evidence fresh but wrong | E2-F: snapshot still `target_failed=true` inside PEER budget | Policy bounds **age**, not **truth** |
| Missing | `DENY_MISSING_EVIDENCE` or DEFER if refresh pending | No guess from global world |
| Stale | `DENY_STALE_EVIDENCE` | May deny a still-valid action (E2 aging-cost analogue) |
| Conflicting local records | keep highest `version`; if versions tie, higher `trusted_at`; if still tied, **DENY_INVALID_EVIDENCE** | No silent merge |
| Source unreachable | remote types stop refreshing; they age | Same as partition |
| Local newer than remote | local types win for LOCAL_*; remote types never overwritten by local guess | No invented peer state |
| Supervisor evidence outdated | SUPERVISOR_AUTHORITY ages; protected actions stay statically forbidden | Cannot unlock hard-safety |
| State change inside validity window | E2-F class | First-class limitation |

---

## 10. Non-goals

Distributed consensus, mutual exclusion, CRDTs, locks, multi-agent negotiation, LLM planning, learned or adaptive TTLs, cryptographic capabilities, field deployment, embedded-AUV claims, manuscript edits, E4 DEV/TEST execution.

---

## 11. Future evaluation design

See `docs/e4_experiment_pre_registration.md`. Baselines B0–B3 frozen. DEV seeds 0–4, TEST 5–9. Success only if B3 improves the frontier vs **both** locked CGEA and locked 400 s lease (or a declared action/context region) without worsening hard-safety. Sitting at another tradeoff point is **not** superiority.

---

## 12. Supported future claims (if E4 TEST succeeds)

- Conditional reassignment depends on **peer-availability and assignment evidence**, not only capsule age.  
- Local sensing/motion can remain authorized under aged capsules if **local** evidence is present and in budget.  
- Hard-safety remains a separate static gate.

---

## 13. Claims that remain unsupported even after a “successful” E4

- Fresh evidence is true.  
- The mechanism dominates E3’s 400 s lease on the E3 grid (not a design target).  
- Multi-stage 180/400/900 is optimal.  
- Ownership safety / no double assignment.  
- Physical harm prevention beyond the four frozen hard-safety classes.

---

## 14. EvidenceStore API (design)

```
put(record) -> None
get_latest(evidence_type, object_id) -> EvidenceRecord | None
age(record, now) -> float          # now - trusted_at
is_valid(requirement, now) -> EvidenceCheck
invalidate(evidence_type, object_id, reason) -> None
snapshot() -> dict                 # for journals / tests
```

Per-AUV, in-memory, no sync protocol.

---

## 15. Reassignment recommendation (architecture, not E3 fit)

**Alternative A:** one budget for `PEER_AVAILABILITY` and `SEGMENT_ASSIGNMENT`.  
**Alternative B (recommended):** different budgets.

Recommendation **B**: peer availability is a **discrete remote liveness bit** that can flip without the proposer acting (recovery). Segment assignment is a **slower ledger fact** that usually changes when a reassignment is executed. Binding them to one clock reintroduces a single lease.

Chosen initial budgets (not copied from 180 or 400):

- `PEER_AVAILABILITY`: **300 s** = frozen `connectivity.authority_timeout_s` (how long the stack already treats authority-adjacent remote status as stale).  
- `SEGMENT_ASSIGNMENT`: **600 s** = \(\frac{2}{3}\) of frozen `hard_expiry_s` (900 s): assignment may remain incomplete for a long dive; a peer-liveness-tight clock would over-deny takeover of still-incomplete work.

These are **engineering defaults** for a later DEV freeze, not TEST-tuned values. If DEV cannot justify them, mark TBD and stop—do not sneak in 180 or 400 to chase E3.

---

## 16. Implementation plan (do not execute Phase 6+)

1. `EvidenceRecord` + `EvidenceStore`  
2. Load `action_evidence_policy_v1.yaml`  
3. Governor hook + reason codes  
4. Event log fields (`evidence_ages`, `evidence_decision`)  
5. Unit tests in §32 of the design prompt  
6. DEV only (seeds 0–4)  
7. Freeze evidence budgets  
8. Held-out TEST (seeds 5–9)  

---

## 17. Unit test plan (specify now; implement with Phase 5)

1. Fresh local observation + energy → `repeat_sonar_scan` not blocked by E4 evidence stage.  
2. Stale `PEER_AVAILABILITY` → reassign `DENY_STALE_EVIDENCE`.  
3. Fresh peer + assignment predicates met → evidence stage passes (static/conditional rules still apply).  
4. Missing peer record → `DENY_MISSING_EVIDENCE` (or DEFER if a refresh flag is set).  
5. `enter_exclusion_zone` + fresh LOCAL_NAVIGATION → still `DENY_FORBIDDEN`.  
6. Store populated only from snapshot; test harness must not inject `world.failed_auv_ids` into `evaluate_requirements`.  
7. E2-F: trusted peer record `failed=true` within 300 s while global target recovered → evidence stage can still pass; obsolete execution remains possible.  
8. After mocked recon `put` of `failed=false`, reassign evidence stage fails predicate or updates—document intended predicate (`requires value.failed == true` for usefulness).  
