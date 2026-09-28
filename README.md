# CGEA underwater — paper-facing repository

Reproducible validation for **Connectivity-Governed Execution Authority (CGEA)** in intermittently connected underwater networks.

This repository is the experimental companion to the IEEE Communications Magazine draft in `paper/overleaf/`. Historical campaign folders under `results/` are **frozen artifacts**. Do not delete or relocate them.

## Scientific freeze (do not retune from outcomes)

| Item | Frozen value |
|------|----------------|
| Capsule policy | `paper_risk_bounded_v1_2026-09-28` |
| Utility | `utility_v2_frozen_2026-09-28` |
| Safe useful retention | `safe_useful_retention_v1_2026-09-28` |
| Freshness | aging 180 s, stale 400 s, hard expiry 900 s, refresh 60 s |
| Forbidden | exclusion, reserve, abandon, objective-change |
| Conditional | `reassign_another_auv` (failed target, incomplete mandatory segment, recruit limit) |

Status of campaigns: [`docs/EXPERIMENT_STATUS.md`](docs/EXPERIMENT_STATUS.md).

## Run (Docker, GPU PHY)

```bash
cd cgea-underwater
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml run --rm cgea pytest -q
```

Paper PHY: `acoustic.allow_fallback: false`, `use_sionna_bridge: true`, `use_gpu_phy: true`. Bellhop/AUBellhop generates paths; Sionna is the OFDM Monte Carlo PHY, not an RF geometric model.

Baselines **B1–B5** (including a-priori B5 variants) must replay **identical** `ChannelTrace` objects per `(environment_id, seed)`.

## Layout

```
configs/     frozen Hydra configs
src/cgea/    platform (acoustic, network, governance, baselines, experiments)
tests/
traces/      shared ChannelTrace parquet (do not regenerate per baseline)
results/     campaign outputs with provenance (keep historical folders)
docs/        experiment status
paper/overleaf/   ComMag draft
docker/
```

## Architecture (frozen)

```
MissionPlanner → ActionProposal → ExecutionGovernor → ALLOW/DENY/DEFER → ExecutionAdapter
Bellhop CIR → Sionna OFDM MC → ChannelTrace → SimPy + NetworkX
```
