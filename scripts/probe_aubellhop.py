#!/usr/bin/env python3
"""Probe aubellhop API for real Bellhop arrivals."""
from __future__ import annotations

import numpy as np


def main() -> None:
    import aubellhop as bh

    print("aubellhop", bh)
    print("has compute_arrivals", hasattr(bh, "compute_arrivals"))
    print("has compute", hasattr(bh, "compute"))
    print("dir sample", [x for x in dir(bh) if not x.startswith("_")][:40])

    ssp = np.column_stack([[0.0, 50.0, 100.0, 200.0], [1520.0, 1510.0, 1505.0, 1515.0]])
    env = bh.Environment(
        name="t",
        frequency=25000.0,
        soundspeed=ssp,
        bottom_depth=200.0,
        source_depth=50.0,
        receiver_depth=60.0,
        receiver_range=1000.0,
    )
    print("env type", type(env))
    print("env fields sample", {k: getattr(env, k, None) for k in ["frequency", "bottom_depth", "source_depth", "receiver_range", "task"] if hasattr(env, k)})

    if hasattr(bh, "compute_arrivals"):
        arr = bh.compute_arrivals(env)
    else:
        if hasattr(env, "task"):
            env.task = "arrivals"
        arr = bh.compute(env)

    print("arr type", type(arr))
    if hasattr(arr, "columns"):
        print("cols", list(arr.columns))
        print(arr.head())
    elif isinstance(arr, dict):
        print("keys", list(arr.keys()))
        for k, v in arr.items():
            print(k, type(v), getattr(v, "shape", None))
    elif hasattr(arr, "__dict__"):
        print("attrs", list(vars(arr).keys())[:40])
    else:
        print(repr(arr)[:500])


if __name__ == "__main__":
    main()
