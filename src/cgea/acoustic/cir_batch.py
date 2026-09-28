"""Pack variable-length Bellhop arrivals into GPU-friendly buckets."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch

PATH_BUCKETS = (8, 16, 32, 64, 128, 256)


@dataclass
class PackedCIRBatch:
    tx_id: list[str]
    rx_id: list[str]
    timestamp: np.ndarray
    distance_m: np.ndarray
    tx_depth_m: np.ndarray
    rx_depth_m: np.ndarray
    path_loss_db: np.ndarray
    snr_db: np.ndarray
    delay_spread_s: np.ndarray
    doppler_hz: np.ndarray
    propagation_delay_s: np.ndarray
    coeffs: torch.Tensor
    delays: torch.Tensor
    mask: torch.Tensor
    path_count: np.ndarray
    bucket: int


def bucket_width(n_paths: int) -> int:
    for w in PATH_BUCKETS:
        if n_paths <= w:
            return w
    return ((n_paths + 31) // 32) * 32


def pack_realizations(
    rows: list[dict],
    device: torch.device,
    precision: str = "complex64",
) -> list[PackedCIRBatch]:
    """Group by path-count buckets and pad only within each bucket."""
    groups: dict[int, list[dict]] = {}
    for r in rows:
        n = int(len(r["path_delays"]))
        groups.setdefault(bucket_width(max(n, 1)), []).append(r)

    cdtype = torch.complex128 if precision == "complex128" else torch.complex64
    rdtype = torch.float64 if precision == "complex128" else torch.float32
    batches: list[PackedCIRBatch] = []
    for width, items in sorted(groups.items()):
        b = len(items)
        coeffs = np.zeros((b, width), dtype=np.complex64 if precision != "complex128" else np.complex128)
        delays = np.zeros((b, width), dtype=np.float32 if precision != "complex128" else np.float64)
        mask = np.zeros((b, width), dtype=np.float32 if precision != "complex128" else np.float64)
        path_count = np.zeros(b, dtype=np.int32)
        for i, r in enumerate(items):
            n = min(len(r["path_delays"]), width)
            if n == 0:
                coeffs[i, 0] = 1e-12 + 0j
                mask[i, 0] = 1.0
                path_count[i] = 1
                continue
            coeffs[i, :n] = np.asarray(r["path_coefficients"][:n], dtype=coeffs.dtype)
            delays[i, :n] = np.asarray(r["path_delays"][:n], dtype=delays.dtype)
            mask[i, :n] = 1.0
            path_count[i] = n
        batches.append(
            PackedCIRBatch(
                tx_id=[r["tx_id"] for r in items],
                rx_id=[r["rx_id"] for r in items],
                timestamp=np.asarray([r["timestamp"] for r in items], dtype=np.float64),
                distance_m=np.asarray([r["distance_m"] for r in items], dtype=np.float64),
                tx_depth_m=np.asarray([r["tx_depth_m"] for r in items], dtype=np.float64),
                rx_depth_m=np.asarray([r["rx_depth_m"] for r in items], dtype=np.float64),
                path_loss_db=np.asarray([r["path_loss_db"] for r in items], dtype=np.float64),
                snr_db=np.asarray([r["snr_db"] for r in items], dtype=np.float64),
                delay_spread_s=np.asarray([r["delay_spread_s"] for r in items], dtype=np.float64),
                doppler_hz=np.asarray([r.get("doppler_hz", 0.0) for r in items], dtype=np.float64),
                propagation_delay_s=np.asarray([r["propagation_delay_s"] for r in items], dtype=np.float64),
                coeffs=torch.from_numpy(coeffs).to(device=device, dtype=cdtype),
                delays=torch.from_numpy(delays).to(device=device, dtype=rdtype),
                mask=torch.from_numpy(mask).to(device=device, dtype=rdtype),
                path_count=path_count,
                bucket=width,
            )
        )
    return batches
