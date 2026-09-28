# CGEA experiment status

Living status for IEEE Communications Magazine validation. **Do not delete** historical `results/` folders.

The file list in STEP 1 of the freeze note matches this tree (`README.md`, `.gitignore`, `docs/EXPERIMENT_STATUS.md`, `paper/overleaf/`). If a zip bundle is later attached, diff it against these files rather than deleting `results/`.

## Freeze (locked)

- Policy: `paper_risk_bounded_v1_2026-09-28`
- Utility: `utility_v2_frozen_2026-09-28`
- Safe useful retention: `safe_useful_retention_v1_2026-09-28`
- Freshness: aging 180 s, stale 400 s, hard expiry 900 s, authority refresh 60 s
- Environments: `paper_ssp_200m_v1`, `paper_ssp_200m_moderate_v1`, `paper_ssp_200m_strong_v1`
- Outages (s): 0, 150, 300, 500, 750, 1000 (start 200 s, mission 1400 s)
- Baselines: B1, B2, B3, B4, B5_conservative, B5_nominal, B5_permissive
- PHY: GPU Bellhop CIR → Sionna OFDM; one trace per `(environment_id, seed)`; baselines replay only

Do not retune CGEA thresholds, B5 variants, utility weights, oracle, queue reservation, reconciliation protocol, or PHY from campaign outcomes.

## Campaigns

| Campaign | Path | Role |
|----------|------|------|
| E1-v2 GPU n=3 | `results/e1_pilot_v2_gpu/` | Diagnostic (`b0d804b`). GCO tautology / capsule-circular conflicts. |
| E1-v3 GPU n=3 | `results/e1_pilot_v3_gpu/` | Diagnostic (`4d8b99e`). Semantic conflicts, mission DATA, freshness challenge. All-AUV DIGEST timeout. |
| DIGEST/B4 audit | `results/e1_v3_diagnosis/` | Direct-link vs multi-hop; broad-capsule hard-safety ALLOW. |
| paper-policy sanity n=3, 27 runs | `results/e1_paper_policy_v1_sanity/` | `4bc73b7`. Hop-by-hop DIGEST, CONDITIONAL reassign, per-AUV recon, safe useful retention. Disconnected recon **partial** (~36% AUV reauth). |
| E1 production n=10 | `results/e1_production_n10/` | Full grid (after this freeze). Headline paper metrics live here. |

## Paper headline metrics

Mission utility; hard-safety violation rate; safe useful retention; semantic conflicts (count + kind); governance / mission / total TX bytes; GCO; AUV reauth fraction; per-AUV reauth latency and timeout fraction.

Legacy useful retention and capsule-circular diagnostics remain in extras for reproducibility. They are not manuscript headlines.

## Production rule

One GPU-PHY trace per `(environment, seed)`. All baselines share that `trace_id`. 24 SimPy CPU workers. Paired tests: B4−B5_nominal and B4−B3 on shared `(environment, seed, outage)`.
