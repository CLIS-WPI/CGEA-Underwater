"""Configurable acoustic-OFDM PHY abstraction (not a native underwater modem).

Waveform is a generic OFDM stack applied to Bellhop CIRs. This is not a claim
that Sionna implements underwater acoustics natively.
"""

from __future__ import annotations

import hashlib
import json

from cgea.types import CgeaBaseModel

PHY_CONFIG_FREEZE_ID = "acoustic_ofdm_phy_v1_2026-09-28"


class PhyConfig(CgeaBaseModel):
    freeze_id: str = PHY_CONFIG_FREEZE_ID
    modulation: str = "qpsk"  # bpsk | qpsk
    coding_rate: float = 0.5
    fft_size: int = 512
    n_data_carriers: int = 384
    n_symbols: int = 32
    cp_samples: int = 128  # against underwater delay spread at 5 kHz
    bandwidth_hz: float = 5000.0
    packet_bits: int = 256  # info bits for BLER (not the full OFDM frame)
    n_noise_realizations: int = 32
    n_packet_realizations: int = 4
    precision: str = "complex64"  # complex64 | complex128
    snr_threshold_db: float = 12.0

    def phy_config_hash(self) -> str:
        raw = json.dumps(self.model_dump(), sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @property
    def bits_per_symbol(self) -> int:
        return 1 if self.modulation == "bpsk" else 2

    @property
    def info_bits_per_packet(self) -> int:
        return int(self.packet_bits)

    @property
    def subcarrier_spacing_hz(self) -> float:
        return float(self.bandwidth_hz / self.fft_size)

    @property
    def symbol_duration_s(self) -> float:
        return (self.fft_size + self.cp_samples) / self.bandwidth_hz


PAPER_PHY = PhyConfig()
HEAVY_PHY = PhyConfig(
    fft_size=1024,
    n_data_carriers=768,
    n_symbols=64,
    cp_samples=256,
    n_noise_realizations=64,
    n_packet_realizations=8,
    packet_bits=256,
)
