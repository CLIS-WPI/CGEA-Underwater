#!/usr/bin/env python3
"""Verify Phase-0 Docker environment for CGEA underwater platform."""
from __future__ import annotations

import subprocess
import sys


def main() -> int:
    print("=== CGEA environment verification ===")
    print(f"Python: {sys.version}")

    # nvidia-smi
    try:
        out = subprocess.check_output(["nvidia-smi"], text=True)
        print("--- nvidia-smi ---")
        print("\n".join(out.splitlines()[:12]))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: nvidia-smi: {exc}")
        return 1

    import torch

    cuda_ok = torch.cuda.is_available()
    print(f"torch.cuda.is_available() == {cuda_ok}")
    if not cuda_ok:
        print("FAIL: CUDA not available to PyTorch")
        return 1
    print(f"torch version: {torch.__version__}")
    print(f"device: {torch.cuda.get_device_name(0)}")

    import sionna

    print(f"sionna.__version__ == {sionna.__version__}")
    if not str(sionna.__version__).startswith("2.1"):
        print(f"WARN: expected sionna 2.1.x, got {sionna.__version__}")

    import aubellhop

    print(f"aubellhop imported: {aubellhop}")
    print("PASS: environment verification succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
