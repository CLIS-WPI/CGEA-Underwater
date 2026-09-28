"""M1: Bellhop generates validated underwater paths."""

from __future__ import annotations

import numpy as np

from cgea.acoustic.bellhop import AcousticEnvironment, BellhopEngine, SoundSpeedProfile
from cgea.types import Position3D


def test_increasing_range_increases_delay():
    eng = BellhopEngine(AcousticEnvironment(seed=0), prefer_aubellhop=False, allow_fallback=True)
    tx = Position3D(x=0, y=0, z=50)
    rx_near = Position3D(x=500, y=0, z=60)
    rx_far = Position3D(x=2000, y=0, z=60)
    c1 = eng.compute_channel("a", "b", tx, rx_near, seed=0)
    c2 = eng.compute_channel("a", "b", tx, rx_far, seed=0)
    assert c2.propagation_delay_s > c1.propagation_delay_s


def test_depth_change_modifies_paths():
    eng = BellhopEngine(AcousticEnvironment(seed=1), prefer_aubellhop=False, allow_fallback=True)
    tx = Position3D(x=0, y=0, z=20)
    rx_shallow = Position3D(x=1000, y=0, z=30)
    rx_deep = Position3D(x=1000, y=0, z=150)
    c1 = eng.compute_channel("a", "b", tx, rx_shallow, seed=1)
    c2 = eng.compute_channel("a", "b", tx, rx_deep, seed=1)
    # Delays or coefficients should differ
    assert c1.path_delays_s != c2.path_delays_s or c1.path_coefficients != c2.path_coefficients


def test_reproducible_with_same_seed():
    eng = BellhopEngine(AcousticEnvironment(seed=7), prefer_aubellhop=False, allow_fallback=True)
    tx = Position3D(x=0, y=0, z=40)
    rx = Position3D(x=800, y=0, z=70)
    a = eng.compute_channel("tx", "rx", tx, rx, seed=7)
    b = eng.compute_channel("tx", "rx", tx, rx, seed=7)
    assert a.path_delays_s == b.path_delays_s
    assert np.allclose(
        [complex(c) for c in a.path_coefficients],
        [complex(c) for c in b.path_coefficients],
    )


def test_environment_change_modifies_channel():
    tx = Position3D(x=0, y=0, z=50)
    rx = Position3D(x=1000, y=0, z=50)
    e1 = AcousticEnvironment(
        water_depth_m=100.0,
        ssp=SoundSpeedProfile(depths_m=[0, 100], speeds_mps=[1500, 1500]),
        seed=0,
        environment_id="iso100",
    )
    e2 = AcousticEnvironment(
        water_depth_m=200.0,
        ssp=SoundSpeedProfile(depths_m=[0, 200], speeds_mps=[1500, 1520]),
        seed=0,
        environment_id="grad200",
    )
    c1 = BellhopEngine(e1, prefer_aubellhop=False, allow_fallback=True).compute_channel("a", "b", tx, rx, seed=0)
    c2 = BellhopEngine(e2, prefer_aubellhop=False, allow_fallback=True).compute_channel("a", "b", tx, rx, seed=0)
    assert c1.arrivals != c2.arrivals or c1.propagation_loss_db != c2.propagation_loss_db
