"""CPU process pool for Bellhop arrivals. Workers must not initialize CUDA."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import numpy as np

from cgea.types import Position3D


def _bellhop_worker(job: dict[str, Any]) -> dict[str, Any]:
    # Isolate CUDA from workers
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
    from cgea.acoustic.bellhop import AcousticEnvironment, BellhopEngine, BottomProperties, SoundSpeedProfile, SurfaceModel

    env = AcousticEnvironment(
        water_depth_m=job["water_depth_m"],
        carrier_frequency_hz=job["carrier_frequency_hz"],
        ssp=SoundSpeedProfile(depths_m=job["ssp_depths"], speeds_mps=job["ssp_speeds"]),
        bottom=BottomProperties(
            soundspeed_mps=job["bottom_soundspeed"],
            density_ratio=job["bottom_density"],
            attenuation_db_per_wavelength=job["bottom_att"],
        ),
        surface=SurfaceModel(boundary=job["surface_boundary"]),
        environment_id=job["environment_id"],
        seed=job["seed"],
    )
    engine = BellhopEngine(
        env,
        prefer_aubellhop=job["prefer_aubellhop"],
        allow_fallback=job["allow_fallback"],
    )
    tx = Position3D(x=job["tx_x"], y=job["tx_y"], z=job["tx_z"])
    rx = Position3D(x=job["rx_x"], y=job["rx_y"], z=job["rx_z"])
    real = engine.compute_channel(job["tx_id"], job["rx_id"], tx, rx, seed=job["seed"])
    return {
        "tx_id": real.tx_id,
        "rx_id": real.rx_id,
        "timestamp": job["timestamp"],
        "distance_m": real.range_m,
        "tx_depth_m": real.tx_depth_m,
        "rx_depth_m": real.rx_depth_m,
        "path_delays": real.path_delays_s,
        "path_coefficients": real.path_coefficients,
        "path_loss_db": real.propagation_loss_db,
        "propagation_delay_s": real.propagation_delay_s,
        "delay_spread_s": real.delay_spread_s,
        "doppler_hz": None,
        "backend": real.backend,
        "n_paths": len(real.path_delays_s),
    }


def env_to_job_fields(engine) -> dict[str, Any]:
    env = engine.env
    return {
        "water_depth_m": env.water_depth_m,
        "carrier_frequency_hz": env.carrier_frequency_hz,
        "ssp_depths": list(env.ssp.depths_m),
        "ssp_speeds": list(env.ssp.speeds_mps),
        "bottom_soundspeed": env.bottom.soundspeed_mps,
        "bottom_density": env.bottom.density_ratio,
        "bottom_att": env.bottom.attenuation_db_per_wavelength,
        "surface_boundary": env.surface.boundary,
        "environment_id": env.environment_id,
        "prefer_aubellhop": engine.prefer_aubellhop,
        "allow_fallback": engine.allow_fallback,
    }


def parallel_bellhop(
    jobs: list[dict[str, Any]],
    max_workers: int | None = None,
) -> list[dict[str, Any]]:
    if not jobs:
        return []
    workers = max_workers or max(1, min(os.cpu_count() or 4, len(jobs), 16))
    if workers == 1 or len(jobs) == 1:
        return [_bellhop_worker(j) for j in jobs]
    ctx = None
    try:
        import multiprocessing as mp

        ctx = mp.get_context("spawn")
    except Exception:
        ctx = None
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
        return list(ex.map(_bellhop_worker, jobs, chunksize=max(1, len(jobs) // (workers * 4) or 1)))


def attach_link_budget(
    rows: list[dict[str, Any]],
    tx_power_dbm: float,
    noise_psd_dbm_hz: float,
    bandwidth_hz: float,
) -> list[dict[str, Any]]:
    noise_power_db = noise_psd_dbm_hz + 10.0 * np.log10(bandwidth_hz)
    for r in rows:
        r["snr_db"] = float(tx_power_dbm - float(r["path_loss_db"]) - noise_power_db)
    return rows
