"""Experiment metrics and statistical summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import Field

from cgea.types import CgeaBaseModel, Provenance, config_hash, git_commit


class RunMetrics(CgeaBaseModel):
    mission_completion_ratio: float
    mission_utility: float
    unauthorized_high_risk_action_rate: float
    governance_false_denial_rate: float
    useful_action_retention: float
    governance_bytes: int
    total_communication_bytes: int
    governance_communication_overhead: float
    reconciliation_latency_s: float
    conflict_count: int
    energy_propulsion_j: float
    energy_communication_j: float
    energy_compute_j: float
    provenance: Provenance
    extra: dict[str, Any] = Field(default_factory=dict)


def compute_overhead(governance_bytes: int, total_bytes: int) -> float:
    if total_bytes <= 0:
        return 0.0
    return governance_bytes / total_bytes


def summarize_runs(values: list[float]) -> dict[str, float]:
    """Mean and 95% CI (normal approx) + Cohen's d helper inputs."""
    arr = np.asarray(values, dtype=float)
    n = len(arr)
    if n == 0:
        return {"mean": float("nan"), "std": float("nan"), "ci95_low": float("nan"), "ci95_high": float("nan"), "n": 0}
    mean = float(arr.mean())
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    se = std / np.sqrt(n) if n > 0 else 0.0
    # t critical ~1.96 for large n; for small n use 2.262 (n=10 df=9) approx via 1.96*adjust
    tcrit = 1.96 if n >= 30 else 2.262
    return {
        "mean": mean,
        "std": std,
        "ci95_low": mean - tcrit * se,
        "ci95_high": mean + tcrit * se,
        "n": float(n),
    }


def cohens_d(a: list[float], b: list[float]) -> float:
    aa, bb = np.asarray(a, float), np.asarray(b, float)
    if len(aa) < 2 or len(bb) < 2:
        return float("nan")
    pooled = np.sqrt(((len(aa) - 1) * aa.var(ddof=1) + (len(bb) - 1) * bb.var(ddof=1)) / (len(aa) + len(bb) - 2))
    if pooled == 0:
        return 0.0
    return float((aa.mean() - bb.mean()) / pooled)


def save_metrics(metrics: RunMetrics, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{metrics.provenance.baseline}_{metrics.provenance.random_seed}_{metrics.provenance.configuration_hash}.json"
    path.write_text(metrics.model_dump_json(indent=2))
    return path


def load_all_metrics(results_dir: Path) -> list[RunMetrics]:
    results_dir = Path(results_dir)
    out: list[RunMetrics] = []
    for p in sorted(results_dir.glob("**/*.json")):
        try:
            out.append(RunMetrics.model_validate_json(p.read_text()))
        except Exception:
            continue
    return out
