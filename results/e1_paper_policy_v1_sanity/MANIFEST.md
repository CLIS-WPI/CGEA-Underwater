# paper_risk_bounded_v1 sanity freeze

**Role:** 27-run sanity after paper-policy + hop-by-hop DIGEST + per-AUV recon. **Not a full production / paper-candidate E1.**

- Policy: `paper_risk_bounded_v1_2026-09-28` (forbid exclusion/reserve/abandon/objective-change; CONDITIONAL reassign)
- Metric freeze: `safe_useful_retention_v1_2026-09-28`
- Grid: 1 env (`paper_ssp_200m_v1`) × seeds 0,1,2 × outages 0,500,1000 × B3, B4, B5_nominal = 27
- Disconnected recon: all `partial`; mean AUV reauth fraction ≈ 0.36; timeout ≈ 0.64
- B4 hard-safety ALLOW count: 0
- Do not start 10-seed or 378-run production from this checkpoint until remaining far-AUV DIGEST timeouts are accepted or separately diagnosed.

Row `git_commit` may still stamp `HEAD` at run time (`2b57f03` if that was `HEAD`). This commit is the snapshot of the tree that produced these tables.
