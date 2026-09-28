"""GPU-batched OFDM PHY on Bellhop CIR tensors.

CIR source is Bellhop. Sionna/torch apply a generic OFDM PHY (modulation,
AWGN, ZF equalization, BER/BLER). This is an abstraction over an acoustic
channel, not a 3GPP TDL/CDL model.

Limitation: the frequency response is static. Path delays/coefficients are not
time-evolved with platform motion, so Doppler is not modeled. Evaluation covers
multipath, attenuation, and delay spread only.
"""

from __future__ import annotations

import math

import torch

from cgea.phy.config import PhyConfig


def _complex_dtype(precision: str) -> torch.dtype:
    return torch.complex128 if precision == "complex128" else torch.complex64


def _real_dtype(precision: str) -> torch.dtype:
    return torch.float64 if precision == "complex128" else torch.float32


def packed_cir_to_sionna_tensors(
    coeffs: torch.Tensor,
    delays: torch.Tensor,
    mask: torch.Tensor,
    num_time_steps: int = 1,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Vectorized Sionna ChannelModel shapes from packed Bellhop paths.

    coeffs, delays, mask: [batch, paths]
    a: [batch, 1, 1, 1, 1, paths, time]
    tau: [batch, 1, 1, paths]
    """
    a = (coeffs * mask).to(coeffs.dtype)[:, None, None, None, None, :, None]
    if num_time_steps != 1:
        a = a.expand(-1, -1, -1, -1, -1, -1, num_time_steps).contiguous()
    tau = delays[:, None, None, :]
    return a, tau


def frequency_response(
    coeffs: torch.Tensor,
    delays: torch.Tensor,
    mask: torch.Tensor,
    n_sc: int,
    df_hz: float,
) -> torch.Tensor:
    """H[b, k] = sum_p a_p * mask * exp(-j 2π k Δf τ_p)."""
    k = torch.arange(n_sc, device=coeffs.device, dtype=delays.dtype)
    # delays [B, P] -> [B, P, 1]; k [K] -> [1, 1, K]
    phase = -2.0 * math.pi * df_hz * delays.unsqueeze(-1) * k.view(1, 1, -1)
    phasor = torch.complex(torch.cos(phase), torch.sin(phase)).to(coeffs.dtype)
    weighted = coeffs.unsqueeze(-1) * mask.unsqueeze(-1).to(coeffs.dtype) * phasor
    return weighted.sum(dim=1)


def modulate(bits: torch.Tensor, modulation: str) -> torch.Tensor:
    """bits: [..., n_bits] with last dim 1 (BPSK) or 2 (QPSK)."""
    if modulation == "bpsk":
        return (1.0 - 2.0 * bits[..., 0].to(torch.float32)).to(torch.complex64)
    # QPSK Gray
    b0 = bits[..., 0].to(torch.float32)
    b1 = bits[..., 1].to(torch.float32)
    scale = math.sqrt(0.5)
    re = (1.0 - 2.0 * b0) * scale
    im = (1.0 - 2.0 * b1) * scale
    return torch.complex(re, im)


def demodulate(symbols: torch.Tensor, modulation: str) -> torch.Tensor:
    if modulation == "bpsk":
        return (symbols.real < 0).to(torch.int8).unsqueeze(-1)
    b0 = (symbols.real < 0).to(torch.int8)
    b1 = (symbols.imag < 0).to(torch.int8)
    return torch.stack((b0, b1), dim=-1)


@torch.no_grad()
def simulate_ofdm_monte_carlo(
    coeffs: torch.Tensor,
    delays: torch.Tensor,
    mask: torch.Tensor,
    snr_db: torch.Tensor,
    phy: PhyConfig,
    generator: torch.Generator | None = None,
) -> dict[str, torch.Tensor]:
    """Fully batched packet Monte Carlo. No Python packet loop.

    coeffs/delays/mask: [B, P]
    snr_db: [B]  (link SNR from Bellhop TL budget)
    """
    device = coeffs.device
    B = coeffs.shape[0]
    n_sc = phy.n_data_carriers
    n_sym = phy.n_symbols
    n_mc = phy.n_noise_realizations * phy.n_packet_realizations
    bps = phy.bits_per_symbol
    cdtype = _complex_dtype(phy.precision)
    rdtype = _real_dtype(phy.precision)
    coeffs = coeffs.to(cdtype)
    delays = delays.to(rdtype)
    mask = mask.to(rdtype)
    snr_db = snr_db.to(rdtype)

    H = frequency_response(coeffs, delays, mask, n_sc, phy.subcarrier_spacing_hz)  # [B, K]
    h_pow = (H.abs() ** 2).mean(dim=-1).clamp_min(1e-12)

    bits = torch.randint(
        0,
        2,
        (B, n_mc, n_sym, n_sc, bps),
        device=device,
        generator=generator,
        dtype=torch.int64,
    ).to(torch.int8)
    tx = modulate(bits, phy.modulation).to(cdtype)

    snr_lin = torch.pow(torch.tensor(10.0, device=device, dtype=rdtype), snr_db / 10.0)
    noise_var = (h_pow / snr_lin.clamp_min(1e-12)).clamp_min(1e-12)  # [B]
    std = torch.sqrt(noise_var / 2.0).to(rdtype)
    noise = torch.complex(
        torch.randn(tx.shape, device=device, dtype=rdtype, generator=generator) * std[:, None, None, None],
        torch.randn(tx.shape, device=device, dtype=rdtype, generator=generator) * std[:, None, None, None],
    ).to(cdtype)

    rx = H[:, None, None, :] * tx + noise
    eq = rx / (H[:, None, None, :] + 1e-8)
    hat = demodulate(eq, phy.modulation)
    err = hat.ne(bits).to(rdtype)
    ber = err.mean(dim=(2, 3, 4))  # [B, n_mc]
    # Packet error: any info-bit error after coding-rate thinning (hard-decision approx)
    n_info = phy.info_bits_per_packet
    flat = err.reshape(B, n_mc, -1)
    n_take = min(n_info, flat.shape[-1])
    bler = (flat[..., :n_take].sum(dim=-1) > 0).to(rdtype)
    per = bler.mean(dim=1)
    mean_ber = ber.mean(dim=1)
    psp = (1.0 - per).clamp(0.0, 1.0)
    retries = per / psp.clamp_min(1e-6)
    # Shannon-style effective rate scaled by (1-PER) and coding
    snr_lin_b = snr_lin
    cap = phy.bandwidth_hz * torch.log2(1.0 + snr_lin_b)
    rate = cap * phy.coding_rate * psp
    return {
        "ber": mean_ber,
        "bler": per,
        "packet_success_probability": psp,
        "expected_retransmissions": retries,
        "effective_rate_bps": rate,
        "freq_response_power": h_pow,
        "n_monte_carlo": torch.full((B,), float(n_mc), device=device, dtype=rdtype),
    }


def estimate_phy_bytes(batch: int, phy: PhyConfig) -> int:
    n_mc = phy.n_noise_realizations * phy.n_packet_realizations
    elems = batch * n_mc * phy.n_symbols * phy.n_data_carriers
    bpe = 16 if phy.precision == "complex128" else 8
    # tx, noise, rx, eq, bits (~0.25)
    return int(elems * bpe * 5.5)


def max_batch_for_memory(phy: PhyConfig, mem_frac: float = 0.72, device: torch.device | None = None) -> int:
    if device is None or device.type != "cuda" or not torch.cuda.is_available():
        return 64
    free, _total = torch.cuda.mem_get_info(device)
    budget = int(free * mem_frac)
    # binary search batch
    lo, hi, best = 8, 4096, 8
    while lo <= hi:
        mid = (lo + hi) // 2
        if estimate_phy_bytes(mid, phy) <= budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return max(8, best)
