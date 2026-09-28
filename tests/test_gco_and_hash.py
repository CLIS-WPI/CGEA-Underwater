"""Stable packet Bernoulli and GCO TX-only accounting."""

from __future__ import annotations

from cgea.network import stable_bernoulli
from cgea.metrics import compute_overhead


def test_stable_bernoulli_is_process_stable():
    a = stable_bernoulli("auth-auv_00-120.0")
    b = stable_bernoulli("auth-auv_00-120.0")
    c = stable_bernoulli("auth-auv_00-120.1")
    assert a == b
    assert 0.0 <= a < 1.0
    assert a != c


def test_gco_is_governance_tx_over_total_tx():
    gov, total = 256, 1024
    gco = compute_overhead(gov, total)
    assert gco == 0.25
    assert compute_overhead(0, 0) == 0.0
    assert compute_overhead(100, 100) == 1.0
    assert compute_overhead(50, 200) <= 1.0
