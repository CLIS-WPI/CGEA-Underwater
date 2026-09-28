# CGEA Underwater Experimental Platform

Reproducible experimental validation for:

> Connectivity-Governed Execution Authority for Agentic Edge Computing
> in Intermittently Connected Underwater Networks

The research architecture is **frozen**. This repository implements
reproducible validation over Bellhop-generated underwater acoustic channels,
Sionna PHY bridging, SimPy networking, and B1–B5 governance baselines.

## Environment note

Phase-0 listed Python 3.11. Current `aubellhop` wheels require Python ≥3.12,
so the Docker image uses **Ubuntu 22.04 + Python 3.12** with PyTorch CUDA and
Sionna 2.1.x. Sionna RT must **not** be used as the underwater propagation
model; Bellhop/AUBellhop generates acoustic paths. A deterministic image-multipath
fallback keeps tests reproducible when Bellhop is unavailable.

## Quick start (all tests inside Docker)

```bash
cd cgea-underwater
docker build -f docker/Dockerfile -t cgea-underwater:latest .

# Phase-0 verification
docker run --rm --gpus all -e CUDA_VISIBLE_DEVICES=1 \
  -e CGEA_ROOT=/workspace/cgea-underwater \
  -v "$PWD:/workspace/cgea-underwater" -w /workspace/cgea-underwater \
  cgea-underwater:latest python /usr/local/bin/verify_cgea_env.py

# Full test suite
docker run --rm --gpus all -e CUDA_VISIBLE_DEVICES=1 \
  -e CGEA_ROOT=/workspace/cgea-underwater \
  -v "$PWD:/workspace/cgea-underwater" -w /workspace/cgea-underwater \
  cgea-underwater:latest pytest -q

# Smoke experiment (B1–B5, identical traces)
docker run --rm --gpus all -e CUDA_VISIBLE_DEVICES=1 \
  -e CGEA_ROOT=/workspace/cgea-underwater \
  -v "$PWD:/workspace/cgea-underwater" -w /workspace/cgea-underwater \
  cgea-underwater:latest \
  cgea-run experiment=smoke

# Paper experiments (config-driven)
cgea-run experiment=e1_outage_sweep
cgea-run experiment=e2_freshness_ablation governance.freshness_mode=continuous
cgea-run experiment=e3_partition_reconciliation governance.immediate_resume=false
cgea-run experiment=e4_cgea_vs_adaptive
cgea-run experiment=e5_governance_overhead
```

Or use compose:

```bash
cd docker && docker compose build && docker compose run --rm cgea pytest -q
```

## Architecture (frozen)

```
Agent (MissionPlanner) → ActionProposal → ExecutionGovernor → ALLOW/DENY/DEFER → ExecutionAdapter
Bellhop/AUBellhop → UnderwaterAcousticChannel(ChannelModel) → ChannelTrace → SimPy network
Baselines B1–B5 MUST replay identical ChannelTrace objects
```

## Repository layout

```
cgea-underwater/
  configs/          # Hydra configs for every experimental parameter
  src/cgea/
    acoustic/       # Bellhop engine + ChannelTrace store
    sionna_ext/     # UnderwaterAcousticChannel
    network/        # SimPy + NetworkX
    mission/        # Pipeline inspection world + frozen action taxonomy
    agent/          # Deterministic MissionPlanner + ExecutionAdapter
    governance/     # Capsules, freshness, connectivity, governor, reconciliation
    baselines/      # B1–B5
    metrics/        # Metrics + CI / effect size
    experiments/    # E1–E5 runner + CLI
  tests/
  traces/           # Persisted ChannelTrace objects (shared by B1–B5)
  results/          # Experiment outputs with provenance metadata
  notebooks/
  docker/
```

## Pass criteria coverage

| ID | Criterion | Coverage |
|----|-----------|----------|
| M1 | Bellhop validated paths | `tests/test_acoustic_m1.py`, `tests/test_aubellhop_backend.py` |
| M2 | Sionna ChannelModel bridge | `tests/test_sionna_m2.py` |
| M3 | Replayable traces for 12 AUVs | `acoustic/trace.py`, mission config |
| M4 | Partitions form and heal | `tests/test_network_m3_m4.py` |
| M5 | Agent cannot bypass governor | `tests/test_governance_m5_m7.py` |
| M6 | B1–B5 share traces | `tests/test_experiments_m6_m8.py` |
| M7 | RECOVERING blocks consequential | `tests/test_governance_m5_m7.py` |
| M8 | Config-driven experiments | `configs/`, `cgea-run` |
| M9 | Provenance for plot regeneration | `Provenance` on every `RunMetrics` |
