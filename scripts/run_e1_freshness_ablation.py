#!/usr/bin/env python3
"""B4 vs B4-NoFreshness (b4_no_freshness_v1). Frozen traces only. 120 runs.

3 env × 5 seeds × 4 outages × 2 variants. Does not retune B4 thresholds.
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

from run_e1_pilot_v2 import OUTAGES, _mission_kwargs  # noqa: E402
from run_e1_pilot_v3 import ENV_FILES, load_base_cfg  # noqa: E402
from run_e1_production_n10 import mean_ci  # noqa: E402
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

CAMPAIGN = os.environ.get("CGEA_CAMPAIGN", "e1_freshness_ablation")
SEEDS = [0, 1, 2, 3, 4]
VARIANTS = ["B4", "B4-NoFreshness"]
OUTAGE_ORDER = ["150", "300", "500", "1000"]
N_BOOT = 10_000
BOOT_SEED = 20260928
ABLATION_ID = "b4_no_freshness_v1"

BOUNDED = {
    "safe_useful_retention",
    "hard_safety_violation_rate",
}


def percentile_bootstrap_ci(xs, rng):
    a = np.asarray([float(x) for x in xs if x is not None], dtype=float)
    if a.size == 0:
        return float("nan"), float("nan")
    if a.size == 1:
        return float(a[0]), float(a[0])
    draws = rng.choice(a, size=(N_BOOT, a.size), replace=True).mean(axis=1)
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return float(lo), float(hi)


def prebuild(cfg, seeds) -> dict[int, str]:
    ids = {}
    for seed in seeds:
        cfg_s = OmegaConf.merge(cfg, {"seed": int(seed)})
        world = build_pipeline_mission(**_mission_kwargs(cfg_s))
        positions = {aid: a.position for aid, a in world.auvs.items()}
        print(f"[trace] env={cfg.acoustic.environment_id} seed={seed}", flush=True)
        trace = ensure_trace(cfg_s, positions)
        ids[int(seed)] = trace.trace_id
        print(f"[trace] {trace.trace_id}", flush=True)
    return ids


def iter_jobs(cfg, trace_ids, environment_id):
    jobs = []
    for seed in SEEDS:
        for outage_name in OUTAGE_ORDER:
            ocfg = OUTAGES[outage_name]
            for baseline in VARIANTS:
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
                        "experiment": {
                            "name": CAMPAIGN,
                            "log_consequential_decisions": True,
                        },
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


def _f(v):
    if v is None or v == "":
        return None
    x = float(v)
    return None if np.isnan(x) else x


def row_from(m, job):
    extra = m.extra
    byc = (extra.get("action_opportunity_coverage") or {}).get("by_class") or {}
    reas = byc.get("reassign_another_auv") or {}
    rc = extra.get("reason_code_counts") or {}
    return {
        "git_commit": m.provenance.git_commit,
        "config_hash": m.provenance.configuration_hash,
        "policy_version": extra.get("policy_version", PAPER_POLICY_VERSION),
        "utility_freeze_id": extra.get("utility_freeze_id", UTILITY_FREEZE_ID),
        "safe_useful_retention_freeze_id": extra.get(
            "safe_useful_retention_freeze_id", SAFE_USEFUL_RETENTION_FREEZE_ID
        ),
        "ablation_variant": extra.get("ablation_variant"),
        "environment_id": job["environment_id"],
        "seed": job["seed"],
        "outage": job["outage"],
        "outage_duration_s": job["outage_duration_s"],
        "baseline": m.provenance.baseline,
        "trace_id": m.provenance.channel_trace_id,
        "mission_utility": m.mission_utility,
        "hard_safety_violation_rate": extra.get("violation_per_proposal"),
        "safe_useful_retention": extra.get("safe_useful_retention"),
        "semantic_conflicts_per_mission": extra.get("semantic_conflicts_per_mission", m.conflict_count),
        "contradictory_reassignment": extra.get("contradictory_reassignment"),
        "duplicate_work_count": extra.get("duplicate_work_count"),
        "useful_reassignment_count": extra.get("useful_reassignment_count"),
        "missed_mandatory": extra.get("missed_mandatory"),
        "reassign_proposed": reas.get("proposed", 0),
        "reassign_allow": reas.get("ALLOW", 0),
        "reassign_deny": reas.get("DENY", 0),
        "reassign_defer": reas.get("DEFER", 0),
        "deny_conditional_unmet": rc.get("DENY_CONDITIONAL_UNMET", 0),
        "deny_hard_expiry": rc.get("DENY_HARD_EXPIRY", 0),
        "deny_forbidden": rc.get("DENY_FORBIDDEN", 0),
        "executed_reassignment_count": extra.get("useful_reassignment_count"),
        "consequential_decision_log": extra.get("consequential_decision_log") or [],
        "conflict_events": extra.get("conflict_events") or [],
    }


def main():
    os.environ.setdefault("CGEA_SIMPY_WORKERS", "24")
    out_dir = ROOT / "results" / CAMPAIGN
    paper = out_dir / "paper"
    out_dir.mkdir(parents=True, exist_ok=True)
    paper.mkdir(parents=True, exist_ok=True)
    commit = git_commit(ROOT)
    print("git_commit", commit, "campaign", CAMPAIGN, "ablation", ABLATION_ID, flush=True)
    workers = resolve_simpy_workers()
    print(f"[simpy] workers={workers} jobs=120", flush=True)

    all_jobs = []
    traces = {}
    for env_path in ENV_FILES:
        cfg = load_base_cfg(env_path)
        cfg.experiment.name = CAMPAIGN
        env_id = str(cfg.acoustic.environment_id)
        print("[gpu] reuse/prebuild", env_id, flush=True)
        ids = prebuild(cfg, SEEDS)
        traces[env_id] = ids
        all_jobs.extend(iter_jobs(cfg, ids, env_id))
    (out_dir / "shared_trace_ids.json").write_text(json.dumps(traces, indent=2))
    assert len(all_jobs) == 120, len(all_jobs)

    def progress(i, payload):
        if i % 10 == 0 or i == len(all_jobs) - 1:
            print(f"[run] {i+1}/{len(all_jobs)} {payload['baseline']} {payload.get('outage')} seed={payload['seed']}", flush=True)

    payloads = run_simpy_jobs(all_jobs, max_workers=workers, on_complete=progress)
    rows = [row_from(metrics_from_worker(p), job) for job, p in zip(all_jobs, payloads)]
    slim = [{k: v for k, v in r.items() if k not in ("consequential_decision_log", "conflict_events")} for r in rows]
    (out_dir / "freshness_ablation_raw.json").write_text(json.dumps(slim, indent=2))
    with (out_dir / "freshness_ablation_raw.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(slim[0].keys()))
        w.writeheader()
        w.writerows(slim)
    (out_dir / "freshness_ablation_logs.json").write_text(
        json.dumps(
            [
                {
                    "environment_id": r["environment_id"],
                    "seed": r["seed"],
                    "outage": r["outage"],
                    "baseline": r["baseline"],
                    "trace_id": r["trace_id"],
                    "consequential_decision_log": r["consequential_decision_log"],
                    "conflict_events": r["conflict_events"],
                }
                for r in rows
            ]
        )
    )
    print("wrote raw", len(rows), flush=True)
    write_paper_artifacts(rows, paper, commit, traces)
    print((paper / "freshness_ablation_case.md").read_text())


def write_paper_artifacts(rows, paper: Path, commit: str, traces: dict) -> None:
    metrics = [
        "safe_useful_retention",
        "hard_safety_violation_rate",
        "semantic_conflicts_per_mission",
        "contradictory_reassignment",
        "duplicate_work_count",
        "useful_reassignment_count",
        "missed_mandatory",
        "mission_utility",
    ]
    rng = np.random.default_rng(BOOT_SEED)
    summary = []
    for o in [None] + OUTAGE_ORDER:
        subset = rows if o is None else [r for r in rows if r["outage"] == o]
        for b in VARIANTS:
            br = [r for r in subset if r["baseline"] == b]
            rec = {"scope": "all" if o is None else f"outage_{o}", "baseline": b, "n": len(br)}
            for m in metrics:
                xs = [_f(r[m]) for r in br]
                xs = [x for x in xs if x is not None]
                mu = float(np.mean(xs)) if xs else float("nan")
                rec[f"{m}_mean"] = mu
                if m in BOUNDED:
                    lo, hi = percentile_bootstrap_ci(xs, rng)
                else:
                    _, lo, hi = mean_ci(xs)
                rec[f"{m}_ci95_lo"] = lo
                rec[f"{m}_ci95_hi"] = hi
            summary.append(rec)
    with (paper / "freshness_ablation_summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
        w.writeheader()
        w.writerows(summary)

    by_cell = defaultdict(dict)
    for r in rows:
        by_cell[(r["environment_id"], r["seed"], r["outage"])][r["baseline"]] = r
    paired_rows = []
    for o in OUTAGE_ORDER:
        rec = {"pair": "B4-B4-NoFreshness", "outage": o}
        cells = [k for k in by_cell if k[2] == o and "B4" in by_cell[k] and "B4-NoFreshness" in by_cell[k]]
        rec["n"] = len(cells)
        for m in metrics:
            deltas = []
            for k in cells:
                va, vb = _f(by_cell[k]["B4"][m]), _f(by_cell[k]["B4-NoFreshness"][m])
                if va is not None and vb is not None:
                    deltas.append(va - vb)
            rec[f"{m}_mean"] = float(np.mean(deltas)) if deltas else float("nan")
            if m in BOUNDED:
                lo, hi = percentile_bootstrap_ci(deltas, rng)
            else:
                _, lo, hi = mean_ci(deltas)
            rec[f"{m}_ci95_lo"] = lo
            rec[f"{m}_ci95_hi"] = hi
        paired_rows.append(rec)
    # all-cell paired
    rec = {"pair": "B4-B4-NoFreshness", "outage": "all"}
    cells = [k for k in by_cell if "B4" in by_cell[k] and "B4-NoFreshness" in by_cell[k]]
    rec["n"] = len(cells)
    all_deltas = {}
    for m in metrics:
        deltas = []
        for k in cells:
            va, vb = _f(by_cell[k]["B4"][m]), _f(by_cell[k]["B4-NoFreshness"][m])
            if va is not None and vb is not None:
                deltas.append(va - vb)
        all_deltas[m] = deltas
        rec[f"{m}_mean"] = float(np.mean(deltas)) if deltas else float("nan")
        if m in BOUNDED:
            lo, hi = percentile_bootstrap_ci(deltas, rng)
        else:
            _, lo, hi = mean_ci(deltas)
        rec[f"{m}_ci95_lo"] = lo
        rec[f"{m}_ci95_hi"] = hi
    paired_rows.append(rec)
    with (paper / "freshness_ablation_paired_effects.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(paired_rows[0].keys()))
        w.writeheader()
        w.writerows(paired_rows)

    diffs = []
    reassign_detail = []
    t220 = []
    for k, bm in sorted(by_cell.items()):
        if "B4" not in bm or "B4-NoFreshness" not in bm:
            continue
        a, b = bm["B4"], bm["B4-NoFreshness"]
        la = {(e["time_s"], e["auv"], e["action"]): e for e in a["consequential_decision_log"]}
        lb = {(e["time_s"], e["auv"], e["action"]): e for e in b["consequential_decision_log"]}
        keys = set(la) | set(lb)
        for key in sorted(keys):
            ea, eb = la.get(key), lb.get(key)
            if ea is None or eb is None:
                if (ea or eb or {}).get("action") == "reassign_another_auv":
                    ev = ea or eb
                    reassign_detail.append(
                        {
                            "environment_id": k[0],
                            "seed": k[1],
                            "outage": k[2],
                            "trace_id": a["trace_id"],
                            "match": "unmatched_proposal",
                            **{x: ev.get(x) for x in ("time_s", "auv", "action", "target_auv", "segment_id")},
                            "B4_decision": None if ea is None else ea.get("decision"),
                            "B4_NoFreshness_decision": None if eb is None else eb.get("decision"),
                        }
                    )
                continue
            reassign_detail.append(
                {
                    "environment_id": k[0],
                    "seed": k[1],
                    "outage": k[2],
                    "trace_id": a["trace_id"],
                    "match": "matched",
                    "time_s": ea["time_s"],
                    "auv": ea["auv"],
                    "action": ea["action"],
                    "target_auv": ea.get("target_auv"),
                    "segment_id": ea.get("segment_id"),
                    "B4_decision": ea["decision"],
                    "B4_reason": ea["reason_code"],
                    "B4_NoFreshness_decision": eb["decision"],
                    "B4_NoFreshness_reason": eb["reason_code"],
                    "B4_freshness": ea.get("freshness"),
                    "NF_freshness": eb.get("freshness"),
                    "mission_beneficial": ea.get("mission_beneficial"),
                    "violates_frozen_risk": ea.get("violates_frozen_risk"),
                }
            )
            if ea["decision"] != eb["decision"] or ea["reason_code"] != eb["reason_code"]:
                diffs.append(
                    {
                        "run_id": f"{k[0]}_{k[1]}_{k[2]}",
                        "environment": k[0],
                        "seed": k[1],
                        "outage": k[2],
                        "trace_id": a["trace_id"],
                        "sim_time": ea["time_s"],
                        "source_auv": ea["auv"],
                        "target_auv": ea.get("target_auv"),
                        "segment": ea.get("segment_id"),
                        "action_type": ea["action"],
                        "B4_decision": ea["decision"],
                        "B4_reason": ea["reason_code"],
                        "B4_NoFreshness_decision": eb["decision"],
                        "B4_NoFreshness_reason": eb["reason_code"],
                        "mission_beneficial": ea.get("mission_beneficial"),
                        "violates_frozen_risk": ea.get("violates_frozen_risk"),
                        "execution_duplicate_work": eb.get("duplicate_work_delta"),
                        "execution_useful_reassignment": eb.get("useful_reassignment_delta"),
                        "execution_missed_mandatory_delta": eb.get("missed_mandatory_delta"),
                        "execution_anomaly_resolved_delta": eb.get("anomaly_resolved_delta"),
                        "B4_freshness": ea.get("freshness"),
                        "rationale": ea.get("rationale"),
                    }
                )
            if (
                ea.get("action") == "reassign_another_auv"
                and float(ea.get("time_s") or -1) == 220.0
                and k[0] == "paper_ssp_200m_strong_v1"
            ):
                t220.append(
                    {
                        "seed": k[1],
                        "outage": k[2],
                        "auv": ea["auv"],
                        "B4_decision": ea["decision"],
                        "B4_reason": ea["reason_code"],
                        "B4_freshness": ea.get("freshness"),
                        "NF_decision": eb["decision"],
                        "NF_reason": eb["reason_code"],
                        "useful_delta": eb.get("useful_reassignment_delta"),
                        "dup_delta": eb.get("duplicate_work_delta"),
                    }
                )

    # contradictory flag on diffs: same-tick multi-allow of same segment in NF log
    nf_allows = defaultdict(list)
    for k, bm in by_cell.items():
        if "B4-NoFreshness" not in bm:
            continue
        for e in bm["B4-NoFreshness"]["consequential_decision_log"]:
            if e.get("action") == "reassign_another_auv" and e.get("decision") == "ALLOW":
                nf_allows[(k, e.get("time_s"), e.get("segment_id"))].append(e.get("auv"))
    for d in diffs:
        key = ((d["environment"], d["seed"], d["outage"]), d["sim_time"], d["segment"])
        proposers = nf_allows.get(key, [])
        d["execution_created_contradictory_reassignment"] = len(set(proposers)) > 1
        d["utility_component_changed"] = any(
            (d.get(x) or 0) != 0
            for x in (
                "execution_duplicate_work",
                "execution_useful_reassignment",
                "execution_missed_mandatory_delta",
                "execution_anomaly_resolved_delta",
            )
        )

    with (paper / "freshness_ablation_decision_diffs.csv").open("w", newline="") as f:
        if diffs:
            w = csv.DictWriter(f, fieldnames=list(diffs[0].keys()))
            w.writeheader()
            w.writerows(diffs)
        else:
            f.write("note,no_decision_differences\n")
    with (paper / "freshness_ablation_reassign_detail.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(reassign_detail[0].keys()) if reassign_detail else ["note"])
        if reassign_detail:
            w.writeheader()
            w.writerows(reassign_detail)
        else:
            f.write("note,none\n")

    # CASE
    d_sur = rec["safe_useful_retention_mean"]
    d_con = rec["contradictory_reassignment_mean"]
    d_dup = rec["duplicate_work_count_mean"]
    d_hs = rec["hard_safety_violation_rate_mean"]
    # B4 - NF: negative SUR means NF higher SUR
    nf_sur_up = d_sur < -1e-6
    nf_con_up = d_con < -1e-6  # NF has more conflicts
    nf_dup_up = d_dup < -1e-6
    if nf_sur_up and (nf_con_up or nf_dup_up):
        case = "A"
        interp = "NoFreshness improves SUR but increases conflicts / duplicate work."
    elif nf_sur_up and not nf_con_up and not nf_dup_up:
        case = "B"
        interp = "NoFreshness improves SUR and does not increase conflicts."
    elif abs(d_sur) <= 1e-6 and abs(d_con) <= 1e-6 and abs(d_dup) <= 1e-6:
        case = "C"
        interp = "NoFreshness does not materially improve SUR and does not change conflicts."
    else:
        case = "D"
        interp = "Mixed/non-monotonic freshness effect; state narrowly."

    n220 = len(t220)
    n220_nf_allow = sum(1 for x in t220 if x["NF_decision"] == "ALLOW")
    n220_b4_deny = sum(1 for x in t220 if x["B4_decision"] == "DENY")
    n220_contra = sum(
        1
        for d in diffs
        if d["action_type"] == "reassign_another_auv"
        and float(d["sim_time"]) == 220.0
        and d["environment"] == "paper_ssp_200m_strong_v1"
        and d.get("execution_created_contradictory_reassignment")
    )
    n220_useful = sum(1 for x in t220 if (x.get("useful_delta") or 0) > 0)
    n220_dup = sum(1 for x in t220 if (x.get("dup_delta") or 0) > 0)

    md = f"""# Freshness ablation case (`{ABLATION_ID}`)

