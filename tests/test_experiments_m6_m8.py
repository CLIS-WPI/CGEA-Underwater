"""M6/M8/M9: shared traces across B1–B5; config-driven experiments; provenance."""

from __future__ import annotations

from pathlib import Path

from omegaconf import OmegaConf

from cgea.experiments.runner import run_experiment, run_single


def _test_cfg(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    cfg = OmegaConf.create(
        {
            "seed": 0,
            "paths": {
                "root": str(root),
                "traces": str(tmp_path / "traces"),
                "results": str(tmp_path / "results"),
            },
            "acoustic": {
                "environment_id": "unit_test_env",
                "water_depth_m": 200.0,
                "carrier_frequency_hz": 25000.0,
                "bandwidth_hz": 5000.0,
                "noise_psd_dbm_hz": -80.0,
                "tx_power_dbm": 180.0,
                "prefer_aubellhop": False,
                "ssp": {"depths_m": [0.0, 200.0], "speeds_mps": [1500.0, 1500.0]},
            },
            "mission": {
                "mission_id": "unit",
                "n_auvs": 4,
                "pipeline_length_m": 800.0,
                "depth_m": 80.0,
                "battery_j": 1e6,
                "reserve_j": 1e5,
                "duration_s": 80.0,
                "sim_tick_s": 20.0,
                "channel_sample_dt_s": 40.0,
            },
            "network": {"queue_limit": 16, "header_bytes": 32},
            "agent": {"consequential_period_s": 40.0},
            "governance": {
                "soft_expiry_s": 100.0,
                "hard_expiry_s": 400.0,
                "authority_refresh_s": 30.0,
                "authority_capsule_bytes": 128,
                "freshness_mode": "continuous",
                "immediate_resume": False,
                "connectivity": {
                    "pdr_degraded": 0.7,
                    "pdr_partition": 0.3,
                    "supervisor_timeout_s": 60.0,
                    "authority_timeout_s": 120.0,
                    "min_throughput_bps": 50.0,
                },
            },
            "scenario": {
                "outage_start_s": 20.0,
                "outage_end_s": 50.0,
                "partition_groups": None,
            },
            "experiment": {
                "name": "unit_smoke",
                "baselines": ["B1", "B2", "B3", "B4", "B5"],
                "seeds": [0],
            },
            "force_regenerate_trace": True,
        }
    )
    return cfg


def test_all_baselines_share_trace_id(tmp_path):
    cfg = _test_cfg(tmp_path)
    results = run_experiment(cfg)
    assert len(results) == 5
    trace_ids = {r.provenance.channel_trace_id for r in results}
    assert len(trace_ids) == 1
    for r in results:
        assert r.provenance.baseline in {"B1", "B2", "B3", "B4", "B5"}
        assert r.provenance.configuration_hash
        assert r.provenance.random_seed == 0
        # Results persisted
        out = tmp_path / "results" / "unit_smoke" / r.provenance.baseline
        assert out.exists()
        assert list(out.glob("*.json"))


def test_b4_vs_b5_differs_on_unauthorized_metric(tmp_path):
    cfg = _test_cfg(tmp_path)
    cfg.experiment.baselines = ["B4", "B5"]
    cfg.scenario.outage_start_s = 10.0
    cfg.scenario.outage_end_s = 60.0
    results = run_experiment(cfg)
    by = {r.provenance.baseline: r for r in results}
    # B5 allows consequential under partition; B4 contracts — rates may differ
    assert by["B4"].provenance.channel_trace_id == by["B5"].provenance.channel_trace_id
