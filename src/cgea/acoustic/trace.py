"""ChannelTrace format — shared by all baselines B1–B5."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import Field

from cgea.acoustic.bellhop import BellhopEngine, ChannelRealization
from cgea.types import CgeaBaseModel, Position3D


class LinkSample(CgeaBaseModel):
    """One directed-link sample at a timestamp."""

    timestamp: float
    tx_id: str
    rx_id: str
    tx_position: Position3D
    rx_position: Position3D
    distance_m: float

    path_delays: list[float]
    path_coefficients: list[complex]

    propagation_delay_s: float
    estimated_rate_bps: float
    packet_success_probability: float
    link_available: bool

    snr_db: float | None = None
    doppler_hz: float | None = None
    delay_spread_s: float | None = None

    model_config = {"arbitrary_types_allowed": True, "extra": "forbid"}


class ChannelTrace(CgeaBaseModel):
    """Full mission-duration directed-link channel trace."""

    trace_id: str
    environment_id: str
    seed: int
    carrier_frequency_hz: float
    samples: list[LinkSample] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True, "extra": "forbid"}

    def link_samples(self, tx_id: str, rx_id: str) -> list[LinkSample]:
        return [s for s in self.samples if s.tx_id == tx_id and s.rx_id == rx_id]

    def sample_at(self, tx_id: str, rx_id: str, t: float) -> LinkSample | None:
        candidates = self.link_samples(tx_id, rx_id)
        if not candidates:
            return None
        # Nearest timestamp (replay)
        return min(candidates, key=lambda s: abs(s.timestamp - t))


def realization_to_link_quality(
    realization: ChannelRealization,
    noise_psd_dbm_hz: float = -80.0,
    bandwidth_hz: float = 5000.0,
    tx_power_dbm: float = 180.0,
    snr_threshold_db: float = 5.0,
) -> dict[str, float | bool]:
    """Map acoustic channel state → delay, availability, throughput, P(success)."""
    # Approximate received power from strongest path
    strongest = max((abs(c) for c in realization.path_coefficients), default=0.0)
    # amplitude is linear pressure-like; convert to relative SNR proxy
    rx_power_db = tx_power_dbm + 20.0 * np.log10(max(strongest, 1e-30))
    noise_power_db = noise_psd_dbm_hz + 10.0 * np.log10(bandwidth_hz)
    snr_db = float(rx_power_db - noise_power_db)

    # Packet success via logistic of SNR
    psp = float(1.0 / (1.0 + np.exp(-(snr_db - snr_threshold_db) / 2.0)))
    link_available = bool(snr_db >= snr_threshold_db and psp >= 0.1)

    # Shannon-like effective rate capped by acoustic modem-ish bandwidth
    snr_lin = 10 ** (snr_db / 10.0)
    rate = float(bandwidth_hz * np.log2(1.0 + max(snr_lin, 0.0)))
    if not link_available:
        rate = 0.0
        psp = min(psp, 0.05)

    return {
        "snr_db": snr_db,
        "estimated_rate_bps": rate,
        "packet_success_probability": psp,
        "link_available": link_available,
        "propagation_delay_s": realization.propagation_delay_s,
        "delay_spread_s": realization.delay_spread_s,
    }


class ChannelTraceStore:
    """Persist and load ChannelTrace objects. All baselines replay the same store."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def trace_path(self, trace_id: str) -> Path:
        return self.root / f"{trace_id}.parquet"

    def meta_path(self, trace_id: str) -> Path:
        return self.root / f"{trace_id}.meta.json"

    def save(self, trace: ChannelTrace) -> Path:
        import pyarrow as pa
        import pyarrow.parquet as pq

        rows: list[dict[str, Any]] = []
        for s in trace.samples:
            rows.append(
                {
                    "timestamp": s.timestamp,
                    "tx_id": s.tx_id,
                    "rx_id": s.rx_id,
                    "tx_x": s.tx_position.x,
                    "tx_y": s.tx_position.y,
                    "tx_z": s.tx_position.z,
                    "rx_x": s.rx_position.x,
                    "rx_y": s.rx_position.y,
                    "rx_z": s.rx_position.z,
                    "distance_m": s.distance_m,
                    "path_delays": json.dumps(s.path_delays),
                    "path_coefficients": json.dumps([[c.real, c.imag] for c in s.path_coefficients]),
                    "propagation_delay_s": s.propagation_delay_s,
                    "estimated_rate_bps": s.estimated_rate_bps,
                    "packet_success_probability": s.packet_success_probability,
                    "link_available": s.link_available,
                    "snr_db": s.snr_db,
                    "doppler_hz": s.doppler_hz,
                    "delay_spread_s": s.delay_spread_s,
                }
            )
        table = pa.Table.from_pylist(rows)
        out = self.trace_path(trace.trace_id)
        pq.write_table(table, out)
        meta = {
            "trace_id": trace.trace_id,
            "environment_id": trace.environment_id,
            "seed": trace.seed,
            "carrier_frequency_hz": trace.carrier_frequency_hz,
            "num_samples": len(trace.samples),
        }
        self.meta_path(trace.trace_id).write_text(json.dumps(meta, indent=2))
        return out

    def load(self, trace_id: str) -> ChannelTrace:
        import pyarrow.parquet as pq

        table = pq.read_table(self.trace_path(trace_id))
        meta = json.loads(self.meta_path(trace_id).read_text())
        samples: list[LinkSample] = []
        for row in table.to_pylist():
            coeffs = [complex(r, i) for r, i in json.loads(row["path_coefficients"])]
            samples.append(
                LinkSample(
                    timestamp=row["timestamp"],
                    tx_id=row["tx_id"],
                    rx_id=row["rx_id"],
                    tx_position=Position3D(x=row["tx_x"], y=row["tx_y"], z=row["tx_z"]),
                    rx_position=Position3D(x=row["rx_x"], y=row["rx_y"], z=row["rx_z"]),
                    distance_m=row["distance_m"],
                    path_delays=json.loads(row["path_delays"]),
                    path_coefficients=coeffs,
                    propagation_delay_s=row["propagation_delay_s"],
                    estimated_rate_bps=row["estimated_rate_bps"],
                    packet_success_probability=row["packet_success_probability"],
                    link_available=row["link_available"],
                    snr_db=row.get("snr_db"),
                    doppler_hz=row.get("doppler_hz"),
                    delay_spread_s=row.get("delay_spread_s"),
                )
            )
        return ChannelTrace(
            trace_id=meta["trace_id"],
            environment_id=meta["environment_id"],
            seed=meta["seed"],
            carrier_frequency_hz=meta["carrier_frequency_hz"],
            samples=samples,
        )


