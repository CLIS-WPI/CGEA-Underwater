"""Sionna PHY bridge: UnderwaterAcousticChannel(ChannelModel)."""

from __future__ import annotations

from typing import Any

import numpy as np
import torch

from cgea.acoustic.bellhop import ChannelRealization


class UnderwaterAcousticChannel:
    """Custom Sionna ChannelModel consuming Bellhop path coefficients/delays.

    Implements the Sionna 2.x ChannelModel call signature:
      a: [batch, num_rx, num_rx_ant, num_tx, num_tx_ant, num_paths, num_time_steps] complex
      tau: [batch, num_rx, num_tx, num_paths] float (seconds)

    Does NOT use Sionna RT for acoustic propagation.
    """

    def __init__(
        self,
        realization: ChannelRealization | None = None,
        path_coefficients: list[complex] | np.ndarray | None = None,
        path_delays: list[float] | np.ndarray | None = None,
        num_rx: int = 1,
        num_rx_ant: int = 1,
        num_tx: int = 1,
        num_tx_ant: int = 1,
        precision: str | None = None,
        device: str | None = None,
        **kwargs: Any,
    ):
        self.num_rx = num_rx
        self.num_rx_ant = num_rx_ant
        self.num_tx = num_tx
        self.num_tx_ant = num_tx_ant
        self.precision = precision or "single"
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))

        self._ChannelModel = None
        try:
            from sionna.phy.channel import ChannelModel  # type: ignore

            self._ChannelModel = ChannelModel
        except Exception:  # noqa: BLE001
            self._ChannelModel = None

        if realization is not None:
            coeffs = realization.path_coefficients
            delays = realization.path_delays_s
        else:
            coeffs = list(path_coefficients or [1.0 + 0.0j])
            delays = list(path_delays or [0.0])

        self.set_paths(coeffs, delays)
        self._bound = None
        if self._ChannelModel is not None:
            try:

                class _Bound(self._ChannelModel):  # type: ignore
                    def __init__(inner_self, outer: "UnderwaterAcousticChannel"):
                        super().__init__(precision=outer.precision, device=str(outer.device))
                        inner_self._outer = outer

                    def __call__(inner_self, batch_size, num_time_steps, sampling_frequency):
                        return inner_self._outer(batch_size, num_time_steps, sampling_frequency)

                self._bound = _Bound(self)
            except Exception:  # noqa: BLE001
                self._bound = None

    def set_paths(
        self,
        path_coefficients: list[complex] | np.ndarray,
        path_delays: list[float] | np.ndarray,
    ) -> None:
        coeffs = np.asarray(list(path_coefficients), dtype=np.complex128)
        delays = np.asarray(list(path_delays), dtype=np.float64)
        if coeffs.size == 0:
            coeffs = np.asarray([1.0 + 0.0j], dtype=np.complex128)
            delays = np.asarray([0.0], dtype=np.float64)
        self._coeffs = coeffs
        self._delays = delays
        self.num_paths = int(coeffs.size)

    def as_sionna_model(self) -> Any:
        return self._bound if self._bound is not None else self

    def __call__(
        self,
        batch_size: int,
        num_time_steps: int,
        sampling_frequency: float,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        dtype_c = torch.complex64 if self.precision == "single" else torch.complex128
        dtype_f = torch.float32 if self.precision == "single" else torch.float64

        a = torch.zeros(
            (
                batch_size,
                self.num_rx,
                self.num_rx_ant,
                self.num_tx,
                self.num_tx_ant,
                self.num_paths,
                num_time_steps,
            ),
            dtype=dtype_c,
            device=self.device,
        )
        tau = torch.zeros(
            (batch_size, self.num_rx, self.num_tx, self.num_paths),
            dtype=dtype_f,
            device=self.device,
        )

        coeffs = torch.tensor(self._coeffs, dtype=dtype_c, device=self.device)
        delays = torch.tensor(self._delays, dtype=dtype_f, device=self.device)

        for p in range(self.num_paths):
            a[:, :, :, :, :, p, :] = coeffs[p]
            tau[:, :, :, p] = delays[p]

        _ = sampling_frequency
        return a, tau


def batched_link_quality_from_channel(
    channel: UnderwaterAcousticChannel,
    batch_size: int = 32,
    bandwidth_hz: float = 5000.0,
    snr_threshold_db: float = 5.0,
) -> dict[str, float]:
    """GPU-batched evaluation → aggregate link-quality summary."""
    a, tau = channel(batch_size=batch_size, num_time_steps=1, sampling_frequency=bandwidth_hz)
    power = (a.abs() ** 2).sum(dim=(1, 2, 3, 4, 5, 6))
    snr_db = 10.0 * torch.log10(power.clamp_min(1e-30))
    psp = torch.sigmoid((snr_db - snr_threshold_db) / 2.0)
    prop_delay = tau.amin(dim=-1).mean()
    rate = bandwidth_hz * torch.log2(1.0 + torch.clamp(10 ** (snr_db / 10.0), min=0.0))
    return {
        "propagation_delay_s": float(prop_delay.detach().cpu()),
        "mean_snr_db": float(snr_db.mean().detach().cpu()),
        "packet_success_probability": float(psp.mean().detach().cpu()),
        "estimated_rate_bps": float(rate.mean().detach().cpu()),
        "link_available": bool(float(psp.mean().detach().cpu()) >= 0.1),
    }
