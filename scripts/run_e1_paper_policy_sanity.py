#!/usr/bin/env python3
"""27-run sanity: paper_risk_bounded_v1 + hop-by-hop DIGEST. Stop after this grid.

1 env × 3 seeds × 3 outages × B3/B4/B5_nominal. Does not start full production.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from omegaconf import OmegaConf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_e1_pilot_v2 import OUTAGES, SEEDS, _mission_kwargs  # noqa: E402
from run_e1_pilot_v3 import load_base_cfg  # noqa: E402
from cgea.experiments.parallel import cfg_to_container, metrics_from_worker, resolve_simpy_workers, run_simpy_jobs
from cgea.experiments.runner import ensure_trace
from cgea.governance import PAPER_POLICY_VERSION
from cgea.mission import build_pipeline_mission
from cgea.types import git_commit

CAMPAIGN = os.environ.get("CGEA_CAMPAIGN", "e1_paper_policy_v1_sanity")
ENV_PATH = ROOT / "configs" / "acoustic" / "default.yaml"
BASELINES = ["B3", "B4", "B5_nominal"]
OUTAGE_NAMES = ["0", "500", "1000"]


def main() -> None:
    out_dir = ROOT / "results" / CAMPAIGN
    out_dir.mkdir(parents=True, exist_ok=True)
    print("git_commit", git_commit(ROOT), "policy", PAPER_POLICY_VERSION, "campaign", CAMPAIGN)
    cfg = load_base_cfg(ENV_PATH)
    cfg.experiment.name = CAMPAIGN
    env_id = str(cfg.acoustic.environment_id)
    traces = {}
    for seed in SEEDS:
        cfg_s = OmegaConf.merge(cfg, {"seed": int(seed)})
        world = build_pipeline_mission(**_mission_kwargs(cfg_s))
        positions = {aid: a.position for aid, a in world.auvs.items()}
        print(f"[trace] seed={seed}", flush=True)
        trace = ensure_trace(cfg_s, positions)
        traces[seed] = trace.trace_id
        print(f"[trace] {trace.trace_id}", flush=True)
    (out_dir / "shared_trace_ids.json").write_text(json.dumps({env_id: traces}, indent=2))

    jobs = []
    for seed in SEEDS:
        for outage in OUTAGE_NAMES:
            ocfg = OUTAGES[outage]
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
                        "outage": outage,
                        "outage_duration_s": ocfg["duration_s"],
                        "baseline": baseline,
                        "environment_id": env_id,
                        "expected_trace_id": traces[seed],
                        "cfg": cfg_to_container(cfg_run),
                    }
                )
    workers = resolve_simpy_workers()
    print(f"[simpy] workers={workers} jobs={len(jobs)}", flush=True)
    payloads = run_simpy_jobs(jobs, max_workers=workers)
    rows = []
    for job, p in zip(jobs, payloads):
        m = metrics_from_worker(p)
        extra = m.extra
        fresh = extra.get("action_opportunity_coverage", {}).get("by_freshness", {})
        rows.append(
            {
                "baseline": m.provenance.baseline,
                "environment_id": job["environment_id"],
                "outage": job["outage"],
                "seed": job["seed"],
                "recon_status": extra.get("recon_status"),
                "recon_latency_s": extra.get("recon_latency_s", m.reconciliation_latency_s),
                "hard_safety_violation_count": extra.get("hard_safety_violation_count"),
                "violation_per_proposal": extra.get("violation_per_proposal"),
                "violation_per_executed_consequential": extra.get("violation_per_executed_consequential"),
                "useful_consequential_retention": extra.get(
                    "useful_consequential_retention", m.useful_action_retention
                ),
                "safe_useful_retention": extra.get("safe_useful_retention"),
                "auv_reauth_fraction": extra.get("auv_reauth_fraction"),
                "auv_timeout_fraction": extra.get("auv_timeout_fraction"),
                "auv_reauth_latency_s": json.dumps(extra.get("auv_reauth_latency_s", {})),
                "governance_tx_bytes": extra.get("governance_tx_bytes", m.governance_bytes),
                "mission_utility": m.mission_utility,
                "semantic_conflicts_per_mission": extra.get("semantic_conflicts_per_mission", m.conflict_count),
                "gco": extra.get("gco", m.governance_communication_overhead),
                "coverage_by_freshness": json.dumps(fresh),
                "reason_code_counts": json.dumps(extra.get("reason_code_counts", {})),
                "policy_version": extra.get("policy_version"),
                "git_commit": m.provenance.git_commit,
            }
        )

    fields = list(rows[0].keys())
    with (out_dir / "sanity_raw.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    (out_dir / "sanity_raw.json").write_text(json.dumps(rows, indent=2))

    disc = [r for r in rows if r["outage"] != "0"]
    recon_ok = sum(1 for r in disc if r["recon_status"] == "ok")
    report = {
        "n": len(rows),
        "policy_version": PAPER_POLICY_VERSION,
        "reconciliation_success_rate_disconnected": recon_ok / max(len(disc), 1),
        "mean_auv_reauth_fraction_disconnected": float(
            np.mean([float(r["auv_reauth_fraction"]) for r in disc if r["auv_reauth_fraction"] not in (None, "")])
        )
        if any(r["auv_reauth_fraction"] not in (None, "") for r in disc)
        else None,
        "recon_status_counts": dict(
            defaultdict(int, {k: sum(1 for r in rows if r["recon_status"] == k) for k in {x["recon_status"] for x in rows}})
        ),
        "by_baseline": {},
    }
    for b in BASELINES:
        sub = [r for r in rows if r["baseline"] == b]
        report["by_baseline"][b] = {
            "mean_utility": float(np.mean([float(r["mission_utility"]) for r in sub])),
            "mean_retention_legacy": float(np.mean([float(r["useful_consequential_retention"]) for r in sub])),
            "mean_safe_useful_retention": float(
                np.nanmean([float(r["safe_useful_retention"]) for r in sub if r["safe_useful_retention"] not in (None, "")])
            ),
            "mean_violation_per_proposal": float(
                np.mean([float(r["violation_per_proposal"] or 0) for r in sub])
            ),
            "mean_hard_safety_count": float(
                np.mean([float(r["hard_safety_violation_count"] or 0) for r in sub])
            ),
            "mean_conflicts": float(np.mean([float(r["semantic_conflicts_per_mission"]) for r in sub])),
            "mean_gco": float(np.mean([float(r["gco"]) for r in sub])),
            "mean_auv_reauth_fraction": float(
                np.nanmean(
                    [float(r["auv_reauth_fraction"]) for r in sub if r["auv_reauth_fraction"] not in (None, "")]
                )
            )
            if any(r["auv_reauth_fraction"] not in (None, "") for r in sub)
            else None,
            "mean_auv_timeout_fraction": float(
                np.nanmean(
                    [float(r["auv_timeout_fraction"]) for r in sub if r["auv_timeout_fraction"] not in (None, "")]
                )
            )
            if any(r["auv_timeout_fraction"] not in (None, "") for r in sub)
            else None,
            "mean_governance_tx_bytes": float(np.mean([float(r["governance_tx_bytes"] or 0) for r in sub])),
            "recon_ok_disconnected": sum(1 for r in sub if r["outage"] != "0" and r["recon_status"] == "ok"),
            "recon_partial_disconnected": sum(1 for r in sub if r["outage"] != "0" and r["recon_status"] == "partial"),
            "recon_n_disconnected": sum(1 for r in sub if r["outage"] != "0"),
        }
    b4 = [r for r in rows if r["baseline"] == "B4"]
    report["b4_freshness"] = [json.loads(r["coverage_by_freshness"]) if isinstance(r["coverage_by_freshness"], str) else r["coverage_by_freshness"] for r in b4]
    (out_dir / "sanity_summary.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in report if k != "b4_freshness"}, indent=2))


if __name__ == "__main__":
    main()