git_commit: `{commit}`
n_runs: {len(rows)} (expect 120)
traces: `{json.dumps(traces)}`

## CASE {case}

{interp}

Paired B4 − B4-NoFreshness (n={rec['n']} cells):
- SUR: {rec['safe_useful_retention_mean']} [{rec['safe_useful_retention_ci95_lo']}, {rec['safe_useful_retention_ci95_hi']}]
- hard-safety: {rec['hard_safety_violation_rate_mean']} [{rec['hard_safety_violation_rate_ci95_lo']}, {rec['hard_safety_violation_rate_ci95_hi']}]
- contradictory_reassignment: {rec['contradictory_reassignment_mean']} [{rec['contradictory_reassignment_ci95_lo']}, {rec['contradictory_reassignment_ci95_hi']}]
- duplicate_work: {rec['duplicate_work_count_mean']} [{rec['duplicate_work_count_ci95_lo']}, {rec['duplicate_work_count_ci95_hi']}]
- utility: {rec['mission_utility_mean']} [{rec['mission_utility_ci95_lo']}, {rec['mission_utility_ci95_hi']}]

Decision differences: {len(diffs)}
t=220 strong-env matched reassigns in this grid (seeds 0–4, not seed 8): n={n220}; B4 DENY={n220_b4_deny}; NF ALLOW={n220_nf_allow}; NF useful_delta>0={n220_useful}; NF dup_delta>0={n220_dup}; same-tick multi-allow on segment={n220_contra}

Seed 8 from the production 48 is **outside** this predeclared 5-seed grid and was not added.

Do not retune 180/400/900 from these numbers.
"""
    (paper / "freshness_ablation_case.md").write_text(md)
    (paper / "t220_strong_check.json").write_text(json.dumps(t220, indent=2))


if __name__ == "__main__":
    main()
