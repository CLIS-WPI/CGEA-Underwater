#!/usr/bin/env python3
"""E1-v3 n=3: semantic recon, live RECOVERING, mission GCO, freshness challenge.

Does not overwrite E1-v2 (b0d804b). Does not retune CGEA/B5/utility weights.
Grid: 3 env × 3 seeds × 6 outages × 7 baselines = 378 runs. No 10-seed production.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from omegaconf import OmegaConf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_e1_pilot_v2 import (  # noqa: E402
    BASELINES,
    OUTAGE_ORDER,
    OUTAGES,
    SEEDS,
    _mission_kwargs,
)
from cgea.experiments.parallel import (  # noqa: E402
    cfg_to_container,
    metrics_from_worker,
    resolve_simpy_workers,
    run_simpy_jobs,
)
from cgea.experiments.runner import ensure_trace
from cgea.metrics import RunMetrics, summarize_runs
from cgea.mission import build_pipeline_mission
from cgea.types import config_hash, git_commit

CAMPAIGN = os.environ.get("CGEA_CAMPAIGN", "e1_pilot_v3_gpu")
ENV_FILES = [
    ROOT / "configs" / "acoustic" / "default.yaml",
    ROOT / "configs" / "acoustic" / "env_moderate.yaml",
    ROOT / "configs" / "acoustic" / "env_strong.yaml",
]


def load_base_cfg(acoustic_path: Path):
    acoustic = OmegaConf.load(acoustic_path)
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
            "network": {
                "queue_limit": 32,
                "header_bytes": 32,
                "governance_reserved_queue_slots": 4,
            },
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


def prebuild(cfg) -> dict[int, str]:
    ids = {}
    for seed in SEEDS:
        cfg_s = OmegaConf.merge(cfg, {"seed": int(seed)})
        world = build_pipeline_mission(**_mission_kwargs(cfg_s))
        positions = {aid: a.position for aid, a in world.auvs.items()}
        env_id = str(cfg.acoustic.environment_id)
        print(f"[trace] env={env_id} seed={seed}", flush=True)
        trace = ensure_trace(cfg_s, positions)
        ids[seed] = trace.trace_id
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
                        "expected_trace_id": trace_ids[seed],
                        "cfg": cfg_to_container(cfg_run),
                    }
                )
    return jobs


def row_from(m: RunMetrics, job: dict) -> dict:
    extra = m.extra
    fresh = extra.get("action_opportunity_coverage", {}).get("by_freshness", {})
    return {
        "baseline": m.provenance.baseline,
        "environment_id": job["environment_id"],
        "outage": job["outage"],
        "outage_duration_s": job["outage_duration_s"],
        "seed": job["seed"],
        "trace_id": m.provenance.channel_trace_id,
        "mission_utility": m.mission_utility,
        "utility_without_conflict_penalty": extra.get("utility_without_conflict_penalty"),
        "semantic_conflicts_per_mission": extra.get("semantic_conflicts_per_mission", m.conflict_count),
        "conflict_by_kind": json.dumps(extra.get("conflict_by_kind", {})),
        "disconnected_consequential_execution_rate": extra.get(
            "disconnected_consequential_execution_rate", m.unauthorized_high_risk_action_rate
        ),
        "violation_per_proposal": extra.get("violation_per_proposal"),
        "violation_per_executed_consequential": extra.get("violation_per_executed_consequential"),
        "gco": extra.get("gco", m.governance_communication_overhead),
        "governance_tx_bytes": extra.get("governance_tx_bytes", m.governance_bytes),
        "mission_tx_bytes": extra.get("mission_tx_bytes"),
        "total_tx_bytes": extra.get("total_tx_bytes", m.total_communication_bytes),
        "recon_status": extra.get("recon_status"),
        "recon_latency_s": extra.get("recon_latency_s", m.reconciliation_latency_s),
        "reason_code_counts": json.dumps(extra.get("reason_code_counts", {})),
        "freshness_proposed": json.dumps({k: v.get("proposed", 0) if isinstance(v, dict) else 0 for k, v in fresh.items()}),
        "coverage_by_freshness": json.dumps(fresh),
        "coverage_by_connectivity": json.dumps(
            extra.get("action_opportunity_coverage", {}).get("by_connectivity", {})
        ),
        "git_commit": m.provenance.git_commit,
        "config_hash": m.provenance.configuration_hash,
    }


def paired_deltas(rows: list[dict]) -> list[dict]:
    by = defaultdict(dict)
    for r in rows:
        key = (r["environment_id"], r["seed"], r["outage"])
        by[key][r["baseline"]] = r
    out = []
    for key, bm in sorted(by.items()):
        if "B4" not in bm or "B5_nominal" not in bm:
            continue
        a, b = bm["B4"], bm["B5_nominal"]
        def d(field):
            va, vb = a.get(field), b.get(field)
            if va is None or vb is None:
                return None
            return float(va) - float(vb)
        out.append(
            {
                "environment_id": key[0],
                "seed": key[1],
                "outage": key[2],
                "delta_utility": d("mission_utility"),
                "delta_utility_without_conflict": d("utility_without_conflict_penalty"),
                "delta_disconnected_execution": d("disconnected_consequential_execution_rate"),
                "delta_violation_per_executed": d("violation_per_executed_consequential"),
                "delta_semantic_conflicts": d("semantic_conflicts_per_mission"),
                "trace_id": a["trace_id"],
            }
        )
    return out


def b4_freshness_table(rows: list[dict]) -> dict:
    table = []
    for r in rows:
        if r["baseline"] != "B4":
            continue
        raw = json.loads(r["coverage_by_freshness"]) if isinstance(r["coverage_by_freshness"], str) else r["coverage_by_freshness"]
        rec = {"environment_id": r["environment_id"], "seed": r["seed"], "outage": r["outage"]}
        for band in ("fresh", "aging", "stale", "hard_expired"):
            cell = raw.get(band, {}) if isinstance(raw, dict) else {}
            rec[f"{band}_proposed"] = int(cell.get("proposed", 0) or 0)
            rec[f"{band}_ALLOW"] = int(cell.get("ALLOW", 0) or 0)
            rec[f"{band}_DENY"] = int(cell.get("DENY", 0) or 0)
            rec[f"{band}_DEFER"] = int(cell.get("DEFER", 0) or 0)
        table.append(rec)
    long = [t for t in table if t["outage"] == "1000"]
    measured_ok = all(
        t["fresh_proposed"] > 0
        and t["aging_proposed"] > 0
        and t["stale_proposed"] > 0
        and t["hard_expired_proposed"] > 0
        for t in long
    ) if long else False
    return {"rows": table, "measured_gate_outage_1000": measured_ok}


def write_report(rows, paired, freshness, out_dir: Path, traces: dict) -> str:
    gco0 = [float(r["gco"]) for r in rows if r["outage"] == "0" and r["gco"] is not None]
    median_gco0 = float(np.median(gco0)) if gco0 else float("nan")
    recov = 0
    for r in rows:
        conn = json.loads(r["coverage_by_connectivity"]) if isinstance(r["coverage_by_connectivity"], str) else {}
        recov += int((conn.get("RECOVERING") or {}).get("proposed", 0) or 0)
    du = [p["delta_utility"] for p in paired if p["delta_utility"] is not None]
    duc = [p["delta_utility_without_conflict"] for p in paired if p["delta_utility_without_conflict"] is not None]
    lines = [
        "# E1-v3 n=3 report",
        "",
        f"- checkpoint_v2: b0d804b",
        f"- n_runs: {len(rows)} (expect 378)",
        f"- traces: {json.dumps(traces)}",
        f"- median GCO (outage=0): {median_gco0:.4f}",
        f"- RECOVERING consequential proposed (all rows sum): {recov}",
        f"- B4 measured freshness gate (outage 1000): {freshness.get('measured_gate_outage_1000')}",
        f"- paired delta_utility B4-B5_nominal mean: {float(np.mean(du)) if du else float('nan'):.3f}",
        f"- paired delta_utility_without_conflict mean: {float(np.mean(duc)) if duc else float('nan'):.3f}",
        "",
        "## H. Advantage after removing capsule-circular conflicts",
    ]
    if du and float(np.mean(du)) > 1.0:
        lines.append(
            "- Mean paired utility delta favors B4. Check `utility_without_conflict_penalty` "
            "and B4 freshness/reason codes before attributing this to freshness contraction."
        )
    elif du and float(np.mean(du)) < -1.0:
        lines.append("- Mean paired utility delta favors B5_nominal after semantic conflicts.")
    else:
        lines.append("- No large paired utility gap; see split risk metrics and conflict types.")
    if not freshness.get("measured_gate_outage_1000"):
        lines.append("- Do **not** claim a freshness mechanism: B4 measured-state gate failed.")
    lines.append("- Do not start 10-seed production from this script.")
    text = "\n".join(lines) + "\n"
    (out_dir / "e1_pilot_v3_interpretation.md").write_text(text)
    return text


def main() -> None:
    out_dir = ROOT / "results" / CAMPAIGN
    out_dir.mkdir(parents=True, exist_ok=True)
    print("git_commit", git_commit(ROOT), "campaign", CAMPAIGN)
    workers = resolve_simpy_workers()
    print(f"[simpy] workers={workers} jobs=378", flush=True)

    all_jobs = []
    traces = {}
    for env_path in ENV_FILES:
        cfg = load_base_cfg(env_path)
        cfg.experiment.name = CAMPAIGN
        env_id = str(cfg.acoustic.environment_id)
        print("[gpu] prebuild", env_id, flush=True)
        ids = prebuild(cfg)
        traces[env_id] = ids
        all_jobs.extend(iter_jobs(cfg, ids, env_id))

    (out_dir / "shared_trace_ids.json").write_text(json.dumps(traces, indent=2))
    (out_dir / "e1_v2_checkpoint.json").write_text(
        (ROOT / "results" / "e1_v2_checkpoint.json").read_text()
    )
    print(f"[simpy] launching {len(all_jobs)} replays", flush=True)

    def progress(i, payload):
        if i % 20 == 0 or i == len(all_jobs) - 1:
            print(f"[run] {i+1}/{len(all_jobs)} {payload['baseline']} {payload.get('outage')} seed={payload['seed']}", flush=True)

    payloads = run_simpy_jobs(all_jobs, max_workers=workers, on_complete=progress)
    rows = [row_from(metrics_from_worker(p), job) for job, p in zip(all_jobs, payloads)]

    fields = list(rows[0].keys())
    with (out_dir / "e1_pilot_v3_gpu_raw.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    (out_dir / "e1_pilot_v3_gpu_raw.json").write_text(json.dumps(rows, indent=2))

    paired = paired_deltas(rows)
    (out_dir / "paired_b4_minus_b5_nominal.json").write_text(json.dumps(paired, indent=2))
    freshness = b4_freshness_table(rows)
    (out_dir / "b4_freshness_measured.json").write_text(json.dumps(freshness, indent=2))

    gco0 = [float(r["gco"]) for r in rows if r["outage"] == "0"]
    median_gco0 = float(np.median(gco0)) if gco0 else 1.0
    summary = {
        "n": len(rows),
        "median_gco_outage0": median_gco0,
        "paired_delta_utility_mean": float(np.mean([p["delta_utility"] for p in paired])) if paired else None,
        "paired_delta_utility_without_conflict_mean": float(
            np.mean([p["delta_utility_without_conflict"] for p in paired])
        )
        if paired
        else None,
        "freshness_gate": freshness.get("measured_gate_outage_1000"),
    }
    (out_dir / "e1_pilot_v3_gpu_summary.json").write_text(json.dumps(summary, indent=2))

    if median_gco0 >= 0.99:
        raise RuntimeError(f"GCO sanity failed: median connected GCO={median_gco0}")
    if not freshness.get("measured_gate_outage_1000"):
        print("WARNING: B4 measured freshness gate failed for outage=1000", flush=True)

    print(write_report(rows, paired, freshness, out_dir, traces))


if __name__ == "__main__":
    main()