def make_trace_id(environment_id: str, seed: int, n_nodes: int, duration_s: float) -> str:
    raw = f"{environment_id}|{seed}|{n_nodes}|{duration_s}"
    return "tr_" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def generate_mission_trace(
    engine: BellhopEngine,
    node_positions: dict[str, Position3D],
    times: list[float],
    seed: int,
    noise_psd_dbm_hz: float = -80.0,
    bandwidth_hz: float = 5000.0,
    tx_power_dbm: float = 180.0,
) -> ChannelTrace:
    """Generate one shared ChannelTrace for all directed links over time.

    DO NOT regenerate separately per baseline — call once and replay.
    """
    trace_id = make_trace_id(
        engine.env.environment_id, seed, len(node_positions), float(times[-1] if times else 0.0)
    )
    samples: list[LinkSample] = []
    node_ids = list(node_positions.keys())
    for t in times:
        for i, tx in enumerate(node_ids):
            for j, rx in enumerate(node_ids):
                if i == j:
                    continue
                realization = engine.compute_channel(
                    tx, rx, node_positions[tx], node_positions[rx], seed=seed
                )
                q = realization_to_link_quality(
                    realization,
                    noise_psd_dbm_hz=noise_psd_dbm_hz,
                    bandwidth_hz=bandwidth_hz,
                    tx_power_dbm=tx_power_dbm,
                )
                samples.append(
                    LinkSample(
                        timestamp=float(t),
                        tx_id=tx,
                        rx_id=rx,
                        tx_position=node_positions[tx],
                        rx_position=node_positions[rx],
                        distance_m=realization.range_m,
                        path_delays=realization.path_delays_s,
                        path_coefficients=realization.path_coefficients,
                        propagation_delay_s=float(q["propagation_delay_s"]),
                        estimated_rate_bps=float(q["estimated_rate_bps"]),
                        packet_success_probability=float(q["packet_success_probability"]),
                        link_available=bool(q["link_available"]),
                        snr_db=float(q["snr_db"]),
                        doppler_hz=0.0,
                        delay_spread_s=float(q["delay_spread_s"]),
                    )
                )
    return ChannelTrace(
        trace_id=trace_id,
        environment_id=engine.env.environment_id,
        seed=seed,
        carrier_frequency_hz=engine.env.carrier_frequency_hz,
        samples=samples,
    )
