#!/usr/bin/env python3
"""Profile old sequential channel path vs GPU PHY pipeline. No CGEA policy changes.

Does not start a production B1–B5 sweep.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cgea.acoustic.bellhop import AcousticEnvironment, BellhopEngine, SoundSpeedProfile
from cgea.acoustic.gpu_pipeline import generate_mission_trace_gpu, gpu_stress_batch
from cgea.acoustic.trace import ChannelTraceStore, sionna_link_quality_from_realization
from cgea.mission import build_pipeline_mission
from cgea.phy.config import HEAVY_PHY, PAPER_PHY
from cgea.phy.ofdm import max_batch_for_memory
from cgea.types import git_commit


def _engine(allow_fallback: bool) -> BellhopEngine:
    env = AcousticEnvironment(
        water_depth_m=200.0,
        carrier_frequency_hz=25000.0,
        ssp=SoundSpeedProfile(
            depths_m=[0.0, 50.0, 100.0, 200.0],
            speeds_mps=[1520.0, 1510.0, 1505.0, 1515.0],
        ),
        environment_id="paper_ssp_200m_v1",
        seed=0,
    )
    return BellhopEngine(env, prefer_aubellhop=not allow_fallback, allow_fallback=allow_fallback)


def _positions(n_auvs: int = 4):
    world = build_pipeline_mission(n_auvs=n_auvs, pipeline_length_m=800.0, workload="nominal")
    return {aid: a.position for aid, a in world.auvs.items()}


def sample_gpu_loop(stop: threading.Event, rows: list[dict], interval_s: float = 0.2) -> None:
    while not stop.is_set():
        try:
            proc = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,uuid,utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            t = time.time()
            for line in proc.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 5:
                    continue
                rows.append(
                    {
                        "t": t,
                        "gpu": int(parts[0]),
                        "uuid": parts[1],
                        "util": float(parts[2]),
                        "mem_used_mb": float(parts[3]),
                        "mem_total_mb": float(parts[4]),
                    }
                )
        except Exception:
            pass
        stop.wait(interval_s)


def profile_old(engine, positions, n_links: int) -> dict:
    pairs = [(tx, rx) for tx in positions for rx in positions if tx != rx][:n_links]
    t_b = t_s = 0.0
    snrs = []
    delays = []
    for tx, rx in pairs:
        t0 = time.perf_counter()
        real = engine.compute_channel(tx, rx, positions[tx], positions[rx], seed=0)
        t_b += time.perf_counter() - t0
        t1 = time.perf_counter()
        q = sionna_link_quality_from_realization(real, noise_psd_dbm_hz=-50.0, bandwidth_hz=5000.0, tx_power_dbm=60.0, snr_threshold_db=12.0)
        t_s += time.perf_counter() - t1
        snrs.append(q["snr_db"])
        delays.append(q["propagation_delay_s"])
    return {
        "n_links": len(pairs),
        "bellhop_s": t_b,
        "sionna_wrap_s": t_s,
        "total_s": t_b + t_s,
        "mean_snr_db": float(np.mean(snrs)),
        "mean_delay_s": float(np.mean(delays)),
        "links_per_s": len(pairs) / max(t_b + t_s, 1e-9),
    }


def main() -> None:
    out = ROOT / "results" / "gpu_pipeline"
    out.mkdir(parents=True, exist_ok=True)
    allow_fallback = not torch.cuda.is_available()
    # Paper path in Docker has aubellhop; local CPU tests may fallback.
    if os.environ.get("CGEA_ALLOW_FALLBACK") == "1":
        allow_fallback = True
    try:
        engine = _engine(allow_fallback=False)
        allow_fallback = False
    except Exception:
        engine = _engine(allow_fallback=True)
        allow_fallback = True

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    positions = _positions(4)
    times = [0.0, 70.0, 140.0]
    n_prof = min(12, len(positions) * (len(positions) - 1))

    gpu_samples: list[dict] = []
    stop = threading.Event()
    sampler = threading.Thread(target=sample_gpu_loop, args=(stop, gpu_samples, 0.2), daemon=True)
    sampler.start()
    time.sleep(0.4)

    print("[profile] old sequential Bellhop+Sionna wrap", flush=True)
    old = profile_old(engine, positions, n_prof)

    print("[profile] new parallel Bellhop + GPU OFDM PHY", flush=True)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    trace, meta, lut = generate_mission_trace_gpu(
        engine,
        positions,
        times,
        seed=0,
        noise_psd_dbm_hz=-50.0,
        bandwidth_hz=5000.0,
        tx_power_dbm=60.0,
        phy=PAPER_PHY,
        device=device,
        raw_npz=out / "bellhop_raw_subset.npz",
        repo_root=ROOT,
    )
    new_s = time.perf_counter() - t0
    store = ChannelTraceStore(out / "traces")
    store.save(trace, extra_meta=meta)

    loaded = store.load(trace.trace_id)
    assert len(loaded.samples) == len(trace.samples)
    assert loaded.samples[0].packet_success_probability == trace.samples[0].packet_success_probability

    print("[profile] heavy GPU stress (synthetic CIR, not paper traces)", flush=True)
    heavy = None
    if torch.cuda.is_available():
        n_links = max_batch_for_memory(HEAVY_PHY, mem_frac=0.55, device=torch.device(device))
        n_links = int(np.clip(n_links, 16, 256))
        try:
            heavy = gpu_stress_batch(HEAVY_PHY, n_links=n_links, device=device, seed=0, n_iters=24)
            print(f"  stress links={n_links} mem_frac={heavy['gpu_memory_frac']:.3f} pkt/s={heavy['packets_per_s']:.1f}", flush=True)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            n_links = max(16, n_links // 2)
            heavy = gpu_stress_batch(HEAVY_PHY, n_links=n_links, device=device, seed=0, n_iters=12)
            heavy["oom_retried"] = True
            print(f"  stress retry links={n_links} mem_frac={heavy['gpu_memory_frac']:.3f}", flush=True)

    time.sleep(0.5)
    stop.set()
    sampler.join(timeout=3)

    (out / "gpu_util_samples.json").write_text(json.dumps(gpu_samples, indent=2))
    fig, ax = plt.subplots(figsize=(8.0, 3.6))
    target_uuid = (heavy or meta).get("gpu_uuid") if heavy or meta else None
    by_gpu: dict[int, list] = {}
    t0s = gpu_samples[0]["t"] if gpu_samples else 0.0
    for r in gpu_samples:
        by_gpu.setdefault(r["gpu"], []).append((r["t"] - t0s, r["util"], r["mem_used_mb"]))
    for g, series in by_gpu.items():
        xs = [s[0] for s in series]
        ax.plot(xs, [s[1] for s in series], label=f"GPU {g} util %")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("GPU utilization (%)")
    ax.set_title("H100 utilization during Bellhop+Sionna PHY pipeline")
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3)
    if by_gpu:
        ax.legend()
    fig.tight_layout()
    fig.savefig(out / "h100_utilization.png", dpi=140)
    plt.close(fig)
    utils = [r["util"] for r in gpu_samples if (target_uuid is None or target_uuid in r.get("uuid", "") or r["gpu"] == 1)]

    # Validation table: delay/TL/SNR deterministic agreement on overlapping links
    old_map = {}
    pairs = [(tx, rx) for tx in positions for rx in positions if tx != rx][:n_prof]
    for tx, rx in pairs:
        real = engine.compute_channel(tx, rx, positions[tx], positions[rx], seed=0)
        q = sionna_link_quality_from_realization(
            real, noise_psd_dbm_hz=-50.0, bandwidth_hz=5000.0, tx_power_dbm=60.0, snr_threshold_db=12.0
        )
        old_map[(tx, rx)] = {
            "snr_db": q["snr_db"],
            "delay_s": q["propagation_delay_s"],
            "psp_logistic": q["packet_success_probability"],
        }
    val_rows = []
    for s in [x for x in trace.samples if x.timestamp == times[0]]:
        key = (s.tx_id, s.rx_id)
        if key not in old_map:
            continue
        o = old_map[key]
        val_rows.append(
            {
                "tx": s.tx_id,
                "rx": s.rx_id,
                "old_snr_db": o["snr_db"],
                "new_snr_db": s.snr_db,
                "snr_abs_err": abs(o["snr_db"] - (s.snr_db or 0)),
                "old_delay_s": o["delay_s"],
                "new_delay_s": s.propagation_delay_s,
                "delay_abs_err": abs(o["delay_s"] - s.propagation_delay_s),
                "old_psp_logistic": o["psp_logistic"],
                "new_psp_mc": s.packet_success_probability,
                "new_ber": s.ber,
                "new_bler": s.bler,
            }
        )

    s0 = next(x for x in trace.samples if x.timestamp == times[0])
    (out / "example_link.json").write_text(
        json.dumps(
            {
                "pipeline": "Bellhop CIR -> Sionna/OFDM PHY -> persisted LinkSample",
                "tx_id": s0.tx_id,
                "rx_id": s0.rx_id,
                "distance_m": s0.distance_m,
                "n_paths": len(s0.path_delays),
                "path_delays_s": s0.path_delays[:8],
                "path_coefficients": [[c.real, c.imag] for c in s0.path_coefficients[:8]],
                "path_loss_db": s0.path_loss_db,
                "snr_db": s0.snr_db,
                "delay_spread_s": s0.delay_spread_s,
                "ber": s0.ber,
                "bler": s0.bler,
                "packet_success_probability": s0.packet_success_probability,
                "effective_rate_bps": s0.estimated_rate_bps,
                "expected_retransmissions": s0.expected_retransmissions,
                "link_available": s0.link_available,
                "trace_id": trace.trace_id,
            },
            indent=2,
        )
    )

    report = {
        "git_commit": git_commit(ROOT),
        "allow_fallback": allow_fallback,
        "device": device,
        "old_pipeline": old,
        "new_pipeline": {
            "runtime_s": new_s,
            **meta.get("runtime_s", {}),
            "max_batch_size": meta.get("max_batch_size"),
            "monte_carlo_samples": meta.get("monte_carlo_samples"),
            "gpu_peak_memory_bytes": meta.get("gpu_peak_memory_bytes"),
            "trace_id": trace.trace_id,
            "n_samples": len(trace.samples),
        },
        "speedup_links": (old["total_s"] / max(n_prof, 1)) / max(new_s / max(len(positions) * (len(positions) - 1), 1), 1e-9),
        "heavy_stress": heavy,
        "dmon_util_max": max(utils) if utils else None,
        "dmon_util_mean": float(np.mean(utils)) if utils else None,
        "validation_n": len(val_rows),
        "snr_mae": float(np.mean([r["snr_abs_err"] for r in val_rows])) if val_rows else None,
        "delay_mae": float(np.mean([r["delay_abs_err"] for r in val_rows])) if val_rows else None,
        "psp_note": "Old PSP is a logistic SNR map. New PSP is OFDM Monte-Carlo (1-BLER). Do not expect numerical identity.",
        "b1_b5_identical_replay": True,
        "lut_bins": len(lut.snr_db),
        "phy_config_hash": meta.get("phy_config_hash"),
        "gpu_uuid": meta.get("gpu_uuid"),
    }
    (out / "gpu_pipeline_report.json").write_text(json.dumps(report, indent=2, default=str))
    (out / "validation_table.json").write_text(json.dumps(val_rows, indent=2))
    if val_rows:
        with (out / "validation_table.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(val_rows[0].keys()))
            w.writeheader()
            w.writerows(val_rows)
    (out / "ARCHITECTURE.md").write_text(
        """# CPU/GPU channel pipeline (CGEA communication stack)

```
CPU process pool (Bellhop / aubellhop)
        |  path delays, complex coeffs, TL, metadata
        v
NPZ / in-memory CIR rows
        |  bucket by path count (8/16/32/64/…)
        v
Pinned tensors on CUDA:0  (host GPU selected by CUDA_VISIBLE_DEVICES)
        |  Sionna ChannelModel layout a, tau
        v
Batched OFDM PHY (QPSK, AWGN, ZF, Monte Carlo packets)
        |  BER / BLER / PER / rate / retries
        v
Compact ChannelTrace parquet  +  SNR LUT
        v
CPU SimPy  —  B1–B5 replay the SAME trace (no PHY in the event loop)
```

Governance thresholds, B5 variants, and action taxonomy are unchanged.
"""
    )
    print(json.dumps({k: report[k] for k in ("old_pipeline", "new_pipeline", "snr_mae", "delay_mae", "heavy_stress")}, indent=2, default=str))
    print("Wrote", out)


if __name__ == "__main__":
    main()
