"""Hydra CLI entrypoint: cgea-run."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from omegaconf import DictConfig, OmegaConf

from cgea.experiments.runner import run_experiment
from cgea.metrics import cohens_d, summarize_runs


def _find_config_dir() -> Path:
    env = os.environ.get("CGEA_ROOT")
    candidates = []
    if env:
        candidates.append(Path(env) / "configs")
    candidates.extend(
        [
            Path.cwd() / "configs",
            Path(__file__).resolve().parents[3] / "configs",  # src layout
            Path("/workspace/cgea-underwater/configs"),
        ]
    )
    for c in candidates:
        if (c / "config.yaml").exists():
            return c
    raise FileNotFoundError(
        "Could not locate configs/config.yaml. Set CGEA_ROOT or run from the repo root."
    )


def main(argv: list[str] | None = None) -> None:
    """Compose Hydra config from disk so installed-package entrypoints work."""
    from hydra import compose, initialize_config_dir
    from hydra.core.global_hydra import GlobalHydra

    argv = list(sys.argv[1:] if argv is None else argv)
    config_dir = str(_find_config_dir().resolve())

    GlobalHydra.instance().clear()
    with initialize_config_dir(version_base=None, config_dir=config_dir):
        cfg = compose(config_name="config", overrides=argv)

    print(OmegaConf.to_yaml(cfg))
    results = run_experiment(cfg)
    by_b: dict[str, list] = {}
    for m in results:
        by_b.setdefault(m.provenance.baseline, []).append(m)

    summary = {}
    for b, runs in by_b.items():
        summary[b] = {
            "mission_completion_ratio": summarize_runs([r.mission_completion_ratio for r in runs]),
            "unauthorized_high_risk_action_rate": summarize_runs(
                [r.unauthorized_high_risk_action_rate for r in runs]
            ),
            "governance_communication_overhead": summarize_runs(
                [r.governance_communication_overhead for r in runs]
            ),
            "mission_utility": summarize_runs([r.mission_utility for r in runs]),
            "n": len(runs),
        }

    if "B4" in by_b and "B5" in by_b:
        summary["effect_size_B4_vs_B5_unauth"] = cohens_d(
            [r.unauthorized_high_risk_action_rate for r in by_b["B4"]],
            [r.unauthorized_high_risk_action_rate for r in by_b["B5"]],
        )

    out = Path(cfg.paths.results) / str(cfg.experiment.name) / "summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2))
    print(f"Wrote {out}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
