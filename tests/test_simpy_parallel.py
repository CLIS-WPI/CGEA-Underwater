"""Parallel SimPy replay must match sequential run_single exactly."""

from __future__ import annotations

import json
from pathlib import Path

from omegaconf import OmegaConf

from cgea.experiments.parallel import cfg_to_container, metrics_from_worker, run_simpy_jobs
from cgea.experiments.runner import ensure_trace, run_single
from cgea.metrics import RunMetrics
from cgea.mission import build_pipeline_mission


def _test_cfg(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    return OmegaConf.create(
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
                "allow_fallback": True,
                "use_sionna_bridge": False,
                "use_gpu_phy": False,
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
                "workload": "nominal",
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
                "outage_disabled": False,
            },
            "experiment": {
                "name": "unit_parallel",
                "baselines": ["B1", "B4"],
                "seeds": [0, 1],
            },
            "force_regenerate_trace": False,
        }
    )


def _canonical(metrics: RunMetrics) -> str:
    dumped = metrics.model_dump(mode="json")
    return json.dumps(dumped, sort_keys=True, separators=(",", ":"))


def test_parallel_matches_sequential_bit_for_bit(tmp_path):
    cfg = _test_cfg(tmp_path)
    jobs = []
    for seed in cfg.experiment.seeds:
        cfg_s = OmegaConf.merge(cfg, {"seed": int(seed), "force_regenerate_trace": False})
        world = build_pipeline_mission(
            n_auvs=int(cfg_s.mission.n_auvs),
            pipeline_length_m=float(cfg_s.mission.pipeline_length_m),
            depth_m=float(cfg_s.mission.depth_m),
            battery_j=float(cfg_s.mission.battery_j),
            reserve_j=float(cfg_s.mission.reserve_j),
            mission_id=str(cfg_s.mission.mission_id),
            workload=str(cfg_s.mission.get("workload", "nominal")),
        )
        positions = {aid: a.position for aid, a in world.auvs.items()}
        trace = ensure_trace(cfg_s, positions)
        for baseline in cfg.experiment.baselines:
            cfg_run = OmegaConf.merge(
                cfg_s,
                {
                    "experiment": {"name": f"unit_parallel_{baseline}_{seed}"},
                    "forbid_trace_generation": True,
                },
            )
            jobs.append(
                {
                    "seed": int(seed),
                    "outage": "unit",
                    "baseline": str(baseline),
                    "expected_trace_id": trace.trace_id,
                    "cfg": cfg_to_container(cfg_run),
                }
            )

    sequential = []
    for job in jobs:
        sequential.append(run_single(OmegaConf.create(job["cfg"]), job["baseline"]))

    parallel_payloads = run_simpy_jobs(jobs, max_workers=4)
    parallel = [metrics_from_worker(p) for p in parallel_payloads]

    assert len(parallel) == len(sequential) == 4
    for seq, par, job in zip(sequential, parallel, jobs):
        assert seq.provenance.channel_trace_id == par.provenance.channel_trace_id == job["expected_trace_id"]
        assert seq.provenance.baseline == par.provenance.baseline == job["baseline"]
        assert seq.provenance.random_seed == par.provenance.random_seed == job["seed"]
        assert _canonical(seq) == _canonical(par)


def test_pick_stable_workers_avoids_oversubscribe_and_prefers_efficiency():
    from cgea.experiments.parallel import pick_stable_workers

    rows = [
        {"workers": 1, "wall_s": 100.0, "efficiency": 1.0},
        {"workers": 16, "wall_s": 10.0, "efficiency": 0.625},
        {"workers": 24, "wall_s": 9.7, "efficiency": 0.43},
        {"workers": 32, "wall_s": 9.6, "efficiency": 0.32},
    ]
    # 24 is within 5% of 16's wall? 9.7 vs 10.0 yes; pick higher efficiency -> 16
    assert pick_stable_workers(rows, cpu_count=32) == 16
    # If 24 is clearly faster, take it
    rows[2]["wall_s"] = 6.0
    assert pick_stable_workers(rows, cpu_count=32) == 24
    # Never pick above cpu_count
    assert pick_stable_workers(rows, cpu_count=16) == 16


def test_worker_refuses_to_regenerate_trace(tmp_path):
    cfg = _test_cfg(tmp_path)
    cfg.forbid_trace_generation = True
    cfg.seed = 0
    world = build_pipeline_mission(
        n_auvs=int(cfg.mission.n_auvs),
        pipeline_length_m=float(cfg.mission.pipeline_length_m),
        depth_m=float(cfg.mission.depth_m),
        battery_j=float(cfg.mission.battery_j),
        reserve_j=float(cfg.mission.reserve_j),
        mission_id=str(cfg.mission.mission_id),
        workload="nominal",
    )
    positions = {aid: a.position for aid, a in world.auvs.items()}
    try:
        ensure_trace(cfg, positions)
        raised = False
    except RuntimeError as exc:
        raised = "refusing to generate" in str(exc)
    assert raised
