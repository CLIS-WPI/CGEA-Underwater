#!/usr/bin/env python3
"""Stage-1 scientific sanity checks for the acoustic stack.

Produces three physically-motivated validation plots from REAL aubellhop:
  1) Propagation delay vs distance
  2) Path loss / received-power proxy vs distance
  3) Packet success / effective rate vs distance

Also asserts:
  - backend == aubellhop (no silent image-multipath fallback)
  - Sionna UnderwaterAcousticChannel consumes Bellhop paths on GPU when available
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from cgea.acoustic.bellhop import AcousticEnvironment, BellhopEngine, SoundSpeedProfile
from cgea.acoustic.trace import sionna_link_quality_from_realization
from cgea.sionna_ext import UnderwaterAcousticChannel
from cgea.types import Position3D


def main() -> None:
    out_dir = Path("results/validation/acoustic_sanity")
    out_dir.mkdir(parents=True, exist_ok=True)

    env = AcousticEnvironment(
        environment_id="paper_ssp_200m_v1",
        water_depth_m=200.0,
        carrier_frequency_hz=25_000.0,
        ssp=SoundSpeedProfile(
            depths_m=[0.0, 50.0, 100.0, 200.0],
            speeds_mps=[1520.0, 1510.0, 1505.0, 1515.0],
        ),
        seed=0,
    )
    engine = BellhopEngine(env, prefer_aubellhop=True, allow_fallback=False)
    assert engine.backend == "aubellhop", engine.backend

    tx = Position3D(x=0.0, y=0.0, z=80.0)
    ranges_m = np.linspace(100.0, 2500.0, 15)
    rows = []
    for r in ranges_m:
        rx = Position3D(x=float(r), y=0.0, z=80.0)
        real = engine.compute_channel("tx", "rx", tx, rx, seed=0)
        assert real.backend == "aubellhop", real.backend
        # Sionna bridge must be on the validation path
        ch = UnderwaterAcousticChannel(realization=real)
        a, tau = ch(batch_size=4, num_time_steps=1, sampling_frequency=5000.0)
        assert a.shape[-2] == len(real.path_coefficients)
        q = sionna_link_quality_from_realization(
            real,
            noise_psd_dbm_hz=-50.0,
            bandwidth_hz=5000.0,
            tx_power_dbm=60.0,
            snr_threshold_db=12.0,
        )
        rows.append(
            {
                "distance_m": float(r),
                "propagation_delay_s": real.propagation_delay_s,
                "propagation_loss_db": real.propagation_loss_db,
                "n_paths": len(real.arrivals),
                "bounce_pairs": [
                    [a.num_surface_bounces, a.num_bottom_bounces] for a in real.arrivals
                ],
                "snr_db": float(q["snr_db"]),
                "packet_success_probability": float(q["packet_success_probability"]),
                "estimated_rate_bps": float(q["estimated_rate_bps"]),
                "sionna_device": str(a.device),
                "backend": real.backend,
            }
        )

    # Physical plausibility checks
    delays = np.array([r["propagation_delay_s"] for r in rows])
    losses = np.array([r["propagation_loss_db"] for r in rows])
    dists = np.array([r["distance_m"] for r in rows])
    assert np.all(np.diff(delays) > -1e-6), "delay must be non-decreasing with range"
    # Spearman-ish: delay correlates with distance
    corr_d = np.corrcoef(dists, delays)[0, 1]
    corr_l = np.corrcoef(dists, losses)[0, 1]
    assert corr_d > 0.95, f"delay-distance correlation too low: {corr_d}"
    assert corr_l > 0.5, f"loss-distance correlation too low: {corr_l}"
    # Approximate c ~ distance/delay for nearest range
    c_est = dists[0] / delays[0]
    assert 1400.0 < c_est < 1600.0, f"implausible sound speed estimate {c_est}"

    # Plots
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.6))
    axes[0].plot(dists, delays, "o-", color="#0B3C5D")
    axes[0].set_xlabel("Distance (m)")
    axes[0].set_ylabel("Propagation delay (s)")
    axes[0].set_title("Delay vs distance")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(dists, losses, "o-", color="#1F7A8C")
    axes[1].set_xlabel("Distance (m)")
    axes[1].set_ylabel("Path loss proxy (dB)")
    axes[1].set_title("Loss vs distance")
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(
        dists,
        [r["packet_success_probability"] for r in rows],
        "o-",
        color="#D64045",
        label="P(success)",
    )
    ax2b = axes[2].twinx()
    ax2b.plot(
        dists,
        [r["estimated_rate_bps"] / 1000.0 for r in rows],
        "s--",
        color="#E9A825",
        label="Rate (kbps)",
    )
    axes[2].set_xlabel("Distance (m)")
    axes[2].set_ylabel("Packet success probability")
    ax2b.set_ylabel("Effective rate (kbps)")
    axes[2].set_title("Link quality vs distance")
    axes[2].grid(True, alpha=0.3)

    fig.tight_layout()
    plot_path = out_dir / "acoustic_sanity_plots.png"
    fig.savefig(plot_path, dpi=160)
    plt.close(fig)

    meta = {
        "backend": "aubellhop",
        "allow_fallback": False,
        "use_sionna_bridge": True,
        "n_ranges": len(rows),
        "delay_distance_corr": float(corr_d),
        "loss_distance_corr": float(corr_l),
        "c_est_near_mps": float(c_est),
        "sionna_device": rows[0]["sionna_device"],
        "plot": str(plot_path),
        "rows": rows,
    }
    (out_dir / "acoustic_sanity.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps({k: meta[k] for k in meta if k != "rows"}, indent=2))
    print(f"Wrote {plot_path}")


if __name__ == "__main__":
    main()
