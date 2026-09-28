#!/usr/bin/env python3
"""E1 pilot campaign: B1–B5(+3 B5 variants) × outage{0,medium,long} × seeds{0,1,2}.

Hard paper gates abort the campaign on failure.
Does NOT tune CGEA after viewing results.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from omegaconf import OmegaConf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cgea.experiments.runner import ensure_trace, run_single
from cgea.metrics import summarize_runs
from cgea.mission import build_pipeline_mission
from cgea.types import config_hash, git_commit


# Fixed a priori — do not change after seeing CGEA results
BASELINES = ["B1", "B2", "B3", "B4", "B5_conservative", "B5_nominal", "B5_permissive"]
SEEDS = [0, 1, 2]
# Outage windows on frozen mission duration 900s, start at 200s
OUTAGES = {
    "0": {"outage_disabled": True, "outage_start_s": 200.0, "outage_end_s": 200.0, "duration_s": 0.0},
    "medium": {"outage_disabled": False, "outage_start_s": 200.0, "outage_end_s": 500.0, "duration_s": 300.0},
    "long": {"outage_disabled": False, "outage_start_s": 200.0, "outage_end_s": 700.0, "duration_s": 500.0},
}


def load_base_cfg():
    acoustic = OmegaConf.load(ROOT / "configs" / "acoustic" / "default.yaml")
    mission = OmegaConf.load(ROOT / "configs" / "mission" / "pipeline.yaml")
    governance = OmegaConf.load(ROOT / "configs" / "governance" / "cgea.yaml")
    experiment = OmegaConf.load(ROOT / "configs" / "experiment" / "e1_pilot.yaml")
    cfg = OmegaConf.create(
        {
            "seed": 0,
            "paths": {
                "root": str(ROOT),
                "traces": str(ROOT / "traces"),
                "results": str(ROOT / "results"),
            },
            "network": {"queue_limit": 32, "header_bytes": 32},
            "agent": {"consequential_period_s": 120.0},
            "scenario": {
                "outage_start_s": 200.0,
                "outage_end_s": 500.0,
                "partition_groups": None,
                "outage_disabled": False,
            },
            "force_regenerate_trace": False,
            "acoustic": acoustic,
            "mission": mission,
            "governance": governance,
            "experiment": experiment,
        }
    )
    return cfg


def prebuild_traces(cfg) -> dict[int, str]:
    """Build one shared trace per seed (independent of baseline/outage)."""
    ids = {}
    for seed in SEEDS:
        cfg_s = OmegaConf.merge(cfg, {"seed": int(seed)})
        world = build_pipeline_mission(
            n_auvs=int(cfg_s.mission.n_auvs),
            pipeline_length_m=float(cfg_s.mission.pipeline_length_m),
            depth_m=float(cfg_s.mission.depth_m),
            battery_j=float(cfg_s.mission.battery_j),
            reserve_j=float(cfg_s.mission.reserve_j),
            mission_id=str(cfg_s.mission.mission_id),
        )
        positions = {aid: a.position for aid, a in world.auvs.items()}
        print(f"[trace] seed={seed} generating/loading shared ChannelTrace ...", flush=True)
        trace = ensure_trace(cfg_s, positions)
        ids[seed] = trace.trace_id
        print(f"[trace] seed={seed} trace_id={trace.trace_id}", flush=True)
    return ids


def run_campaign(cfg, trace_ids: dict[int, str]) -> list[dict]:
    rows = []
    out_root = Path(cfg.paths.results) / "e1_pilot"
    out_root.mkdir(parents=True, exist_ok=True)

    for seed in SEEDS:
        for outage_name, ocfg in OUTAGES.items():
            for baseline in BASELINES:
                cfg_run = OmegaConf.merge(
                    cfg,
                    {
                        "seed": int(seed),
                        "scenario": {
                            "outage_disabled": ocfg["outage_disabled"],
                            "outage_start_s": ocfg["outage_start_s"],
                            "outage_end_s": ocfg["outage_end_s"],
                            "partition_groups": None,
                        },
                        "experiment": {"name": "e1_pilot"},
                    },
                )
                print(
                    f"[run] baseline={baseline} outage={outage_name}({ocfg['duration_s']}s) seed={seed}",
                    flush=True,
                )
                m = run_single(cfg_run, baseline)
                if m.provenance.channel_trace_id != trace_ids[seed]:
                    raise RuntimeError(
                        f"Trace mismatch: expected {trace_ids[seed]}, got {m.provenance.channel_trace_id}"
                    )
                row = {
                    "baseline": baseline,
                    "outage": outage_name,
                    "outage_duration_s": ocfg["duration_s"],
                    "seed": seed,
                    "trace_id": m.provenance.channel_trace_id,
                    "config_hash": m.provenance.configuration_hash,
                    "git_commit": m.provenance.git_commit,
                    "mission_completion_ratio": m.mission_completion_ratio,
                    "mission_utility": m.mission_utility,
                    "high_risk_action_count": m.extra.get("high_risk_action_count", 0),
                    "high_risk_action_rate": m.unauthorized_high_risk_action_rate,
                    "false_denial_count": m.extra.get("false_denial_count", 0),
                    "false_denial_rate": m.governance_false_denial_rate,
                    "useful_action_retention": m.useful_action_retention,
                    "total_communication_bytes": m.total_communication_bytes,
                    "governance_bytes": m.governance_bytes,
                    "gco": m.governance_communication_overhead,
                    "energy_propulsion_j": m.energy_propulsion_j,
                    "energy_communication_j": m.energy_communication_j,
                    "energy_compute_j": m.energy_compute_j,
                    "decisions_allow": m.extra.get("decisions_allow", 0),
                    "decisions_deny": m.extra.get("decisions_deny", 0),
                    "decisions_defer": m.extra.get("decisions_defer", 0),
                    "consequential_proposed": m.extra.get("consequential_proposed", 0),
                    "consequential_allowed": m.extra.get("consequential_allowed", 0),
                    "connectivity_occupancy": json.dumps(m.extra.get("connectivity_state_occupancy", {})),
                    "proposed_by_type": json.dumps(m.extra.get("proposed_by_type", {})),
                    "decision_by_type": json.dumps(m.extra.get("decision_by_type", {})),
                    "decision_by_risk_class": json.dumps(m.extra.get("decision_by_risk_class", {})),
                    "backend": m.extra.get("trace_backend"),
                    "allow_fallback": m.extra.get("allow_fallback"),
                    "use_sionna_bridge": m.extra.get("use_sionna_bridge"),
                    "sionna_device": m.extra.get("sionna_device"),
                }
                rows.append(row)
                # per-run json already saved by run_single
    return rows


def write_tables(rows: list[dict], out_dir: Path) -> None:
    csv_path = out_dir / "e1_pilot_raw.csv"
    fields = list(rows[0].keys())
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # Aggregate mean ± 95% CI by baseline × outage
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["baseline"], r["outage"])].append(r)

    metrics = [
        "mission_completion_ratio",
        "mission_utility",
        "high_risk_action_rate",
        "false_denial_rate",
        "useful_action_retention",
        "gco",
        "high_risk_action_count",
    ]
    summary = {}
    for (b, o), rs in sorted(groups.items()):
        summary[f"{b}|{o}"] = {
            m: summarize_runs([float(r[m]) for r in rs]) for m in metrics
        }
        summary[f"{b}|{o}"]["n"] = len(rs)
        summary[f"{b}|{o}"]["note"] = "pilot n=3; CI is indicative only"

    (out_dir / "e1_pilot_summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "e1_pilot_raw.json").write_text(json.dumps(rows, indent=2))
    print(f"Wrote {csv_path}")


def _series(rows, baseline, metric):
    """Mean metric vs outage duration for one baseline."""
    xs, ys, ylo, yhi = [], [], [], []
    for name in ["0", "medium", "long"]:
        vals = [r[metric] for r in rows if r["baseline"] == baseline and r["outage"] == name]
        s = summarize_runs(vals)
        xs.append(OUTAGES[name]["duration_s"])
        ys.append(s["mean"])
        ylo.append(s["ci95_low"])
        yhi.append(s["ci95_high"])
    return np.array(xs), np.array(ys), np.array(ylo), np.array(yhi)


def make_plots(rows: list[dict], out_dir: Path) -> None:
    colors = {
        "B1": "#1B1B1E",
        "B2": "#E63946",
        "B3": "#457B9D",
        "B4": "#2A9D8F",
        "B5_conservative": "#F4A261",
        "B5_nominal": "#E9C46A",
        "B5_permissive": "#9B2226",
    }
    markers = {
        "B1": "o",
        "B2": "s",
        "B3": "^",
        "B4": "D",
        "B5_conservative": "v",
        "B5_nominal": "P",
        "B5_permissive": "X",
    }

    def line_plot(metric, ylabel, fname):
        fig, ax = plt.subplots(figsize=(7.2, 4.2))
        for b in BASELINES:
            x, y, ylo, yhi = _series(rows, b, metric)
            ax.plot(x, y, marker=markers[b], color=colors[b], label=b, linewidth=1.8)
            ax.fill_between(x, ylo, yhi, color=colors[b], alpha=0.12)
        ax.set_xlabel("Outage duration (s)")
        ax.set_ylabel(ylabel)
        ax.set_title(ylabel + " vs outage duration (E1 pilot, n=3)")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8, ncol=2)
        fig.tight_layout()
        fig.savefig(out_dir / fname, dpi=160)
        plt.close(fig)

    line_plot("mission_utility", "Mission utility", "e1_utility_vs_outage.png")
    line_plot("high_risk_action_rate", "High-risk action rate", "e1_highrisk_vs_outage.png")
    line_plot("false_denial_rate", "False denial rate", "e1_falsedenial_vs_outage.png")
    line_plot("useful_action_retention", "Useful action retention", "e1_retention_vs_outage.png")

    # Plot 5: risk–utility frontier (all runs as points + mean per baseline)
    fig, ax = plt.subplots(figsize=(6.4, 5.2))
    for b in BASELINES:
        xs = [r["high_risk_action_rate"] for r in rows if r["baseline"] == b]
        ys = [r["mission_utility"] for r in rows if r["baseline"] == b]
        ax.scatter(xs, ys, color=colors[b], marker=markers[b], alpha=0.35, s=28)
        ax.scatter(
            [np.mean(xs)],
            [np.mean(ys)],
            color=colors[b],
            marker=markers[b],
            s=110,
            edgecolors="k",
            linewidths=0.6,
            label=b,
            zorder=3,
        )
    ax.set_xlabel("High-risk action rate")
    ax.set_ylabel("Mission utility")
    ax.set_title("Risk–utility frontier (E1 pilot)")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(out_dir / "e1_risk_utility_frontier.png", dpi=160)
    plt.close(fig)
    print(f"Wrote plots under {out_dir}")


def aggregate_action_counts(rows: list[dict]) -> dict:
    proposed = defaultdict(int)
    decisions = defaultdict(lambda: defaultdict(int))
    for r in rows:
        for k, v in json.loads(r["proposed_by_type"]).items():
            proposed[k] += int(v)
        for atype, dd in json.loads(r["decision_by_type"]).items():
            for dec, n in dd.items():
                decisions[atype][dec] += int(n)
    return {
        "proposed_by_type_total": dict(proposed),
        "decision_by_type_total": {k: dict(v) for k, v in decisions.items()},
    }


def critical_interpretation(rows: list[dict], out_dir: Path) -> str:
    def mean_for(b, metric, outage=None):
        sel = [r for r in rows if r["baseline"] == b and (outage is None or r["outage"] == outage)]
        return float(np.mean([r[metric] for r in sel])) if sel else float("nan")

    lines = []
    lines.append("# E1 Pilot — Critical Interpretation")
    lines.append("")
    lines.append(f"- git_commit: `{git_commit(ROOT)}`")
    lines.append(f"- seeds: {SEEDS} (pilot only; n=3)")
    lines.append("- B5 variants fixed a priori: conservative / nominal / permissive")
    lines.append("- No CGEA threshold tuning after viewing results.")
    lines.append("")
    lines.append("## Mean metrics (all outages pooled)")
    for b in BASELINES:
        lines.append(
            f"- **{b}**: utility={mean_for(b,'mission_utility'):.3f}, "
            f"highrisk={mean_for(b,'high_risk_action_rate'):.3f}, "
            f"falsedeny={mean_for(b,'false_denial_rate'):.3f}, "
            f"retention={mean_for(b,'useful_action_retention'):.3f}"
        )
    lines.append("")
    lines.append("## Mean metrics at long outage")
    for b in BASELINES:
        lines.append(
            f"- **{b}**: utility={mean_for(b,'mission_utility','long'):.3f}, "
            f"highrisk={mean_for(b,'high_risk_action_rate','long'):.3f}"
        )
    lines.append("")
    b4_u = mean_for("B4", "mission_utility", "long")
    b4_r = mean_for("B4", "high_risk_action_rate", "long")
    b5n_u = mean_for("B5_nominal", "mission_utility", "long")
    b5n_r = mean_for("B5_nominal", "high_risk_action_rate", "long")
    b2_u = mean_for("B2", "mission_utility", "long")
    b2_r = mean_for("B2", "high_risk_action_rate", "long")
    b1_u = mean_for("B1", "mission_utility", "long")
    b1_r = mean_for("B1", "high_risk_action_rate", "long")

    lines.append("## Does CGEA show a real risk–utility tradeoff?")
    # Heuristic interpretation flags (not tuning)
    if b4_r + 0.05 < b5n_r and abs(b4_u - b5n_u) < 0.35:
        lines.append(
            "- **Supportive signal**: B4 utility is close to B5_nominal while high-risk rate is lower — "
            "consistent with a risk–utility frontier shift (pilot-level only)."
        )
    elif abs(b4_u - b5n_u) < 0.15 and abs(b4_r - b5n_r) < 0.05:
        lines.append(
            "- **Warning**: B4 ≈ B5_nominal on both utility and risk. Do **not** tune CGEA to win; "
            "this suggests current contribution may be insufficient or difference is policy-construction."
        )
    else:
        lines.append(
            "- **Mixed**: Inspect frontier plot. Differences may be driven by B5 policy construction "
            "(esp. permissive vs conservative) rather than capsule/freshness/reconciliation alone."
        )

    lines.append("")
    lines.append("## Policy-construction caveat")
    lines.append(
        "- B1 denies consequential without supervisor → low risk, possibly lower utility under long outage."
    )
    lines.append("- B2 always allows → high risk by construction under partition.")
    lines.append("- B3 static low-risk only → low risk, may stall consequential mission repairs.")
    lines.append(
        "- B5_permissive ≈ B2 for consequential under outage; B5_conservative ≈ B1-like under partition. "
        "Compare B4 primarily to **B5_nominal**, not only extremes."
    )
    lines.append(
        f"- Long-outage snapshot: B1(u={b1_u:.2f},r={b1_r:.2f}), B2(u={b2_u:.2f},r={b2_r:.2f}), "
        f"B4(u={b4_u:.2f},r={b4_r:.2f}), B5_nominal(u={b5n_u:.2f},r={b5n_r:.2f})."
    )
    lines.append("")
    lines.append("## Go / No-Go for 10-seed production")
    lines.append("- Review raw table + frontier before any production sweep.")
    lines.append("- Do not retune freshness/authority thresholds based on this pilot.")

    text = "\n".join(lines) + "\n"
    (out_dir / "e1_pilot_interpretation.md").write_text(text)
    return text


def main() -> None:
    cfg = load_base_cfg()
    out_dir = Path(cfg.paths.results) / "e1_pilot"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("git_commit", git_commit(ROOT))
    print("config_hash(base)", config_hash(cfg))

    # Record B5 policy definitions before any CGEA-looking analysis
    (out_dir / "b5_variants_a_priori.json").write_text(
        json.dumps(
            {
                "B5_conservative": "consequential ALLOW only if CONNECTED and supervisor_reachable; else DEFER/DENY",
                "B5_nominal": "CONNECTED allow; DEGRADED allow iff supervisor else DEFER; PARTITIONED/ISOLATED/RECOVERING ALLOW",
                "B5_permissive": "consequential ALLOW in all connectivity states",
                "note": "Fixed before examining CGEA pilot results",
            },
            indent=2,
        )
    )

    trace_ids = prebuild_traces(cfg)
    (out_dir / "shared_trace_ids.json").write_text(json.dumps(trace_ids, indent=2))

    rows = run_campaign(cfg, trace_ids)
    write_tables(rows, out_dir)
    make_plots(rows, out_dir)
    counts = aggregate_action_counts(rows)
    (out_dir / "e1_pilot_action_counts.json").write_text(json.dumps(counts, indent=2))
    interp = critical_interpretation(rows, out_dir)
    print(interp)


if __name__ == "__main__":
    main()
