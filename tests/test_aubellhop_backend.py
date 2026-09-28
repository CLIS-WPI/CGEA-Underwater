"""Bellhop via aubellhop when available (Docker)."""

from __future__ import annotations

import pytest

from cgea.acoustic.bellhop import AcousticEnvironment, BellhopEngine
from cgea.types import Position3D

aubellhop = pytest.importorskip("aubellhop")


@pytest.mark.bellhop
def test_aubellhop_backend_and_range_delay():
    eng = BellhopEngine(AcousticEnvironment(seed=0), prefer_aubellhop=True)
    assert eng.backend == "aubellhop"
    tx = Position3D(x=0, y=0, z=50)
    near = eng.compute_channel("a", "b", tx, Position3D(x=500, y=0, z=60), seed=0)
    far = eng.compute_channel("a", "b", tx, Position3D(x=2000, y=0, z=60), seed=0)
    assert far.propagation_delay_s > near.propagation_delay_s
    assert len(near.arrivals) >= 1
