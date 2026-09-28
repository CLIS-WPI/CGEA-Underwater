"""Bellhop / AUBellhop underwater acoustic propagation engine.

Sionna RT is intentionally NOT used here. Acoustic paths come from Bellhop.
"""

from __future__ import annotations

import hashlib
import math
import tempfile
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import Field

from cgea.types import CgeaBaseModel, Position3D


class SoundSpeedProfile(CgeaBaseModel):
    """Depth (m, positive down) → sound speed (m/s)."""

    depths_m: list[float] = Field(default_factory=lambda: [0.0, 50.0, 100.0, 200.0])
    speeds_mps: list[float] = Field(default_factory=lambda: [1520.0, 1510.0, 1505.0, 1515.0])

    def interpolate(self, depth_m: float) -> float:
        return float(np.interp(depth_m, self.depths_m, self.speeds_mps))

    def mean_speed(self) -> float:
        return float(np.mean(self.speeds_mps))


class BottomProperties(CgeaBaseModel):
    soundspeed_mps: float = 1600.0
    density_ratio: float = 1.5
    attenuation_db_per_wavelength: float = 0.5
    roughness_m: float = 0.0


class SurfaceModel(CgeaBaseModel):
    boundary: Literal["vacuum", "acoustically_soft", "acoustically_hard"] = "vacuum"
    roughness_m: float = 0.0


class AcousticEnvironment(CgeaBaseModel):
    water_depth_m: float = 200.0
    carrier_frequency_hz: float = 25_000.0
    ssp: SoundSpeedProfile = Field(default_factory=SoundSpeedProfile)
    bottom: BottomProperties = Field(default_factory=BottomProperties)
    surface: SurfaceModel = Field(default_factory=SurfaceModel)
    environment_id: str = "default_isovelocity"
    seed: int = 0

    def hash_id(self) -> str:
        payload = self.model_dump()
        return hashlib.sha256(str(payload).encode()).hexdigest()[:12]


class ArrivalPath(CgeaBaseModel):
    delay_s: float
    amplitude_complex: complex
    launch_angle_deg: float = 0.0
    arrival_angle_deg: float = 0.0
    num_surface_bounces: int = 0
    num_bottom_bounces: int = 0
    propagation_loss_db: float = 0.0


class ChannelRealization(CgeaBaseModel):
    tx_id: str
    rx_id: str
    tx_position: Position3D
    rx_position: Position3D
    range_m: float
    tx_depth_m: float
    rx_depth_m: float
    carrier_frequency_hz: float
    environment_id: str
    seed: int
    arrivals: list[ArrivalPath]
    path_delays_s: list[float]
    path_coefficients: list[complex]
    propagation_delay_s: float
    propagation_loss_db: float
    delay_spread_s: float
    cir_time_s: list[float] = Field(default_factory=list)
    cir_complex: list[complex] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True, "extra": "forbid"}


def _thorp_absorption_db_per_km(freq_hz: float) -> float:
    """Thorp absorption (dB/km) for seawater."""
    f_khz = freq_hz / 1000.0
    f2 = f_khz * f_khz
    return 0.11 * f2 / (1.0 + f2) + 44.0 * f2 / (4100.0 + f2) + 2.75e-4 * f2 + 0.003


def _image_path(
    range_m: float,
    tx_depth: float,
    rx_depth: float,
    water_depth: float,
    c: float,
    freq_hz: float,
    n_surface: int,
    n_bottom: int,
    surface_phase: float,
    bottom_phase: float,
    bottom_refl: float,
) -> ArrivalPath | None:
    """Simple multipath via method of images (deterministic fallback / seed path)."""
    if n_surface == 0 and n_bottom == 0:
        z_eff = abs(tx_depth - rx_depth)
        phase = 0.0
        refl = 1.0
    else:
        # Unfolded vertical distance
        if n_surface == 1 and n_bottom == 0:
            z_eff = tx_depth + rx_depth
            phase = surface_phase
            refl = 1.0
        elif n_surface == 0 and n_bottom == 1:
            z_eff = (2 * water_depth - tx_depth - rx_depth)
            phase = bottom_phase
            refl = bottom_refl
        elif n_surface == 1 and n_bottom == 1:
            z_eff = (2 * water_depth - abs(tx_depth - rx_depth))
            phase = surface_phase + bottom_phase
            refl = bottom_refl
        else:
            return None

    path_len = math.hypot(range_m, z_eff)
    if path_len <= 0:
        return None
    delay = path_len / c
    # Spherical spreading + absorption
    loss_db = 20.0 * math.log10(max(path_len, 1.0)) + _thorp_absorption_db_per_km(freq_hz) * (
        path_len / 1000.0
    )
    amp_mag = 10 ** (-loss_db / 20.0) * refl
    amp = amp_mag * complex(math.cos(phase), math.sin(phase))
    launch = math.degrees(math.atan2(z_eff if n_surface or n_bottom else (rx_depth - tx_depth), range_m))
    return ArrivalPath(
        delay_s=delay,
        amplitude_complex=amp,
        launch_angle_deg=launch,
        arrival_angle_deg=launch,
        num_surface_bounces=n_surface,
        num_bottom_bounces=n_bottom,
        propagation_loss_db=loss_db - 20.0 * math.log10(max(refl, 1e-12)),
    )


