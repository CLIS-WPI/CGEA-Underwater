"""Hard assertions for paper / pilot experimental runs."""

from __future__ import annotations

from typing import Any

import torch
from omegaconf import DictConfig


class PaperAssertionError(RuntimeError):
    """Abort the entire campaign if a paper-run gate fails."""


def assert_paper_config(cfg: DictConfig) -> None:
    allow_fb = bool(cfg.acoustic.get("allow_fallback", True))
    use_sionna = bool(cfg.acoustic.get("use_sionna_bridge", False))
    prefer = bool(cfg.acoustic.get("prefer_aubellhop", False))
    use_gpu_phy = bool(cfg.acoustic.get("use_gpu_phy", True))
    if allow_fb:
        raise PaperAssertionError("allow_fallback must be false for paper runs")
    if not use_sionna:
        raise PaperAssertionError("use_sionna_bridge must be true for paper runs")
    if not prefer:
        raise PaperAssertionError("prefer_aubellhop must be true for paper runs")
    if not use_gpu_phy:
        raise PaperAssertionError("use_gpu_phy must be true for paper runs")
    if not torch.cuda.is_available():
        raise PaperAssertionError("CUDA is required for paper runs (torch.cuda.is_available() is False)")


def assert_cuda_device(device: str | Any) -> None:
    s = str(device)
    if not s.startswith("cuda"):
        raise PaperAssertionError(f"Sionna/torch device must start with 'cuda', got {s!r}")


def assert_aubellhop_backend(backend: str) -> None:
    if backend != "aubellhop":
        raise PaperAssertionError(f'backend must be "aubellhop", got {backend!r}')


def assert_trace_paper_meta(meta: dict[str, Any], require_gpu_phy: bool = True) -> None:
    if meta.get("backend") != "aubellhop":
        raise PaperAssertionError(f"trace backend must be aubellhop, got {meta.get('backend')!r}")
    if meta.get("allow_fallback") is not False:
        raise PaperAssertionError("trace meta allow_fallback must be false")
    if meta.get("use_sionna_bridge") is not True:
        raise PaperAssertionError("trace meta use_sionna_bridge must be true")
    device = str(meta.get("sionna_device", "") or meta.get("cuda_device", ""))
    assert_cuda_device(device)
    if require_gpu_phy:
        if meta.get("gpu_phy") is not True:
            raise PaperAssertionError("paper traces must be GPU-PHY family (meta.gpu_phy=true)")
        mode = str(meta.get("doppler_mode") or "not_modeled")
        if mode != "not_modeled":
            raise PaperAssertionError(f"unexpected doppler_mode {mode!r}")
