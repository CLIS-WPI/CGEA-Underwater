"""Dense LUT from GPU PHY Monte Carlo (no silent neural surrogate)."""

from __future__ import annotations

from typing import Any

import numpy as np
from cgea.types import CgeaBaseModel


class LinkQualityLUT(CgeaBaseModel):
    """Interpolates PER / rate vs SNR (and optionally delay spread)."""

    snr_db: list[float]
    per: list[float]
    rate_bps: list[float]
    ber: list[float]
    phy_config_hash: str = ""
    note: str = "dense LUT from Bellhop+Sionna OFDM Monte Carlo; not a neural surrogate"

    def interpolate(self, snr_db: float) -> dict[str, float]:
        per = float(np.interp(snr_db, self.snr_db, self.per, left=1.0, right=self.per[-1] if self.per else 1.0))
        rate = float(np.interp(snr_db, self.snr_db, self.rate_bps, left=0.0, right=self.rate_bps[-1] if self.rate_bps else 0.0))
        ber = float(np.interp(snr_db, self.snr_db, self.ber, left=0.5, right=self.ber[-1] if self.ber else 0.5))
        psp = float(np.clip(1.0 - per, 0.0, 1.0))
        retries = per / max(psp, 1e-6)
        return {
            "packet_success_probability": psp,
            "effective_rate_bps": rate,
            "expected_retransmissions": retries,
            "ber": ber,
            "bler": per,
        }


def build_snr_lut(records: list[dict[str, Any]], phy_config_hash: str, n_bins: int = 24) -> LinkQualityLUT:
    snrs = np.asarray([r["snr_db"] for r in records], dtype=float)
    pers = np.asarray([1.0 - r["packet_success_probability"] for r in records], dtype=float)
    rates = np.asarray([r["effective_rate_bps"] for r in records], dtype=float)
    bers = np.asarray([r.get("ber", 0.0) for r in records], dtype=float)
    lo, hi = float(np.min(snrs)), float(np.max(snrs))
    edges = np.linspace(lo, hi, n_bins + 1)
    centers, per_m, rate_m, ber_m = [], [], [], []
    for i in range(n_bins):
        sel = (snrs >= edges[i]) & (snrs <= edges[i + 1] if i == n_bins - 1 else snrs < edges[i + 1])
        if not np.any(sel):
            continue
        centers.append(float(0.5 * (edges[i] + edges[i + 1])))
        per_m.append(float(pers[sel].mean()))
        rate_m.append(float(rates[sel].mean()))
        ber_m.append(float(bers[sel].mean()))
    if not centers:
        centers, per_m, rate_m, ber_m = [0.0], [1.0], [0.0], [0.5]
    return LinkQualityLUT(
        snr_db=centers,
        per=per_m,
        rate_bps=rate_m,
        ber=ber_m,
        phy_config_hash=phy_config_hash,
    )
