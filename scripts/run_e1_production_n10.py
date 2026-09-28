#!/usr/bin/env python3
"""E1 production n=10: frozen paper policy. 3 env × 10 seeds × 6 outages × 7 baselines.

Does not retune CGEA, B5, utility, oracle, PHY, or reconciliation.
One GPU-PHY trace per (environment, seed); baselines replay only.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from omegaconf import OmegaConf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_e1_pilot_v2 import BASELINES, OUTAGE_ORDER, OUTAGES, _mission_kwargs  # noqa: E402
from run_e1_pilot_v3 import ENV_FILES, load_base_cfg  # noqa: E402
from cgea.experiments.parallel import (  # noqa: E402
    cfg_to_container,
    metrics_from_worker,
    resolve_simpy_workers,
    run_simpy_jobs,
)
from cgea.experiments.runner import SAFE_USEFUL_RETENTION_FREEZE_ID, ensure_trace
from cgea.governance import PAPER_POLICY_VERSION
from cgea.mission import build_pipeline_mission
from cgea.mission.utility import UTILITY_FREEZE_ID
from cgea.types import git_commit

CAMPAIGN = os.environ.get("CGEA_CAMPAIGN", "e1_production_n10")
SEEDS = list(range(10))
HEADLINE_BASELINES = ["B3", "B4", "B5_nominal"]


def mean_ci(xs: list[float]) -> tuple[float, float, float]:
    a = np.asarray([float(x) for x in xs if x is not None], dtype=float)
    n = int(a.size)
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    m = float(np.mean(a))
    if n == 1:
        return m, m, m
    se = float(np.std(a, ddof=1) / np.sqrt(n))
    # Student-t 95% for moderate n (n=30 → t≈2.045); 1.96 is slightly tight.
    tcrit = 1.95996398454
    if n <= 30:
        tcrit = float(
            [
                12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
                2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
                2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
            ][n - 1]
        )
    h = tcrit * se
    return m, m - h, m + h


def prebuild(cfg, seeds) -> dict[int, str]:
    ids = {}
    for seed in seeds:
        cfg_s = OmegaConf.merge(cfg, {"seed": int(seed)})
        world = build_pipeline_mission(**_mission_kwargs(cfg_s))
        positions = {aid: a.position for aid, a in world.auvs.items()}
        env_id = str(cfg.acoustic.environment_id)
        print(f"[trace] env={env_id} seed={seed}", flush=True)
        trace = ensure_trace(cfg_s, positions)
        ids[int(seed)] = trace.trace_id
        print(f"[trace] {trace.trace_id}", flush=True)
    return ids


def iter_jobs(cfg, trace_ids: dict[int, str], environment_id: str) -> list[dict]:
    jobs = []
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
                        "experiment": {"name": CAMPAIGN},
                        "force_regenerate_trace": False,
                        "forbid_trace_generation": True,
                    },
                )
                jobs.append(
                    {
                        "seed": int(seed),
                        "outage": outage_name,
                        "outage_duration_s": ocfg["duration_s"],
                        "baseline": baseline,
                        "environment_id": environment_id,
                        "expected_trace_id": trace_ids[int(seed)],
                        "cfg": cfg_to_container(cfg_run),
                    }
                )
    return jobs


def row_from(m, job: dict) -> dict:
    extra = m.extra
    return {
        "git_commit": m.provenance.git_commit,
        "config_hash": m.provenance.configuration_hash,
        "policy_version": extra.get("policy_version", PAPER_POLICY_VERSION),
        "utility_freeze_id": extra.get("utility_freeze_id", UTILITY_FREEZE_ID),
        "safe_useful_retention_freeze_id": extra.get(
            "safe_useful_retention_freeze_id", SAFE_USEFUL_RETENTION_FREEZE_ID
        ),
        "environment_id": job["environment_id"],
        "seed": job["seed"],
        "outage": job["outage"],
        "outage_duration_s": job["outage_duration_s"],
        "baseline": m.provenance.baseline,
        "trace_id": m.provenance.channel_trace_id,
        "mission_utility": m.mission_utility,
        "hard_safety_violation_rate": extra.get("violation_per_proposal"),
        "violation_per_executed_consequential": extra.get("violation_per_executed_consequential"),
        "hard_safety_violation_count": extra.get("hard_safety_violation_count"),
        "safe_useful_retention": extra.get("safe_useful_retention"),
        "useful_consequential_retention": extra.get(
            "useful_consequential_retention", m.useful_action_retention
        ),
        "semantic_conflicts_per_mission": extra.get("semantic_conflicts_per_mission", m.conflict_count),
        "conflict_by_kind": json.dumps(extra.get("conflict_by_kind", {})),
        "governance_tx_bytes": extra.get("governance_tx_bytes", m.governance_bytes),
        "mission_tx_bytes": extra.get("mission_tx_bytes"),
        "total_tx_bytes": extra.get("total_tx_bytes", m.total_communication_bytes),
        "gco": extra.get("gco", m.governance_communication_overhead),
        "auv_reauth_fraction": extra.get("auv_reauth_fraction"),
        "auv_timeout_fraction": extra.get("auv_timeout_fraction"),
        "auv_reauth_latency_s": json.dumps(extra.get("auv_reauth_latency_s", {})),
        "recon_status": extra.get("recon_status"),
        "recon_latency_s": extra.get("recon_latency_s", m.reconciliation_latency_s),
    }


def _f(row, key):
    v = row.get(key)
    if v is None or v == "":
        return None
    return float(v)


def paired_table(rows: list[dict], a: str, b: str, fields: list[str]) -> list[dict]:
    by = defaultdict(dict)
    for r in rows:
        by[(r["environment_id"], r["seed"], r["outage"])][r["baseline"]] = r
    out = []
    for key, bm in sorted(by.items()):
        if a not in bm or b not in bm:
            continue
        rec = {
            "environment_id": key[0],
            "seed": key[1],
            "outage": key[2],
            "pair": f"{a}-{b}",
            "trace_id": bm[a]["trace_id"],
        }
        for f in fields:
            va, vb = _f(bm[a], f), _f(bm[b], f)
            rec[f"delta_{f}"] = None if va is None or vb is None else va - vb
        out.append(rec)
    return out


def summarize_outage(rows: list[dict], field: str, baseline: str, outage: str) -> dict:
    xs = [_f(r, field) for r in rows if r["baseline"] == baseline and r["outage"] == outage]
    xs = [x for x in xs if x is not None]
    m, lo, hi = mean_ci(xs)
    return {"baseline": baseline, "outage": outage, "metric": field, "n": len(xs), "mean": m, "ci95_lo": lo, "ci95_hi": hi}


def plot_metric(rows, field, ylabel, path_stem, baselines=HEADLINE_BASELINES):
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    xs = [OUTAGES[o]["duration_s"] for o in OUTAGE_ORDER]
    for b in baselines:
        means, lo, hi = [], [], []
        for o in OUTAGE_ORDER:
            s = summarize_outage(rows, field, b, o)
            means.append(s["mean"])
            lo.append(s["ci95_lo"])
            hi.append(s["ci95_hi"])
        yerr = np.vstack([np.array(means) - np.array(lo), np.array(hi) - np.array(means)])
        ax.errorbar(xs, means, yerr=yerr, marker="o", capsize=3, label=b)
    ax.set_xlabel("Outage duration (s)")
    ax.set_ylabel(ylabel)
    ax.legend(frameon=False)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path_stem.with_suffix(".png"), dpi=200)
    fig.savefig(path_stem.with_suffix(".pdf"))
    plt.close(fig)


def write_manifest(out_dir: Path, traces: dict, n: int, commit: str) -> None:
    text = "\n".join(
        [
            "# E1 production n=10",
            "",
            f"- git_commit: `{commit}`",
            f"- campaign: `{CAMPAIGN}`",
            f"- n_runs: {n} (expect 1260 = 3 env × 10 seeds × 6 outages × 7 baselines)",
            f"- policy_version: `{PAPER_POLICY_VERSION}`",
            f"- utility_freeze_id: `{UTILITY_FREEZE_ID}`",
            f"- safe_useful_retention_freeze_id: `{SAFE_USEFUL_RETENTION_FREEZE_ID}`",
            "- workers: 24 SimPy CPU; GPU traces prebuilt per (environment, seed)",
            "- paired: B4−B5_nominal (primary), B4−B3",
            "- B5_conservative / B5_permissive: robustness bounds, not the main manuscript pair",
            f"- traces: `{json.dumps(traces)}`",
            "",
            "Do not retune policy from these numbers. Diagnostic campaigns remain in results/.",
            "",
        ]
    )
    (out_dir / "production_manifest.md").write_text(text)


def main() -> None:
    os.environ.setdefault("CGEA_SIMPY_WORKERS", "24")
    out_dir = ROOT / "results" / CAMPAIGN
    paper_dir = out_dir / "paper"
    out_dir.mkdir(parents=True, exist_ok=True)
    paper_dir.mkdir(parents=True, exist_ok=True)
    commit = git_commit(ROOT)
    print("git_commit", commit, "campaign", CAMPAIGN, "policy", PAPER_POLICY_VERSION, flush=True)
    workers = resolve_simpy_workers()
    print(f"[simpy] workers={workers} jobs=1260", flush=True)

    all_jobs = []
    traces = {}
    for env_path in ENV_FILES:
        cfg = load_base_cfg(env_path)
        cfg.experiment.name = CAMPAIGN
        env_id = str(cfg.acoustic.environment_id)
        print("[gpu] prebuild", env_id, flush=True)
        ids = prebuild(cfg, SEEDS)
        traces[env_id] = ids
        all_jobs.extend(iter_jobs(cfg, ids, env_id))

    (out_dir / "shared_trace_ids.json").write_text(json.dumps(traces, indent=2))
    assert len(all_jobs) == 1260, len(all_jobs)

    def progress(i, payload):
        if i % 50 == 0 or i == len(all_jobs) - 1:
            print(f"[run] {i+1}/{len(all_jobs)} {payload['baseline']} {payload.get('outage')} seed={payload['seed']}", flush=True)

    payloads = run_simpy_jobs(all_jobs, max_workers=workers, on_complete=progress)
    rows = [row_from(metrics_from_worker(p), job) for job, p in zip(all_jobs, payloads)]

    fields = list(rows[0].keys())
    with (out_dir / "e1_production_n10_raw.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    (out_dir / "e1_production_n10_raw.json").write_text(json.dumps(rows, indent=2))

    headline = [
        "mission_utility",
        "hard_safety_violation_rate",
        "safe_useful_retention",
        "semantic_conflicts_per_mission",
        "governance_tx_bytes",
        "mission_tx_bytes",
        "total_tx_bytes",
        "gco",
        "auv_reauth_fraction",
        "auv_timeout_fraction",
    ]
    table = [summarize_outage(rows, metric, b, o) for o in OUTAGE_ORDER for b in BASELINES for metric in headline]
    with (paper_dir / "paper_results_table.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["baseline", "outage", "metric", "n", "mean", "ci95_lo", "ci95_hi"])
        w.writeheader()
        w.writerows(table)

    p1 = paired_table(rows, "B4", "B5_nominal", headline)
    p2 = paired_table(rows, "B4", "B3", headline)
    paired_rows = []
    for pair_name, plist in (("B4-B5_nominal", p1), ("B4-B3", p2)):
        by_o = defaultdict(list)
        for r in plist:
            by_o[r["outage"]].append(r)
        for o in OUTAGE_ORDER:
            rec = {"pair": pair_name, "outage": o, "n": len(by_o[o])}
            for metric in headline:
                xs = [x[f"delta_{metric}"] for x in by_o[o] if x.get(f"delta_{metric}") is not None]
                m, lo, hi = mean_ci(xs)
                rec[f"{metric}_mean"] = m
                rec[f"{metric}_ci95_lo"] = lo
                rec[f"{metric}_ci95_hi"] = hi
            paired_rows.append(rec)
    with (paper_dir / "paired_effects.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(paired_rows[0].keys()))
        w.writeheader()
        w.writerows(paired_rows)
    (out_dir / "paired_b4_minus_b5_nominal.json").write_text(json.dumps(p1, indent=2))

    plot_metric(rows, "mission_utility", "Mission utility", paper_dir / "utility_vs_outage")
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    xs = [OUTAGES[o]["duration_s"] for o in OUTAGE_ORDER]
    ax2 = ax.twinx()
    for b, color in (("B4", "C0"), ("B5_nominal", "C1"), ("B3", "C2")):
        means = [summarize_outage(rows, "safe_useful_retention", b, o)["mean"] for o in OUTAGE_ORDER]
        ax.plot(xs, means, "o-", color=color, label=f"{b} safe ret.")
        v = [summarize_outage(rows, "hard_safety_violation_rate", b, o)["mean"] for o in OUTAGE_ORDER]
        ax2.plot(xs, v, "s--", color=color, alpha=0.7, label=f"{b} viol.")
    ax.set_xlabel("Outage duration (s)")
    ax.set_ylabel("Safe useful retention")
    ax2.set_ylabel("Hard-safety violation rate")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, frameon=False, fontsize=7)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(paper_dir / "safe_retention_and_safety_vs_outage.png", dpi=200)
    fig.savefig(paper_dir / "safe_retention_and_safety_vs_outage.pdf")
    plt.close(fig)

    plot_metric(rows, "auv_reauth_fraction", "AUV reauthorization fraction", paper_dir / "reauthorization_vs_outage")
    plot_metric(rows, "gco", "GCO (governance TX / total TX)", paper_dir / "governance_overhead_vs_outage")

    write_manifest(out_dir, traces, len(rows), commit)
    print((out_dir / "production_manifest.md").read_text())


if __name__ == "__main__":
    main()
