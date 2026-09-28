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
    path_loss_db: float | None = None
    expected_retransmissions: float | None = None
    ber: float | None = None
    bler: float | None = None

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
    """Map acoustic channel state → delay, availability, throughput, P(success).

    SNR uses Bellhop path-loss (TL) as the primary physical quantity:
        SNR ≈ tx_power - TL - noise
    """
    tl_db = float(realization.propagation_loss_db)
    noise_power_db = noise_psd_dbm_hz + 10.0 * np.log10(bandwidth_hz)
    snr_db = float(tx_power_dbm - tl_db - noise_power_db)

    psp = float(1.0 / (1.0 + np.exp(-(snr_db - snr_threshold_db) / 2.0)))
    link_available = bool(snr_db >= snr_threshold_db and psp >= 0.1)

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

    def save(self, trace: ChannelTrace, extra_meta: dict[str, Any] | None = None) -> Path:
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
                    "path_loss_db": s.path_loss_db,
                    "expected_retransmissions": s.expected_retransmissions,
                    "ber": s.ber,
                    "bler": s.bler,
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
        if extra_meta:
            meta.update(extra_meta)
        self.meta_path(trace.trace_id).write_text(json.dumps(meta, indent=2))
        return out

    def load_meta(self, trace_id: str) -> dict[str, Any]:
        return json.loads(self.meta_path(trace_id).read_text())

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
                    path_loss_db=row.get("path_loss_db"),
                    expected_retransmissions=row.get("expected_retransmissions"),
                    ber=row.get("ber"),
                    bler=row.get("bler"),
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


def sionna_link_quality_from_realization(
    realization: ChannelRealization,
    noise_psd_dbm_hz: float = -80.0,
    bandwidth_hz: float = 5000.0,
    tx_power_dbm: float = 180.0,
    snr_threshold_db: float = 5.0,
    batch_size: int = 8,
) -> dict[str, float | bool]:
    """Bellhop → UnderwaterAcousticChannel(Sionna) → link quality.

    This is the paper path: path coefficients/delays enter the Sionna ChannelModel
    API on GPU (when available); quality metrics are derived from returned tensors.
    """
    from cgea.sionna_ext.channel_model import UnderwaterAcousticChannel

    channel = UnderwaterAcousticChannel(realization=realization)
    a, tau = channel(batch_size=batch_size, num_time_steps=1, sampling_frequency=bandwidth_hz)

    # Confirm Bellhop paths entered Sionna tensors
    assert a.shape[-2] == len(realization.path_coefficients)
    prop_delay = float(tau.amin(dim=-1).mean().detach().cpu())
    delays = tau[0, 0, 0].detach().cpu().numpy()
    # Path powers from Sionna coefficients for delay spread
    path_amp = a.abs().mean(dim=(0, 1, 2, 3, 4, 6))  # [paths]
    powers = (path_amp.detach().cpu().numpy() ** 2)
    powers = powers / max(powers.sum(), 1e-30)
    mean_delay = float(np.sum(powers * delays))
    delay_spread = float(np.sqrt(max(np.sum(powers * (delays - mean_delay) ** 2), 0.0)))

    # Link budget from Bellhop TL (physical), after Sionna bridge of CIR
    tl_db = float(realization.propagation_loss_db)
    noise_power_db = noise_psd_dbm_hz + 10.0 * np.log10(bandwidth_hz)
    snr_db = float(tx_power_dbm - tl_db - noise_power_db)
    psp = float(1.0 / (1.0 + np.exp(-(snr_db - snr_threshold_db) / 2.0)))
    link_available = bool(snr_db >= snr_threshold_db and psp >= 0.1)
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
        "propagation_delay_s": prop_delay,
        "delay_spread_s": delay_spread,
        "sionna_bridged": True,
    }


def generate_mission_trace(
    engine: BellhopEngine,
    node_positions: dict[str, Position3D],
    times: list[float],
    seed: int,
    noise_psd_dbm_hz: float = -80.0,
    bandwidth_hz: float = 5000.0,
    tx_power_dbm: float = 180.0,
    use_sionna_bridge: bool = True,
    snr_threshold_db: float = 12.0,
) -> ChannelTrace:
    """Generate one shared ChannelTrace for all directed links over time.

    Paper path: Bellhop → Sionna UnderwaterAcousticChannel → link quality.
    DO NOT regenerate separately per baseline — call once and replay.
    """
    backends: set[str] = set()
    trace_id = make_trace_id(
        engine.env.environment_id, seed, len(node_positions), float(times[-1] if times else 0.0)
    )
    samples: list[LinkSample] = []
    node_ids = list(node_positions.keys())
    # Positions are frozen for paper runs: compute each directed link once, replay over time.
    link_cache: dict[tuple[str, str], tuple] = {}
    total_links = len(node_ids) * (len(node_ids) - 1)
    done = 0
    for i, tx in enumerate(node_ids):
        for j, rx in enumerate(node_ids):
            if i == j:
                continue
            realization = engine.compute_channel(
                tx, rx, node_positions[tx], node_positions[rx], seed=seed
            )
            backends.add(realization.backend)
            if use_sionna_bridge:
                q = sionna_link_quality_from_realization(
                    realization,
                    noise_psd_dbm_hz=noise_psd_dbm_hz,
                    bandwidth_hz=bandwidth_hz,
                    tx_power_dbm=tx_power_dbm,
                    snr_threshold_db=snr_threshold_db,
                )
            else:
                q = realization_to_link_quality(
                    realization,
                    noise_psd_dbm_hz=noise_psd_dbm_hz,
                    bandwidth_hz=bandwidth_hz,
                    tx_power_dbm=tx_power_dbm,
                    snr_threshold_db=snr_threshold_db,
                )
            link_cache[(tx, rx)] = (realization, q)
            done += 1
            if done == 1 or done % 20 == 0 or done == total_links:
                print(
                    f"  [channel] {done}/{total_links} links backend={realization.backend} "
                    f"n_paths={len(realization.arrivals)}",
                    flush=True,
                )

    for t in times:
        for (tx, rx), (realization, q) in link_cache.items():
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

    if not engine.allow_fallback and backends and backends != {"aubellhop"}:
        raise RuntimeError(
            f"Paper traces must use aubellhop only; got backends={sorted(backends)}"
        )

    return ChannelTrace(
        trace_id=trace_id,
        environment_id=engine.env.environment_id,
        seed=seed,
        carrier_frequency_hz=engine.env.carrier_frequency_hz,
        samples=samples,
    )
