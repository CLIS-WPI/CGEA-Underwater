"""CPU Bellhop → GPU Sionna/OFDM PHY → compact ChannelTrace for SimPy replay."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch

from cgea.acoustic.bellhop import BellhopEngine
from cgea.acoustic.bellhop_pool import attach_link_budget, env_to_job_fields, parallel_bellhop
from cgea.acoustic.cir_batch import PackedCIRBatch, pack_realizations
from cgea.acoustic.link_lut import LinkQualityLUT, build_snr_lut
from cgea.acoustic.trace import ChannelTrace, ChannelTraceStore, LinkSample, make_trace_id
from cgea.phy.config import PAPER_PHY, PhyConfig
from cgea.phy.ofdm import max_batch_for_memory, packed_cir_to_sionna_tensors, simulate_ofdm_monte_carlo
from cgea.types import Position3D, git_commit


def _device(explicit: str | None = None) -> torch.device:
    if explicit:
        return torch.device(explicit)
    vis = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    if torch.cuda.is_available():
        return torch.device("cuda:0")
    return torch.device("cpu")


def _gpu_meta(device: torch.device) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "torch_version": torch.__version__,
        "cuda_device": str(device),
        "gpu_uuid": None,
        "gpu_name": None,
    }
    try:
        import sionna

        meta["sionna_version"] = str(sionna.__version__)
    except Exception:
        meta["sionna_version"] = None
    if device.type == "cuda" and torch.cuda.is_available():
        props = torch.cuda.get_device_properties(device)
        meta["gpu_name"] = props.name
        meta["gpu_uuid"] = str(getattr(props, "uuid", ""))
        meta["gpu_total_memory_bytes"] = int(props.total_memory)
    return meta


def build_bellhop_jobs(
    engine: BellhopEngine,
    node_positions: dict[str, Position3D],
    times: list[float],
    seed: int,
    cache_links: bool = True,
) -> list[dict[str, Any]]:
    """One Bellhop job per directed link (positions frozen) × time if not cached."""
    base = env_to_job_fields(engine)
    base["seed"] = seed
    node_ids = list(node_positions.keys())
    jobs: list[dict[str, Any]] = []
    links = [(tx, rx) for tx in node_ids for rx in node_ids if tx != rx]
    stamps = [float(times[0])] if cache_links else [float(t) for t in times]
    for t in stamps:
        for tx, rx in links:
            ptx, prx = node_positions[tx], node_positions[rx]
            jobs.append(
                {
                    **base,
                    "tx_id": tx,
                    "rx_id": rx,
                    "timestamp": t,
                    "tx_x": ptx.x,
                    "tx_y": ptx.y,
                    "tx_z": ptx.z,
                    "rx_x": prx.x,
                    "rx_y": prx.y,
                    "rx_z": prx.z,
                }
            )
    return jobs


def persist_raw_bellhop(rows: list[dict[str, Any]], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for r in rows:
        payload.append(
            {
                **{k: v for k, v in r.items() if k not in ("path_coefficients", "path_delays")},
                "path_delays": np.asarray(r["path_delays"], dtype=np.float64),
                "path_coefficients_ri": np.stack(
                    [
                        np.asarray([c.real for c in r["path_coefficients"]], dtype=np.float64),
                        np.asarray([c.imag for c in r["path_coefficients"]], dtype=np.float64),
                    ],
                    axis=0,
                ),
            }
        )
    np.savez_compressed(path, n=len(payload), raw=np.array(payload, dtype=object))
    return path


def evaluate_packed_on_gpu(
    packed: PackedCIRBatch,
    phy: PhyConfig,
    device: torch.device,
    seed: int,
) -> list[dict[str, Any]]:
    B = packed.coeffs.shape[0]
    max_b = max_batch_for_memory(phy, device=device) if device.type == "cuda" else min(64, B)
    out: list[dict[str, Any]] = []
    gen = torch.Generator(device=device if device.type == "cuda" else "cpu")
    gen.manual_seed(seed)
    # Touch Sionna-shaped tensors so ChannelModel layout is actually used
    for start in range(0, B, max_b):
        sl = slice(start, min(start + max_b, B))
        a_s, tau_s = packed_cir_to_sionna_tensors(
            packed.coeffs[sl], packed.delays[sl], packed.mask[sl], num_time_steps=1
        )
        _ = a_s.sum()  # keep graph live on GPU
        snr = torch.as_tensor(packed.snr_db[sl], device=device, dtype=packed.delays.dtype)
        stats = simulate_ofdm_monte_carlo(
            packed.coeffs[sl],
            packed.delays[sl],
            packed.mask[sl],
            snr,
            phy,
            generator=gen,
        )
        n = sl.stop - sl.start
        ber = stats["ber"].detach().cpu().numpy()
        per = stats["bler"].detach().cpu().numpy()
        psp = stats["packet_success_probability"].detach().cpu().numpy()
        rate = stats["effective_rate_bps"].detach().cpu().numpy()
        retries = stats["expected_retransmissions"].detach().cpu().numpy()
        for i in range(n):
            idx = start + i
            out.append(
                {
                    "timestamp": float(packed.timestamp[idx]),
                    "tx_id": packed.tx_id[idx],
                    "rx_id": packed.rx_id[idx],
                    "distance_m": float(packed.distance_m[idx]),
                    "tx_depth_m": float(packed.tx_depth_m[idx]),
                    "rx_depth_m": float(packed.rx_depth_m[idx]),
                    "path_loss_db": float(packed.path_loss_db[idx]),
                    "snr_db": float(packed.snr_db[idx]),
                    "delay_spread_s": float(packed.delay_spread_s[idx]),
                    "doppler_hz": float(packed.doppler_hz[idx]),
                    "propagation_delay_s": float(packed.propagation_delay_s[idx]),
                    "packet_success_probability": float(psp[i]),
                    "effective_rate_bps": float(rate[i]),
                    "expected_retransmissions": float(retries[i]),
                    "ber": float(ber[i]),
                    "bler": float(per[i]),
                    "link_available": bool(psp[i] >= 0.1 and packed.snr_db[idx] >= phy.snr_threshold_db),
                    "sionna_a_shape": list(a_s.shape),
                    "sionna_tau_shape": list(tau_s.shape),
                    "path_bucket": packed.bucket,
                    "n_monte_carlo": int(phy.n_noise_realizations * phy.n_packet_realizations),
                }
            )
        del a_s, tau_s, stats
        if device.type == "cuda":
            torch.cuda.synchronize(device)
    return out


def generate_mission_trace_gpu(
    engine: BellhopEngine,
    node_positions: dict[str, Position3D],
    times: list[float],
    seed: int,
    noise_psd_dbm_hz: float,
    bandwidth_hz: float,
    tx_power_dbm: float,
    phy: PhyConfig | None = None,
    device: str | None = None,
    max_workers: int | None = None,
    raw_npz: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[ChannelTrace, dict[str, Any], LinkQualityLUT]:
    """Parallel Bellhop + batched GPU PHY. SimPy must only replay the returned trace."""
    phy = phy or PAPER_PHY
    phy = phy.model_copy(update={"bandwidth_hz": bandwidth_hz, "snr_threshold_db": phy.snr_threshold_db})
    dev = _device(device)
    t0 = time.perf_counter()
    jobs = build_bellhop_jobs(engine, node_positions, times, seed, cache_links=True)
    rows = parallel_bellhop(jobs, max_workers=max_workers)
    t_bellhop = time.perf_counter() - t0
    backends = {r["backend"] for r in rows}
    if not engine.allow_fallback and backends and backends != {"aubellhop"}:
        raise RuntimeError(f"Paper traces must use aubellhop only; got {sorted(backends)}")
    rows = attach_link_budget(rows, tx_power_dbm, noise_psd_dbm_hz, bandwidth_hz)
    if raw_npz is not None:
        persist_raw_bellhop(rows, raw_npz)

    t1 = time.perf_counter()
    packed_list = pack_realizations(rows, device=dev, precision=phy.precision)
    phy_rows: list[dict[str, Any]] = []
    batch_sizes = []
    for packed in packed_list:
        batch_sizes.append(int(packed.coeffs.shape[0]))
        phy_rows.extend(evaluate_packed_on_gpu(packed, phy, dev, seed))
    if dev.type == "cuda":
        torch.cuda.synchronize(dev)
    t_phy = time.perf_counter() - t1

    # Replay frozen geometry over mission times (same contract as CPU generator)
    by_link_phy = {(r["tx_id"], r["rx_id"]): r for r in phy_rows}
    by_link_cir = {(r["tx_id"], r["rx_id"]): r for r in rows}
    samples: list[LinkSample] = []
    for t in times:
        for (tx, rx), r in by_link_phy.items():
            cir = by_link_cir[(tx, rx)]
            samples.append(
                LinkSample(
                    timestamp=float(t),
                    tx_id=tx,
                    rx_id=rx,
                    tx_position=node_positions[tx],
                    rx_position=node_positions[rx],
                    distance_m=r["distance_m"],
                    path_delays=cir["path_delays"],
                    path_coefficients=cir["path_coefficients"],
                    propagation_delay_s=r["propagation_delay_s"],
                    estimated_rate_bps=r["effective_rate_bps"],
                    packet_success_probability=r["packet_success_probability"],
                    link_available=r["link_available"],
                    snr_db=r["snr_db"],
                    doppler_hz=r["doppler_hz"],
                    delay_spread_s=r["delay_spread_s"],
                    path_loss_db=r["path_loss_db"],
                    expected_retransmissions=r["expected_retransmissions"],
                    ber=r["ber"],
                    bler=r["bler"],
                )
            )

    lut = build_snr_lut(phy_rows, phy.phy_config_hash())
    trace_id = make_trace_id(engine.env.environment_id, seed, len(node_positions), float(times[-1] if times else 0.0))
    trace_id = trace_id + "_gpu"
    trace = ChannelTrace(
        trace_id=trace_id,
        environment_id=engine.env.environment_id,
        seed=seed,
        carrier_frequency_hz=engine.env.carrier_frequency_hz,
        samples=samples,
    )
    peak_mem = 0
    if dev.type == "cuda":
        peak_mem = int(torch.cuda.max_memory_allocated(dev))
    meta = {
        **_gpu_meta(dev),
        "pipeline": "bellhop_parallel+sionna_ofdm_mc",
        "bellhop_backend": sorted(backends)[0] if backends else engine.backend,
        "batch_sizes": batch_sizes,
        "max_batch_size": max(batch_sizes) if batch_sizes else 0,
        "monte_carlo_samples": phy.n_noise_realizations * phy.n_packet_realizations,
        "phy_config": phy.model_dump(),
        "phy_config_hash": phy.phy_config_hash(),
        "git_commit": git_commit(repo_root or Path(".")),
        "runtime_s": {
            "bellhop_parallel": t_bellhop,
            "gpu_phy": t_phy,
            "total": time.perf_counter() - t0,
        },
        "n_links": len(by_link_phy),
        "n_times": len(times),
        "gpu_peak_memory_bytes": peak_mem,
        "use_sionna_bridge": True,
        "gpu_phy": True,
        "doppler_mode": "not_modeled",
        "doppler_limitation": (
            "GPU PHY uses a static Bellhop CIR (multipath, attenuation, delay). "
            "Along-path Doppler is not applied to OFDM symbols."
        ),
        "allow_fallback": bool(engine.allow_fallback),
        "lut": lut.model_dump(),
        "note": "B1-B5 must replay this same persisted trace; SimPy does not recompute PHY.",
    }
    return trace, meta, lut


def gpu_stress_batch(
    phy: PhyConfig,
    n_links: int,
    device: str | None = None,
    seed: int = 0,
    n_iters: int = 1,
) -> dict[str, Any]:
    """Large synthetic CIR batch to measure H100 utilization (not used as paper traces)."""
    dev = _device(device)
    if dev.type == "cuda":
        torch.cuda.reset_peak_memory_stats(dev)
        torch.cuda.synchronize(dev)
    P = 32
    g = torch.Generator(device=dev if dev.type == "cuda" else "cpu")
    g.manual_seed(seed)
    cdtype = torch.complex64
    coeffs = torch.randn(n_links, P, generator=g, device=dev, dtype=torch.float32) + 1j * torch.randn(
        n_links, P, generator=g, device=dev, dtype=torch.float32
    )
    coeffs = coeffs.to(cdtype) * 0.05
    delays = torch.rand(n_links, P, generator=g, device=dev) * 0.02
    mask = torch.ones(n_links, P, device=dev)
    snr = torch.linspace(-5, 25, n_links, device=dev)
    t0 = time.perf_counter()
    a, tau = packed_cir_to_sionna_tensors(coeffs, delays, mask)
    stats = None
    for _ in range(max(1, n_iters)):
        stats = simulate_ofdm_monte_carlo(coeffs, delays, mask, snr, phy, generator=g)
    if dev.type == "cuda":
        torch.cuda.synchronize(dev)
    dt = time.perf_counter() - t0
    n_pkt = n_links * phy.n_noise_realizations * phy.n_packet_realizations * max(1, n_iters)
    peak = int(torch.cuda.max_memory_allocated(dev)) if dev.type == "cuda" else 0
    total = int(torch.cuda.get_device_properties(dev).total_memory) if dev.type == "cuda" else 0
    return {
        "n_iters": n_iters,
        "n_links": n_links,
        "sionna_a_shape": list(a.shape),
        "runtime_s": dt,
        "packets_per_s": n_pkt / max(dt, 1e-9),
        "links_per_s": n_links / max(dt, 1e-9),
        "gpu_peak_memory_bytes": peak,
        "gpu_total_memory_bytes": total,
        "gpu_memory_frac": peak / total if total else 0.0,
        "mean_ber": float(stats["ber"].mean().cpu()),
        "mean_per": float(stats["bler"].mean().cpu()),
        "device": str(dev),
        **_gpu_meta(dev),
    }
