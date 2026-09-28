"""Environment verification entrypoint."""

from __future__ import annotations


def main() -> None:
    from pathlib import Path
    import runpy

    verify = Path(__file__).resolve().parents[3] / "docker" / "verify_env.py"
    runpy.run_path(str(verify), run_name="__main__")


if __name__ == "__main__":
    main()