class BellhopEngine:
    """Generate underwater acoustic arrivals via aubellhop, with deterministic fallback.

    Prefer aubellhop when available. Fallback uses a physics-inspired deterministic
    image multipath model so unit tests and offline CI remain reproducible.
    """

    def __init__(self, env: AcousticEnvironment | None = None, prefer_aubellhop: bool = True):
        self.env = env or AcousticEnvironment()
        self.prefer_aubellhop = prefer_aubellhop
        self._aubellhop = None
        if prefer_aubellhop:
            try:
                import aubellhop as bh  # type: ignore

                self._aubellhop = bh
            except ImportError:
                self._aubellhop = None

    @property
    def backend(self) -> str:
        return "aubellhop" if self._aubellhop is not None else "deterministic_image"

    def compute_channel(
        self,
        tx_id: str,
        rx_id: str,
        tx_position: Position3D,
        rx_position: Position3D,
        seed: int | None = None,
    ) -> ChannelRealization:
        seed = self.env.seed if seed is None else seed
        range_m = tx_position.horizontal_range_to(rx_position)
        tx_depth = float(tx_position.z)
        rx_depth = float(rx_position.z)

        if self._aubellhop is not None:
            arrivals = self._compute_aubellhop(range_m, tx_depth, rx_depth, seed)
        else:
            arrivals = self._compute_deterministic(range_m, tx_depth, rx_depth, seed)

        arrivals = sorted(arrivals, key=lambda a: a.delay_s)
        if not arrivals:
            # Guarantee at least a direct path for numerical stability
            c = self.env.ssp.mean_speed()
            path_len = math.hypot(range_m, abs(rx_depth - tx_depth))
            delay = path_len / c
            loss_db = 20.0 * math.log10(max(path_len, 1.0)) + _thorp_absorption_db_per_km(
                self.env.carrier_frequency_hz
            ) * (path_len / 1000.0)
            arrivals = [
                ArrivalPath(
                    delay_s=delay,
                    amplitude_complex=10 ** (-loss_db / 20.0),
                    propagation_loss_db=loss_db,
                )
            ]

        delays = [a.delay_s for a in arrivals]
        coeffs = [a.amplitude_complex for a in arrivals]
        powers = np.array([abs(c) ** 2 for c in coeffs], dtype=float)
        powers = powers / max(powers.sum(), 1e-30)
        mean_delay = float(np.sum(powers * np.array(delays)))
        delay_spread = float(np.sqrt(max(np.sum(powers * (np.array(delays) - mean_delay) ** 2), 0.0)))
        prop_delay = float(min(delays))
        # Effective loss from strongest path
        strongest = max(arrivals, key=lambda a: abs(a.amplitude_complex))
        prop_loss = float(strongest.propagation_loss_db)

        cir_t, cir_h = self._build_cir(delays, coeffs)

        return ChannelRealization(
            tx_id=tx_id,
            rx_id=rx_id,
            tx_position=tx_position,
            rx_position=rx_position,
            range_m=float(range_m),
            tx_depth_m=tx_depth,
            rx_depth_m=rx_depth,
            carrier_frequency_hz=self.env.carrier_frequency_hz,
            environment_id=self.env.environment_id,
            seed=seed,
            arrivals=arrivals,
            path_delays_s=delays,
            path_coefficients=coeffs,
            propagation_delay_s=prop_delay,
            propagation_loss_db=prop_loss,
            delay_spread_s=delay_spread,
            cir_time_s=cir_t,
            cir_complex=cir_h,
        )

    def _compute_deterministic(
        self, range_m: float, tx_depth: float, rx_depth: float, seed: int
    ) -> list[ArrivalPath]:
        rng = np.random.default_rng(seed)
        c = self.env.ssp.interpolate(0.5 * (tx_depth + rx_depth))
        bottom_refl = 10 ** (-abs(self.env.bottom.attenuation_db_per_wavelength) / 40.0)
        surface_phase = math.pi if self.env.surface.boundary == "vacuum" else 0.0
        bottom_phase = 0.0

        candidates = [
            (0, 0),
            (1, 0),
            (0, 1),
            (1, 1),
        ]
        arrivals: list[ArrivalPath] = []
        for ns, nb in candidates:
            path = _image_path(
                range_m,
                tx_depth,
                rx_depth,
                self.env.water_depth_m,
                c,
                self.env.carrier_frequency_hz,
                ns,
                nb,
                surface_phase,
                bottom_phase,
                bottom_refl,
            )
            if path is None:
                continue
            # Tiny deterministic phase jitter from seed (reproducible, not per-baseline)
            jitter = float(rng.uniform(-0.05, 0.05))
            path.amplitude_complex *= complex(math.cos(jitter), math.sin(jitter))
            arrivals.append(path)
        return arrivals

    def _compute_aubellhop(
        self, range_m: float, tx_depth: float, rx_depth: float, seed: int
    ) -> list[ArrivalPath]:
        """Call aubellhop compute_arrivals and map to ArrivalPath list."""
        assert self._aubellhop is not None
        bh = self._aubellhop

        # Build environment dict compatible with aubellhop Environment API
        ssp = np.column_stack(
            [np.asarray(self.env.ssp.depths_m), np.asarray(self.env.ssp.speeds_mps)]
        )
        env_kwargs: dict[str, Any] = {
            "name": f"cgea_{self.env.environment_id}_{seed}",
            "frequency": self.env.carrier_frequency_hz,
            "soundspeed": ssp,
            "bottom_depth": self.env.water_depth_m,
            "bottom_density": self.env.bottom.density_ratio,
            "bottom_soundspeed": self.env.bottom.soundspeed_mps,
            "bottom_attenuation": self.env.bottom.attenuation_db_per_wavelength,
            "source_depth": tx_depth,
            "receiver_depth": rx_depth,
            "receiver_range": max(range_m, 1.0),
        }

        with tempfile.TemporaryDirectory(prefix="cgea_bellhop_") as tmp:
            try:
                env = bh.Environment(**env_kwargs)
                # Prefer arrivals task
                if hasattr(bh, "compute_arrivals"):
                    arr = bh.compute_arrivals(env, workspace=tmp)
                elif hasattr(bh, "compute"):
                    env.task = "arrivals"
                    arr = bh.compute(env, workspace=tmp)
                else:
                    return self._compute_deterministic(range_m, tx_depth, rx_depth, seed)
            except Exception:
                # Robust fallback if binary / env write fails
                return self._compute_deterministic(range_m, tx_depth, rx_depth, seed)

        return self._parse_aubellhop_arrivals(arr, range_m, tx_depth, rx_depth, seed)

    def _parse_aubellhop_arrivals(
        self, arr: Any, range_m: float, tx_depth: float, rx_depth: float, seed: int
    ) -> list[ArrivalPath]:
        """Best-effort parse of aubellhop arrival structures across versions."""
        arrivals: list[ArrivalPath] = []
        try:
            # Common patterns: dict with time/amp, pandas, or object with attributes
            if isinstance(arr, dict):
                times = np.asarray(arr.get("time_of_arrival", arr.get("delay", []))).ravel()
                amps = np.asarray(arr.get("amplitude", arr.get("arrival_amplitude", []))).ravel()
            elif hasattr(arr, "time_of_arrival"):
                times = np.asarray(arr.time_of_arrival).ravel()
                amps = np.asarray(getattr(arr, "amplitude", getattr(arr, "arrival_amplitude"))).ravel()
            elif hasattr(arr, "columns"):  # DataFrame-like
                cols = list(arr.columns)
                tcol = next(c for c in cols if "time" in str(c).lower() or "delay" in str(c).lower())
                acol = next(c for c in cols if "amp" in str(c).lower())
                times = np.asarray(arr[tcol]).ravel()
                amps = np.asarray(arr[acol]).ravel()
            else:
                return self._compute_deterministic(range_m, tx_depth, rx_depth, seed)

            for t, a in zip(times, amps):
                amp = complex(a) if np.iscomplexobj(a) or isinstance(a, complex) else complex(float(a), 0.0)
                if abs(amp) == 0 or not np.isfinite(t):
                    continue
                loss_db = -20.0 * math.log10(max(abs(amp), 1e-30))
                arrivals.append(
                    ArrivalPath(
                        delay_s=float(t),
                        amplitude_complex=amp,
                        propagation_loss_db=loss_db,
                    )
                )
        except Exception:
            return self._compute_deterministic(range_m, tx_depth, rx_depth, seed)

        if not arrivals:
            return self._compute_deterministic(range_m, tx_depth, rx_depth, seed)
        return arrivals

    @staticmethod
    def _build_cir(
        delays: list[float], coeffs: list[complex], fs: float = 100_000.0, duration_pad_s: float = 0.01
    ) -> tuple[list[float], list[complex]]:
        if not delays:
            return [], []
        t_max = max(delays) + duration_pad_s
        n = int(math.ceil(t_max * fs)) + 1
        h = np.zeros(n, dtype=np.complex128)
        for d, c in zip(delays, coeffs):
            idx = int(round(d * fs))
            if 0 <= idx < n:
                h[idx] += c
        t = (np.arange(n) / fs).tolist()
        return t, h.tolist()

    def save_realization(self, realization: ChannelRealization, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Persist as JSON with complex encoded
        payload = realization.model_dump()
        payload["path_coefficients"] = [[c.real, c.imag] for c in realization.path_coefficients]
        payload["cir_complex"] = [[c.real, c.imag] for c in realization.cir_complex]
        payload["arrivals"] = [
            {
                **a.model_dump(exclude={"amplitude_complex"}),
                "amplitude_complex": [a.amplitude_complex.real, a.amplitude_complex.imag],
            }
            for a in realization.arrivals
        ]
        import json

        path.write_text(json.dumps(payload, indent=2, default=str))
