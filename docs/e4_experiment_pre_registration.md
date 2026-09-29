# E4 experiment pre-registration (frozen conceptually; not run)

**Do not run this campaign in E4-DESIGN.**  
**Do not change this split or these baselines after implementation starts.**  
**Do not retune evidence budgets on DEV or after seeing TEST.**

## B3 policy composition (no B2 freshness inside B3)

| ID | Composition |
|----|-------------|
| B0 NoFreshness | static risk + conditional rule |
| B1 Lease-400 | static risk + binary **400 s** expiry + conditional rule |
| B2 CGEA | static risk + **180/400/900** contraction + conditional rule |
| B3 Evidence | static risk + **per-action evidence validity** + conditional rule |

B3 pipeline:

```
static hard-safety / PAPER forbid
  → capsule grants / action eligibility
  → action-specific evidence requirement evaluation
  → existing conditional predicate
  → ALLOW / DENY / DEFER
```

B3 does **not** apply Fresh/Aging/Stale contraction or `DENY_HARD_EXPIRY` (see spec §4.2).

## Baselines (locked set)

| ID | Name | Status |
|----|------|--------|
| B0 | B4-NoFreshness (`b4_no_freshness_v1`) | locked |
| B1 | Fixed-expiry lease `fixed_expiry_v1`, **TTL = 400 s** | locked (E3 selection) |
| B2 | CGEA B4 `paper_risk_bounded_v1_2026-09-28` | locked |
| B3 | Action-dependent evidence authority `action_evidence_policy_v1` | new |

No fifth baseline after seeing B3.

## Frozen evidence budgets (primary TEST)

| Type | s | Role |
|------|--:|------|
| LOCAL_NAVIGATION | 40 | secondary local actions |
| LOCAL_OBSERVATION | 60 | secondary local actions |
| LOCAL_ENERGY | 60 | secondary local actions |
| PEER_AVAILABILITY | 300 | **primary** reassignment |
| SEGMENT_ASSIGNMENT | 600 | **primary** reassignment |

Engineering assumptions, not optima. Sensitivity later may vary them; primary TEST may not.

## Independent factors (conceptual)

1. Mission-state change timing (global recovery / no_change), local snapshot unchanged  
2. Outage / update delay (reuse induced partition 200–1200 s unless a registered variant is added *before* DEV)  
3. Proposal timing / evidence age at the controlled action  
4. Action type: **primary = `reassign_another_auv`**; local actions collected **separately**

Policy thresholds are **not** a TEST factor.

## DEV / TEST split

Same as E3:

- **DEV:** seeds 0,1,2,3,4 × three environments → 15 clusters  
- **TEST:** seeds 5,6,7,8,9 × three environments → 15 clusters  

Bootstrap: cluster-preserving, 10 000 resamples, seed **20260929**.

## Primary scientific comparison

**Primary action:** `reassign_another_auv`  
**Primary metrics:** useful-valid execution; obsolete execution; ownership override; hard-safety  

Local-action outcomes are **secondary** (architectural differentiation only).  
**Do not** pool large numbers of local-action events with reassignment to manufacture a better aggregate Pareto result. Report local actions in a separate table.

## Success criterion (reassignment only)

B3 may claim an improved frontier only if, on held-out TEST, **for the PRIMARY reassignment analysis**, it achieves either:

- **A.** higher useful-valid execution at **no worse** obsolete execution than **both** locked B1 lease-400 **and** B2 CGEA;  
- **OR B.** lower obsolete execution at **no worse** useful-valid execution than **both**;  
- **OR C.** a **predeclared** context-region Pareto improvement that is visible **without** pooling unrelated local-action events.

Hard-safety must not worsen (four frozen classes).

Local-action preservation may support the architectural claim but **cannot by itself** establish B3 superiority on reassignment.

If B3 is only another interior tradeoff on reassignment: **do not claim superiority.**

Useful-valid / obsolete stay E2/E3 event-level definitions (local authorize vs global oracle).

## Reporting (mandatory slices)

Reassignment (primary), then local actions (secondary), by:

- evidence type that failed or passed  
- context-valid vs context-obsolete  
- connectivity at decision  

## Overhead / cost

Use replayed `size_bits / estimated_rate_bps` airtime. Add evidence-refresh bytes/airtime if new packets appear. Optional host-side latency; no embedded-AUV claim.

## Traces

Existing 30 production GPU traces only. `forbid_trace_generation = true`.
