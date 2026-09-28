"""M2: Bellhop CIR enters custom Sionna ChannelModel."""

from __future__ import annotations

import torch

from cgea.acoustic.bellhop import AcousticEnvironment, BellhopEngine
from cgea.sionna_ext import UnderwaterAcousticChannel, batched_link_quality_from_channel
from cgea.types import Position3D


def test_sionna_channel_shapes():
    eng = BellhopEngine(AcousticEnvironment(seed=0), prefer_aubellhop=False)
    tx = Position3D(x=0, y=0, z=40)
    rx = Position3D(x=1000, y=0, z=60)
    real = eng.compute_channel("tx", "rx", tx, rx, seed=0)
    ch = UnderwaterAcousticChannel(realization=real, device="cpu")
    a, tau = ch(batch_size=8, num_time_steps=4, sampling_frequency=5000.0)
    assert a.shape == (8, 1, 1, 1, 1, real.path_coefficients.__len__(), 4)
    assert tau.shape == (8, 1, 1, len(real.path_delays_s))
    assert a.is_complex()
    assert torch.allclose(tau[0, 0, 0], torch.tensor(real.path_delays_s, dtype=tau.dtype))


def test_batched_link_quality():
    ch = UnderwaterAcousticChannel(
        path_coefficients=[0.1 + 0.0j, 0.05 + 0.01j],
        path_delays=[0.5, 0.55],
        device="cpu",
    )
    q = batched_link_quality_from_channel(ch, batch_size=16)
    assert "propagation_delay_s" in q
    assert "packet_success_probability" in q
    assert q["propagation_delay_s"] == 0.5
