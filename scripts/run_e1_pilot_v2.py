#!/usr/bin/env python3
"""E1-v2 pilot: frozen CGEA policies, strengthened mission workload.

Default campaign name is e1_pilot_v2_gpu (GPU-PHY traces, paper-result candidate).
The running/legacy folder results/e1_pilot_v2 is workload validation on the old
Sionna-wrap traces — do not mix the two families.

n=3 seeds. Outages cover frozen freshness bands. Do not start 10-seed production.
Do not retune aging/stale/hard expiry, capsules, or B5 variants.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from omegaconf import OmegaConf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cgea.experiments.parallel import (
    cfg_to_container,
    metrics_from_worker,
    resolve_simpy_workers,
    run_simpy_jobs,
)
from cgea.experiments.runner import ensure_trace
from cgea.metrics import RunMetrics, summarize_runs
from cgea.mission import CONSEQUENTIAL_ACTIONS, build_pipeline_mission
from cgea.types import config_hash, git_commit

BASELINES = ["B1", "B2", "B3", "B4", "B5_conservative", "B5_nominal", "B5_permissive"]
SEEDS = [0, 1, 2]
# Outage windows on duration 1400 s, start at 200 s. Thresholds unchanged.
OUTAGES = {
    "0": {"outage_disabled": True, "outage_start_s": 200.0, "outage_end_s": 200.0, "duration_s": 0.0},
    "150": {"outage_disabled": False, "outage_start_s": 200.0, "outage_end_s": 350.0, "duration_s": 150.0},
    "300": {"outage_disabled": False, "outage_start_s": 200.0, "outage_end_s": 500.0, "duration_s": 300.0},
    "500": {"outage_disabled": False, "outage_start_s": 200.0, "outage_end_s": 700.0, "duration_s": 500.0},
    "750": {"outage_disabled": False, "outage_start_s": 200.0, "outage_end_s": 950.0, "duration_s": 750.0},
    "1000": {"outage_disabled": False, "outage_start_s": 200.0, "outage_end_s": 1200.0, "duration_s": 1000.0},
}
OUTAGE_ORDER = ["0", "150", "300", "500", "750", "1000"]
CAMPAIGN = os.environ.get("CGEA_CAMPAIGN", "e1_pilot_v2_gpu")
REQUIRED_CLASSES = [
    "enter_exclusion_zone",
    "reassign_another_auv",
    "exceed_return_energy_reserve",
    "abandon_mandatory_inspection",
]


def load_base_cfg():
    acoustic = OmegaConf.load(ROOT / "configs" / "acoustic" / "default.yaml")
    mission = OmegaConf.load(ROOT / "configs" / "mission" / "pipeline.yaml")
    governance = OmegaConf.load(ROOT / "configs" / "governance" / "cgea.yaml")
    experiment = OmegaConf.load(ROOT / "configs" / "experiment" / "e1_pilot.yaml")
    return OmegaConf.create(
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


def _mission_kwargs(cfg):
    return dict(
        n_auvs=int(cfg.mission.n_auvs),
        pipeline_length_m=float(cfg.mission.pipeline_length_m),
        depth_m=float(cfg.mission.depth_m),
        battery_j=float(cfg.mission.battery_j),
        reserve_j=float(cfg.mission.reserve_j),
        mission_id=str(cfg.mission.mission_id),
        workload=str(cfg.mission.get("workload", "stress")),
    )


def prebuild_traces(cfg) -> dict[int, str]:
    ids = {}
    for seed in SEEDS:
        cfg_s = OmegaConf.merge(cfg, {"seed": int(seed)})
        world = build_pipeline_mission(**_mission_kwargs(cfg_s))
        positions = {aid: a.position for aid, a in world.auvs.items()}
        print(f"[trace] seed={seed} generating/loading shared ChannelTrace ...", flush=True)
        trace = ensure_trace(cfg_s, positions)
        ids[seed] = trace.trace_id
        print(f"[trace] seed={seed} trace_id={trace.trace_id}", flush=True)
    return ids


def _cfg_run(cfg, seed: int, ocfg: dict) -> Any:
    return OmegaConf.merge(
        cfg,
        {
            "seed": int(seed),
            "scenario": {
                "outage_disabled": ocfg["outage_disabled"],
                "outage_start_s": ocfg["outage_start_s"],
                "outage_end_s": ocfg["outage_end_s"],
                "partition_groups": None,
            },
            "experiment": {"name": str(cfg.experiment.name)},
            "force_regenerate_trace": False,
            "forbid_trace_generation": True,
        },
    )


def iter_campaign_jobs(cfg, trace_ids: dict[int, str], seeds=None) -> list[dict]:
    seeds = list(SEEDS if seeds is None else seeds)
    jobs = []
    for seed in seeds:
        for outage_name, ocfg in OUTAGES.items():
            for baseline in BASELINES:
                cfg_run = _cfg_run(cfg, seed, ocfg)
                jobs.append(
                    {
                        "seed": int(seed),
                        "outage": outage_name,
                        "outage_duration_s": ocfg["duration_s"],
                        "baseline": baseline,
                        "expected_trace_id": trace_ids[seed],
                        "cfg": cfg_to_container(cfg_run),
                    }
                )
    return jobs


def metrics_to_row(m: RunMetrics, *, outage_name: str, outage_duration_s: float, seed: int) -> dict:
    cov = m.extra.get("action_opportunity_coverage", {})
    return {
        "baseline": m.provenance.baseline,
        "outage": outage_name,
        "outage_duration_s": outage_duration_s,
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
        "useful_consequential_retention": m.extra.get(
            "useful_consequential_retention", m.useful_action_retention
        ),
        "useful_consequential_proposed": m.extra.get("useful_consequential_proposed", 0),
        "useful_consequential_allowed": m.extra.get("useful_consequential_allowed", 0),
        "denied_consequential_count": m.extra.get("denied_consequential_count", 0),
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
        "n_exercised_consequential_classes": cov.get("n_exercised_consequential_classes", 0),
        "valid_coverage": cov.get("valid_coverage", False),
        "exercised_classes": json.dumps(cov.get("exercised_consequential_classes", [])),
        "coverage_by_class": json.dumps(cov.get("by_class", {})),
        "coverage_by_connectivity": json.dumps(cov.get("by_connectivity", {})),
        "coverage_by_freshness": json.dumps(cov.get("by_freshness", {})),
        "utility_breakdown": json.dumps(m.extra.get("utility_breakdown", {})),
        "utility_freeze_id": m.extra.get("utility_freeze_id"),
        "connectivity_occupancy": json.dumps(m.extra.get("connectivity_state_occupancy", {})),
        "proposed_by_type": json.dumps(m.extra.get("proposed_by_type", {})),
        "decision_by_type": json.dumps(m.extra.get("decision_by_type", {})),
        "decision_by_risk_class": json.dumps(m.extra.get("decision_by_risk_class", {})),
        "backend": m.extra.get("trace_backend"),
        "allow_fallback": m.extra.get("allow_fallback"),
        "use_sionna_bridge": m.extra.get("use_sionna_bridge"),
        "sionna_device": m.extra.get("sionna_device"),
    }


def run_campaign(cfg, trace_ids: dict[int, str], max_workers: int | None = None) -> list[dict]:
    jobs = iter_campaign_jobs(cfg, trace_ids)
    workers = resolve_simpy_workers(requested=max_workers)
    print(f"[simpy] {len(jobs)} independent replays, workers={workers} (spawn ProcessPool)", flush=True)

    def _progress(i: int, payload: dict) -> None:
        print(
            f"[run] baseline={payload['baseline']} outage={payload['outage']} "
            f"seed={payload['seed']} elapsed={payload['elapsed_s']:.1f}s pid={payload['pid']}",
            flush=True,
        )

    payloads = run_simpy_jobs(jobs, max_workers=workers, on_complete=_progress)

    rows = []
    for job, payload in zip(jobs, payloads):
        m = metrics_from_worker(payload)
        rows.append(
            metrics_to_row(
                m,
                outage_name=str(job["outage"]),
                outage_duration_s=float(job["outage_duration_s"]),
                seed=int(job["seed"]),
            )
        )
    return rows


def write_tables(rows: list[dict], out_dir: Path) -> None:
    csv_path = out_dir / f"{CAMPAIGN}_raw.csv"
    fields = list(rows[0].keys())
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["baseline"], r["outage"])].append(r)

    metrics = [
        "mission_completion_ratio",
        "mission_utility",
        "high_risk_action_rate",
        "false_denial_rate",
        "useful_consequential_retention",
        "gco",
        "high_risk_action_count",
    ]
    summary = {}
    for (b, o), rs in sorted(groups.items()):
        summary[f"{b}|{o}"] = {m: summarize_runs([float(r[m]) for r in rs]) for m in metrics}
        summary[f"{b}|{o}"]["n"] = len(rs)
        summary[f"{b}|{o}"]["note"] = "pilot n=3; CI is indicative only"

    (out_dir / f"{CAMPAIGN}_summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / f"{CAMPAIGN}_raw.json").write_text(json.dumps(rows, indent=2))


def write_coverage_table(rows: list[dict], out_dir: Path) -> dict:
    """Aggregate action-opportunity coverage by experimental condition."""
    table = []
    by_cond: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cond[r["outage"]].append(r)

    campaign_classes = set()
    invalid = []
    for outage in OUTAGE_ORDER:
        rs = by_cond[outage]
        class_agg = {c: {"proposed": 0, "ALLOW": 0, "DENY": 0, "DEFER": 0} for c in [a.value for a in CONSEQUENTIAL_ACTIONS]}
        conn_proposed = defaultdict(int)
        fresh_proposed = defaultdict(int)
        for r in rs:
            for cls, d in json.loads(r["coverage_by_class"]).items():
                if cls not in class_agg:
                    class_agg[cls] = {"proposed": 0, "ALLOW": 0, "DENY": 0, "DEFER": 0}
                for k in ("proposed", "ALLOW", "DENY", "DEFER"):
                    class_agg[cls][k] += int(d.get(k, 0))
            for st, d in json.loads(r["coverage_by_connectivity"]).items():
                conn_proposed[st] += int(d.get("proposed", 0) if isinstance(d, dict) else 0)
            for st, d in json.loads(r["coverage_by_freshness"]).items():
                fresh_proposed[st] += int(d.get("proposed", 0) if isinstance(d, dict) else 0)
        exercised = [c for c, d in class_agg.items() if d["proposed"] > 0]
        campaign_classes.update(exercised)
        missing = [c for c in REQUIRED_CLASSES if class_agg.get(c, {}).get("proposed", 0) == 0]
        valid = len(missing) == 0
        if not valid:
            invalid.append({"outage": outage, "missing": missing})
        rec = {
            "outage": outage,
            "outage_duration_s": OUTAGES[outage]["duration_s"],
            "valid": valid,
            "exercised_classes": exercised,
            "by_class": class_agg,
            "consequential_by_connectivity": dict(conn_proposed),
            "consequential_by_freshness": dict(fresh_proposed),
        }
        table.append(rec)

    payload = {
        "required_classes": REQUIRED_CLASSES,
        "campaign_exercised": sorted(campaign_classes),
        "campaign_valid": len(invalid) == 0 and set(REQUIRED_CLASSES).issubset(campaign_classes),
        "invalid_conditions": invalid,
        "by_outage": table,
    }
    (out_dir / "action_opportunity_coverage.json").write_text(json.dumps(payload, indent=2))

    cov_csv = out_dir / "action_opportunity_coverage.csv"
    with cov_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "outage",
                "action_class",
                "proposed",
                "ALLOW",
                "DENY",
                "DEFER",
            ]
        )
        for rec in table:
            for cls, d in rec["by_class"].items():
                w.writerow([rec["outage"], cls, d["proposed"], d["ALLOW"], d["DENY"], d["DEFER"]])
    return payload


def _series(rows, baseline, metric):
    xs, ys, ylo, yhi = [], [], [], []
    for name in OUTAGE_ORDER:
        vals = [r[metric] for r in rows if r["baseline"] == baseline and r["outage"] == name]
        s = summarize_runs(vals)
        xs.append(OUTAGES[name]["duration_s"])
        ys.append(s["mean"])
        ylo.append(s["ci95_low"])
        yhi.append(s["ci95_high"])
    return np.array(xs), np.array(ys), np.array(ylo), np.array(yhi)


def plot_authority_timeline(out_dir: Path) -> Path | None:
    """B4 timeline for representative AUV from a long-outage seed-0 run."""
    b4_dir = out_dir / "B4"
    if not b4_dir.exists():
        return None
    chosen = None
    for p in sorted(b4_dir.glob("*.json")):
        data = json.loads(p.read_text())
        extra = data.get("extra", {})
        if extra.get("outage_duration_s") == 1000.0 and data.get("provenance", {}).get("random_seed") == 0:
            chosen = extra
            break
    if chosen is None:
        for p in sorted(b4_dir.glob("*.json")):
            data = json.loads(p.read_text())
            extra = data.get("extra", {})
            if extra.get("authority_timeline"):
                chosen = extra
                break
    if chosen is None:
        return None
    tl = chosen.get("authority_timeline") or []
    if not tl:
        return None

    t = np.array([e["time_s"] for e in tl], dtype=float)
    age = np.array([e["authority_age_s"] for e in tl], dtype=float)
    conn_map = {"CONNECTED": 4, "DEGRADED": 3, "PARTITIONED": 2, "ISOLATED": 1, "RECOVERING": 0}
    fresh_map = {"fresh": 3, "aging": 2, "stale": 1, "hard_expired": 0}
    conn = np.array([conn_map.get(e["connectivity"], -1) for e in tl])
    fresh = np.array([fresh_map.get(e["freshness"], -1) for e in tl])
    outage = np.array([1 if e.get("outage") else 0 for e in tl])

    fig, axes = plt.subplots(4, 1, figsize=(9.2, 8.2), sharex=True)
    auv = chosen.get("authority_timeline_auv", "auv")
    axes[0].fill_between(t, 0, outage, color="#9B2226", alpha=0.25, label="outage")
    axes[0].step(t, conn, where="post", color="#1B1B1E", linewidth=1.6)
    axes[0].set_yticks([0, 1, 2, 3, 4])
    axes[0].set_yticklabels(["REC", "ISO", "PAR", "DEG", "CON"])
    axes[0].set_ylabel("Connectivity")
    axes[0].set_title(f"B4 authority timeline ({auv}, 1000 s outage, seed 0)")
    axes[0].legend(loc="upper right", fontsize=8)

    axes[1].plot(t, age, color="#457B9D", linewidth=1.6)
    axes[1].axhline(180, color="#F4A261", linestyle="--", linewidth=1, label="aging 180 s")
    axes[1].axhline(400, color="#E9C46A", linestyle="--", linewidth=1, label="stale 400 s")
    axes[1].axhline(900, color="#E63946", linestyle="--", linewidth=1, label="hard 900 s")
    axes[1].set_ylabel("Authority age (s)")
    axes[1].legend(fontsize=7, loc="upper left")

    axes[2].step(t, fresh, where="post", color="#2A9D8F", linewidth=1.6)
    axes[2].set_yticks([0, 1, 2, 3])
    axes[2].set_yticklabels(["HARD", "STALE", "AGING", "FRESH"])
    axes[2].set_ylabel("Freshness")

    dec_c = {"ALLOW": "#2A9D8F", "DENY": "#E63946", "DEFER": "#F4A261"}
    for e in tl:
        if e.get("risk") != "consequential":
            continue
        y = {"ALLOW": 2, "DENY": 1, "DEFER": 0}[e["decision"]]
        axes[3].scatter(e["time_s"], y, c=dec_c[e["decision"]], marker="o", s=28, zorder=3)
    axes[3].set_yticks([0, 1, 2])
    axes[3].set_yticklabels(["DEFER", "DENY", "ALLOW"])
    axes[3].set_ylabel("Consequential")
    axes[3].set_xlabel("Time (s)")
    fig.tight_layout()
    path = out_dir / "b4_authority_timeline.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


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
        ax.set_title(ylabel + " vs outage duration (E1-v2 pilot, n=3)")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8, ncol=2)
        fig.tight_layout()
        fig.savefig(out_dir / fname, dpi=160)
        plt.close(fig)

    line_plot("mission_utility", "Mission utility", "e1_utility_vs_outage.png")
    line_plot("high_risk_action_rate", "High-risk action rate", "e1_highrisk_vs_outage.png")
    line_plot("false_denial_rate", "False-denial rate (oracle)", "e1_falsedenial_vs_outage.png")
    line_plot("useful_consequential_retention", "Useful consequential retention", "e1_retention_vs_outage.png")

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
    ax.set_title("Risk–utility frontier (E1-v2 pilot)")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(out_dir / "e1_risk_utility_frontier.png", dpi=160)
    plt.close(fig)


def critical_interpretation(rows: list[dict], coverage: dict, out_dir: Path) -> str:
    def mean_for(b, metric, outage=None):
        sel = [r for r in rows if r["baseline"] == b and (outage is None or r["outage"] == outage)]
        return float(np.mean([r[metric] for r in sel])) if sel else float("nan")

    lines = []
    lines.append("# E1-v2 Pilot — Workload-strengthened interpretation")
    lines.append("")
    lines.append(f"- git_commit: `{git_commit(ROOT)}`")
    lines.append("- CGEA thresholds unchanged (aging 180 / stale 400 / hard 900).")
    lines.append("- Utility weights frozen as utility_v2_frozen_2026-09-28 before inspecting B4.")
    lines.append("- n=3 seeds. Production 10-seed sweep was not started.")
    lines.append(f"- Coverage valid: {coverage.get('campaign_valid')} classes={coverage.get('campaign_exercised')}")
    lines.append("")
    lines.append("## Mean metrics (all outages pooled)")
    for b in BASELINES:
        lines.append(
            f"- **{b}**: utility={mean_for(b,'mission_utility'):.3f}, "
            f"highrisk={mean_for(b,'high_risk_action_rate'):.3f}, "
            f"falsedeny={mean_for(b,'false_denial_rate'):.3f}, "
            f"cons_retention={mean_for(b,'useful_consequential_retention'):.3f}"
        )
    lines.append("")
    lines.append("## Mean metrics at 1000 s outage")
    for b in BASELINES:
        lines.append(
            f"- **{b}**: utility={mean_for(b,'mission_utility','1000'):.3f}, "
            f"highrisk={mean_for(b,'high_risk_action_rate','1000'):.3f}, "
            f"cons_retention={mean_for(b,'useful_consequential_retention','1000'):.3f}"
        )

    b4_u = mean_for("B4", "mission_utility", "1000")
    b4_r = mean_for("B4", "high_risk_action_rate", "1000")
    b5n_u = mean_for("B5_nominal", "mission_utility", "1000")
    b5n_r = mean_for("B5_nominal", "high_risk_action_rate", "1000")
    b2_u = mean_for("B2", "mission_utility", "1000")
    b3_u = mean_for("B3", "mission_utility", "1000")
    b1_u = mean_for("B1", "mission_utility", "1000")

    lines.append("")
    lines.append("## Real mission consequences vs policy construction")
    util_spread = float(np.nanmax([mean_for(b, "mission_utility") for b in BASELINES]) - np.nanmin([mean_for(b, "mission_utility") for b in BASELINES]))
    if util_spread < 1.0:
        lines.append(
            "- **Still mostly policy construction**: pooled utility spread is small (<1). "
            "Governance is being exercised, but mission outcomes remain weakly separated."
        )
    else:
        lines.append(
            f"- **Mission consequences are visible**: pooled utility spread ≈ {util_spread:.2f} "
            "under the frozen weights (completion, anomaly, reassignment, penalties)."
        )
    if abs(b4_u - b5n_u) < 0.5 and abs(b4_r - b5n_r) < 0.05:
        lines.append(
            "- At 1000 s outage B4 still converges toward B5_nominal on both utility and risk. "
            "That is expected once authority is STALE/HARD_EXPIRED (see timeline); it is a "
            "lifecycle effect, not evidence to retune thresholds."
        )
    lines.append(
        f"- 1000 s snapshot: B1 u={b1_u:.2f}; B2 u={b2_u:.2f}; B3 u={b3_u:.2f}; "
        f"B4 u={b4_u:.2f} r={b4_r:.2f}; B5_nominal u={b5n_u:.2f} r={b5n_r:.2f}."
    )
    lines.append("")
    lines.append("## Reconciliation (E3 note)")
    lines.append(
        "- `reconcile_partitions()` still uses a formulaic latency "
        "(5 + 0.01·actions + 2·conflicts) and does not send provenance packets on the SimPy network. "
        "E3 remains unpublished; this does not block E1/E4."
    )
    lines.append("")
    lines.append("## Go / No-Go")
    lines.append("- Do not start the 10-seed production sweep from this script.")
    lines.append("- Do not retune CGEA thresholds after viewing B4.")

    text = "\n".join(lines) + "\n"
    (out_dir / "e1_pilot_v2_interpretation.md").write_text(text)
    return text


def main() -> None:
    cfg = load_base_cfg()
    cfg.experiment.name = CAMPAIGN
    out_dir = Path(cfg.paths.results) / CAMPAIGN
    out_dir.mkdir(parents=True, exist_ok=True)
    print("git_commit", git_commit(ROOT))
    print("config_hash(base)", config_hash(cfg))
    print("utility freeze", "utility_v2_frozen_2026-09-28")

    (out_dir / "utility_freeze.json").write_text(
        Path(ROOT / "configs" / "mission" / "utility.yaml").read_text()
    )
    (out_dir / "b5_variants_a_priori.json").write_text(
        json.dumps(
            {
                "B5_conservative": "consequential ALLOW only if CONNECTED and supervisor_reachable; else DEFER/DENY",
                "B5_nominal": "CONNECTED allow; DEGRADED allow iff supervisor else DEFER; PARTITIONED/ISOLATED/RECOVERING ALLOW",
                "B5_permissive": "consequential ALLOW in all connectivity states",
                "note": "Unchanged from E1-v1; not retuned",
            },
            indent=2,
        )
    )

    print("[gpu] Bellhop/CIR + Sionna PHY traces (parent process only)", flush=True)
    trace_ids = prebuild_traces(cfg)
    (out_dir / "shared_trace_ids.json").write_text(json.dumps(trace_ids, indent=2))
    print("[simpy] traces persisted; CPU process pool will only replay parquet", flush=True)

    rows = run_campaign(cfg, trace_ids)
    write_tables(rows, out_dir)
    coverage = write_coverage_table(rows, out_dir)
    if not coverage["campaign_valid"]:
        raise RuntimeError(
            f"Workload coverage invalid: missing classes {coverage.get('invalid_conditions')} "
            f"exercised={coverage.get('campaign_exercised')}"
        )
    make_plots(rows, out_dir)
    plot_authority_timeline(out_dir)
    interp = critical_interpretation(rows, coverage, out_dir)
    print(interp)


if __name__ == "__main__":
    main()
