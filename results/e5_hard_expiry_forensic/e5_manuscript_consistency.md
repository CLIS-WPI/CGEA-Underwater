# Manuscript consistency (no TeX edits)

Files checked: `CGEA_Updated_Overleaf_Final.tex` (workspace root) and `cgea-underwater/paper/overleaf/main.tex`.

## Sentences that are incorrect or incomplete vs implementation

### CGEA_Updated_Overleaf_Final.tex

Line 27 (abstract): “A risk-bounded paper policy allows low-risk actions, conditionally permits bounded task reassignment, and forbids four high-impact action classes.”
Issue: hard-expiry `contract_capsule` forbids every ActionType except `fallback_action` (`surfacing_safe_mode`), including LOW_RISK classes. The abstract states low-risk as allowed without the HARD_EXPIRED exception.

Line 71: “Allowed: low-risk actions such as collision avoidance, bounded path correction, repeated sonar scan, and local inference;”
Issue: these are paper-capsule grants for non-HARD_EXPIRED freshness. At HARD_EXPIRED they are placed in `forbidden_actions`. Collision avoidance is listed as allowed but was never proposed in production B4; path correction and hold_station were denied via DENY_FORBIDDEN after contraction.

Line 76: “The freshness thresholds are also frozen: aging at 180 s, stale at 400 s, hard expiry at 900 s …”
Issue: does not state that hard expiry replaces the allowed set with only the fallback action rather than only stripping consequential authority.

Line 161: “encodes bounded execution authority in local capsules, contracts that authority with freshness”
Issue: readers can infer that contraction is limited to consequential authority. Implementation at HARD_EXPIRED admits no LOW_RISK execution except if the action is the configured fallback (surfacing_safe_mode, which is not in LOW_RISK_ACTIONS).

### cgea-underwater/paper/overleaf/main.tex

Line 33: “Low-risk inspection actions are allowed when not otherwise expired.”
This is closer to code. Still incomplete: STALE keeps LOW_RISK; HARD_EXPIRED does not. “Expired” is ambiguous between stale and hard expiry.

Line 35: “Freshness bands … contract allowed sets further.”
True, but does not disclose that HARD_EXPIRED forbids collision avoidance / path correction / hold / scan, not only consequential classes.

Line 57: “contracts further when stale.”
Stale forbids consequential and keeps LOW_RISK. Hard expiry is a stricter, different contraction. This sentence understates HARD_EXPIRED.
