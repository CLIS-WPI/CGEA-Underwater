"""CPU-safe tests for packed CIR + OFDM Monte Carlo (no aubellhop required)."""

from __future__ import annotations

import torch

from cgea.acoustic.cir_batch import pack_realizations
from cgea.phy.config import PhyConfig
from cgea.phy.ofdm import packed_cir_to_sionna_tensors, simulate_ofdm_monte_carlo


def test_pack_buckets_do_not_global_pad():
    rows = []
    for n, tx in ((3, "a"), (12, "b"), (40, "c")):
        rows.append(
            {
                "tx_id": tx,
                "rx_id": "d",
                "timestamp": 0.0,
                "distance_m": 100.0,
                "tx_depth_m": 80.0,
                "rx_depth_m": 80.0,
                "path_loss_db": 40.0,
                "snr_db": 10.0,
                "delay_spread_s": 0.01,
                "doppler_hz": 0.0,
                "propagation_delay_s": 0.07,
                "path_delays": [0.07 + 0.001 * i for i in range(n)],
                "path_coefficients": [0.1 + 0.0j] * n,
            }
        )
    batches = pack_realizations(rows, device=torch.device("cpu"))
    widths = sorted(b.bucket for b in batches)
    assert 8 in widths and 16 in widths and 64 in widths
    assert max(widths) < 256


def test_ofdm_monte_carlo_shapes_cpu():
    phy = PhyConfig(n_symbols=4, n_data_carriers=16, fft_size=32, n_noise_realizations=4, n_packet_realizations=2)
    B, P = 8, 8
    coeffs = torch.ones(B, P, dtype=torch.complex64) * 0.2
    delays = torch.linspace(0.0, 0.01, P).repeat(B, 1)
    mask = torch.ones(B, P)
    snr = torch.linspace(0, 20, B)
    a, tau = packed_cir_to_sionna_tensors(coeffs, delays, mask)
    assert a.shape == (B, 1, 1, 1, 1, P, 1)
    assert tau.shape == (B, 1, 1, P)
    out = simulate_ofdm_monte_carlo(coeffs, delays, mask, snr, phy)
    assert out["ber"].shape == (B,)
    assert torch.all(out["packet_success_probability"] >= 0)
    assert torch.all(out["packet_success_probability"] <= 1)
    # Higher SNR should not be worse on average
    assert float(out["ber"][-1]) <= float(out["ber"][0]) + 0.15
