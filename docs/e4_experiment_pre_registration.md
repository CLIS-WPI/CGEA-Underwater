# E4 experiment pre-registration (frozen conceptually; not run)

**Do not run this campaign in E4-DESIGN.**  
**Do not change this split or these baselines after implementation starts.**

## Baselines (locked set)

| ID | Name | Status |
|----|------|--------|
| B0 | B4-NoFreshness (`b4_no_freshness_v1`) | locked |
| B1 | Fixed-expiry lease `fixed_expiry_v1`, **TTL = 400 s** | locked (E3 selection) |
| B2 | CGEA B4 `paper_risk_bounded_v1_2026-09-28` | locked |
| B3 | Action-dependent evidence authority `action_evidence_policy_v1` | new |

No fifth baseline after seeing B3.

## Independent factors (conceptual)

1. Mission-state change timing (global recovery / no_change), local snapshot unchanged  
2. Outage / update delay (reuse induced partition 200–1200 s unless a registered variant is added *before* DEV)  
3. Proposal / authority age at the controlled action  
4. Action type (at least reassignment **and** one local action; do not pool only reassignment)

Policy thresholds are **not** a TEST factor.

## DEV / TEST split

Same as E3:

- **DEV:** seeds 0,1,2,3,4 × three environments → 15 clusters  
- **TEST:** seeds 5,6,7,8,9 × three environments → 15 clusters  

Bootstrap: cluster-preserving, 10 000 resamples, seed **20260929**.

## Success criterion (B3 useful only if)

On held-out TEST, B3 improves the tradeoff relative to **both** B1 and B2, via at least one of:

- **A.** Higher useful-valid execution, obsolete **no worse**  
- **B.** Lower obsolete execution, useful-valid **no worse**  
- **C.** Pareto dominance over both on a **predeclared** action×context region, hard-safety not worse  

If B3 is only another interior tradeoff point: **do not claim superiority.**

Useful-valid / obsolete stay **event-level** definitions from E2/E3 (local authorize vs global oracle). Hard-safety remains the four frozen classes only.

## Reporting (mandatory slices)

Not one pooled headline. Report by:

- action class (local vs cross-agent vs supervisory)  
- evidence type that failed or passed  
- context-valid vs context-obsolete  
- connectivity at decision  

## Overhead / cost

Use replayed `size_bits / estimated_rate_bps` airtime (already in network). Add evidence-refresh bytes/airtime if new packets appear. Optional host-side governor/store latency; no embedded-AUV claim.

## Traces

Existing 30 production GPU traces only. `forbid_trace_generation = true`.
