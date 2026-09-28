"""Shared types, hashing, and provenance helpers."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CgeaBaseModel(BaseModel):
    """Strict base model for frozen research schemas."""

    model_config = ConfigDict(extra="forbid", frozen=False, validate_assignment=True)


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=_json_default, separators=(",", ":"))


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, Path):
        return str(obj)
    try:
        from omegaconf import OmegaConf

        if OmegaConf.is_config(obj):
            return OmegaConf.to_container(obj, resolve=True)
    except Exception:  # noqa: BLE001
        pass
    if hasattr(obj, "tolist"):
        return obj.tolist()
    raise TypeError(f"Object of type {type(obj)!r} is not JSON serializable")


def config_hash(cfg: Any) -> str:
    """Deterministic hash of a Hydra/OmegaConf or mapping config."""
    try:
        from omegaconf import OmegaConf

        if OmegaConf.is_config(cfg):
            payload = OmegaConf.to_container(cfg, resolve=True)
        elif hasattr(cfg, "model_dump"):
            payload = cfg.model_dump()
        else:
            payload = cfg
    except Exception:  # noqa: BLE001
        payload = cfg.model_dump() if hasattr(cfg, "model_dump") else cfg
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()[:16]


def git_commit(repo_root: Path | None = None) -> str:
    import os

    env_c = os.environ.get("GIT_COMMIT") or os.environ.get("GIT_SHA")
    if env_c:
        return env_c.strip()
    root = repo_root or Path(__file__).resolve().parents[2]
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            .strip()
        )
    except Exception:  # noqa: BLE001
        return "unknown"


class Provenance(CgeaBaseModel):
    """Required metadata attached to every experimental result."""

    git_commit: str
    configuration_hash: str
    random_seed: int
    baseline: str
    channel_trace_id: str
    environment_id: str
    notes: str = ""


class Position3D(CgeaBaseModel):
    x: float
    y: float
    z: float  # depth positive downward in meters for mission coords

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def horizontal_range_to(self, other: "Position3D") -> float:
        return float(((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5)

    def distance_to(self, other: "Position3D") -> float:
        return float(
            (
                (self.x - other.x) ** 2
                + (self.y - other.y) ** 2
                + (self.z - other.z) ** 2
            )
            ** 0.5
        )
