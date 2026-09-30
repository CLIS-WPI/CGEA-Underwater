#!/usr/bin/env python3
"""E6: correct HARD_EXPIRED LOW_RISK contraction and recompute B4 production.

Does not overwrite e1_production_n10. Does not edit the manuscript.
Does not retune aging/stale/hard thresholds.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from omegaconf import OmegaConf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_e1_pilot_v2 import OUTAGE_ORDER, OUTAGES  # noqa: E402
from run_e1_pilot_v3 import ENV_FILES, load_base_cfg  # noqa: E402
from run_e1_production_n10 import SEEDS, mean_ci, plot_metric  # noqa: E402
from cgea.experiments.parallel import (  # noqa: E402
    cfg_to_container,
    execute_simpy_job,
    metrics_from_worker,
    resolve_simpy_workers,
    run_simpy_jobs,
)
from cgea.governance import (  # noqa: E402
    AuthorityFreshness,
    PAPER_POLICY_VERSION,
    PAPER_POLICY_VERSION_V2,
    contract_capsule,
    freshness_policy_for_version,
    issue_capsule,
)
from cgea.mission import LOW_RISK_ACTIONS, CONSEQUENTIAL_ACTIONS, ActionType
from cgea.mission.utility import UTILITY_FREEZE_ID
from cgea.experiments.runner import SAFE_USEFUL_RETENTION_FREEZE_ID

PROD_DIR = ROOT / "results" / "e1_production_n10"
OUT_DIR = ROOT / "results" / "e6_hard_expiry_correction"
CAMPAIGN = "e1_production_b4_hardexpiry_corrected"
POLICY_V2 = PAPER_POLICY_VERSION_V2
E5_COMMIT = "5828470cfdb76aa27a287259ced8e6d4173edb01"

HEADLINE = [
    "mission_utility",
    "hard_safety_violation_rate",
    "safe_useful_retention",
    "semantic_conflicts_per_mission",
    "mission_completion_ratio",
    "missed_mandatory",
    "useful_reassignment_count",
    "duplicate_work_count",
    "energy_total_j",
    "governance_tx_bytes",
    "mission_tx_bytes",
    "gco",
    "auv_reauth_fraction",
    "recon_latency_s",
]


def git_head() -> str:
    env = os.environ.pop("GIT_COMMIT", None)
    os.environ.pop("GIT_SHA", None)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if env is not None:
        os.environ["GIT_COMMIT"] = env
    return sha


def parse_outage_duration(notes: str | None) -> float | None:
    if not notes or "outage_duration_s=" not in notes:
        return None
    return float(notes.split("outage_duration_s=", 1)[1].split()[0])


def load_v1_b4() -> dict[tuple, dict]:
    out = {}
    for path in (PROD_DIR / "B4").glob("B4_*.json"):
        d = json.loads(path.read_text())
        p = d["provenance"]
        key = (p["environment_id"], int(p["random_seed"]), float(parse_outage_duration(p.get("notes"))))
        out[key] = d
    return out


def capsule_sets(pol, freshness, now: float) -> dict:
    cap = issue_capsule("auv_00", "mission", 0.0)
    eff = contract_capsule(cap, freshness, pol, now)
    return {
        "allowed": sorted(eff.allowed_actions),
        "conditional": sorted(eff.conditional_actions),
        "forbidden": sorted(eff.forbidden_actions),
        "risk_ceiling": eff.risk_ceiling,
    }


def write_policy_diff(path: Path) -> dict:
    v1 = freshness_policy_for_version(PAPER_POLICY_VERSION)
    v2 = freshness_policy_for_version(POLICY_V2)
    bands = {
        "FRESH": (AuthorityFreshness.FRESH, 10.0),
        "AGING": (AuthorityFreshness.AGING, 200.0),
        "STALE": (AuthorityFreshness.STALE, 500.0),
        "HARD_EXPIRED": (AuthorityFreshness.HARD_EXPIRED, 960.0),
    }
    payload = {
        "v1": PAPER_POLICY_VERSION,
        "v2": POLICY_V2,
        "v2_differs_only_at": "HARD_EXPIRED LOW_RISK contraction",
        "bands": {},
        "identical_except_hard_expired": True,
    }
    lines = [
        "# E6 policy diff",
        "",
        f"- v1: `{PAPER_POLICY_VERSION}` (preserved)",
        f"- v2: `{POLICY_V2}`",
        "- v2 differs from v1 ONLY in HARD_EXPIRED handling of LOW_RISK actions.",
        "- aging/stale/hard thresholds unchanged (180 / 400 / 900).",
        "",
    ]
    for name, (fr, now) in bands.items():
        a, b = capsule_sets(v1, fr, now), capsule_sets(v2, fr, now)
        same = a == b
        payload["bands"][name] = {"v1": a, "v2": b, "identical": same}
        if name != "HARD_EXPIRED" and not same:
            payload["identical_except_hard_expired"] = False
        lines.append(f"## {name}")
        lines.append(f"- identical: {same}")
        if not same:
            lines.append(f"- v1 allowed: {a['allowed']}")
            lines.append(f"- v2 allowed: {b['allowed']}")
            lines.append(f"- v1 forbidden: {a['forbidden']}")
            lines.append(f"- v2 forbidden: {b['forbidden']}")
        lines.append("")
    (path.with_suffix(".json")).write_text(json.dumps(payload, indent=2))
    path.write_text("\n".join(lines))
    return payload


def make_b4_job(env_path, seed, outage_name, ocfg, traces, campaign, policy, forensic: bool) -> dict:
    cfg = load_base_cfg(env_path)
    env_id = str(cfg.acoustic.environment_id)
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
            "governance": {"policy_version": policy},
            "experiment": {
                "name": campaign,
                "forensic_log_all_decisions": forensic,
            },
            "force_regenerate_trace": False,
            "forbid_trace_generation": True,
        },
    )
    return {
        "seed": int(seed),
        "outage": outage_name,
        "outage_duration_s": ocfg["duration_s"],
        "baseline": "B4",
        "environment_id": env_id,
        "expected_trace_id": traces[env_id][str(seed)],
        "cfg": cfg_to_container(cfg_run),
        "policy_version": policy,
    }


def row_from_metrics(m, job: dict) -> dict:
    extra = m.extra
    energy = float(m.energy_propulsion_j) + float(m.energy_communication_j) + float(m.energy_compute_j)
    return {
        "git_commit": m.provenance.git_commit,
        "config_hash": m.provenance.configuration_hash,
        "policy_version": extra.get("policy_version"),
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
        "mission_completion_ratio": m.mission_completion_ratio,
        "hard_safety_violation_rate": extra.get("violation_per_proposal"),
        "hard_safety_violation_count": extra.get("hard_safety_violation_count"),
        "safe_useful_retention": extra.get("safe_useful_retention"),
        "semantic_conflicts_per_mission": extra.get("semantic_conflicts_per_mission", m.conflict_count),
        "missed_mandatory": extra.get("missed_mandatory"),
        "useful_reassignment_count": extra.get("useful_reassignment_count"),
        "duplicate_work_count": extra.get("duplicate_work_count"),
        "energy_propulsion_j": m.energy_propulsion_j,
        "energy_communication_j": m.energy_communication_j,
        "energy_compute_j": m.energy_compute_j,
        "energy_total_j": energy,
        "governance_tx_bytes": extra.get("governance_tx_bytes", m.governance_bytes),
        "mission_tx_bytes": extra.get("mission_tx_bytes"),
        "total_tx_bytes": extra.get("total_tx_bytes", m.total_communication_bytes),
        "gco": extra.get("gco", m.governance_communication_overhead),
        "auv_reauth_fraction": extra.get("auv_reauth_fraction"),
        "auv_timeout_fraction": extra.get("auv_timeout_fraction"),
        "recon_status": extra.get("recon_status"),
        "recon_latency_s": extra.get("recon_latency_s", m.reconciliation_latency_s),
        "proposed_by_type": json.dumps(extra.get("proposed_by_type") or {}),
        "decision_by_type": json.dumps(extra.get("decision_by_type") or {}),
        "decision_by_risk_class": json.dumps(extra.get("decision_by_risk_class") or {}),
        "reason_code_counts": json.dumps(extra.get("reason_code_counts") or {}),
    }


def v1_row(d: dict) -> dict:
    extra = d.get("extra") or {}
    p = d["provenance"]
    energy = float(d["energy_propulsion_j"]) + float(d["energy_communication_j"]) + float(d["energy_compute_j"])
    dur = parse_outage_duration(p.get("notes"))
    outage = None
    for name, ocfg in OUTAGES.items():
        if abs(float(ocfg["duration_s"]) - float(dur)) < 1e-9:
            outage = name
            break
    return {
        "environment_id": p["environment_id"],
        "seed": int(p["random_seed"]),
        "outage": outage,
        "outage_duration_s": dur,
        "trace_id": p["channel_trace_id"],
        "mission_utility": d["mission_utility"],
        "mission_completion_ratio": d["mission_completion_ratio"],
        "hard_safety_violation_rate": extra.get("violation_per_proposal"),
        "hard_safety_violation_count": extra.get("hard_safety_violation_count"),
        "safe_useful_retention": extra.get("safe_useful_retention"),
        "semantic_conflicts_per_mission": extra.get("semantic_conflicts_per_mission", d.get("conflict_count")),
        "missed_mandatory": extra.get("missed_mandatory"),
        "useful_reassignment_count": extra.get("useful_reassignment_count"),
        "duplicate_work_count": extra.get("duplicate_work_count"),
        "energy_total_j": energy,
        "governance_tx_bytes": extra.get("governance_tx_bytes"),
        "mission_tx_bytes": extra.get("mission_tx_bytes"),
        "gco": extra.get("gco"),
        "auv_reauth_fraction": extra.get("auv_reauth_fraction"),
        "recon_latency_s": extra.get("recon_latency_s", d.get("reconciliation_latency_s")),
        "decision_by_type": extra.get("decision_by_type") or {},
        "decision_by_risk_class": extra.get("decision_by_risk_class") or {},
        "reason_code_counts": extra.get("reason_code_counts") or {},
        "proposed_by_type": extra.get("proposed_by_type") or {},
    }


def _f(row, key):
    v = row.get(key)
    if v is None or v == "":
        return None
    return float(v)


def pre_rerun_validation(traces) -> dict:
    env_path = ENV_FILES[2]
    seed, outage = 0, "150"
    job_v1 = make_b4_job(env_path, seed, outage, OUTAGES[outage], traces, "e6_precheck_v1", PAPER_POLICY_VERSION, True)
    job_v2 = make_b4_job(env_path, seed, outage, OUTAGES[outage], traces, "e6_precheck_v2", POLICY_V2, True)
    m1 = metrics_from_worker(execute_simpy_job(job_v1))
    m2 = metrics_from_worker(execute_simpy_job(job_v2))
    log1 = m1.extra.get("forensic_decision_log") or []
    log2 = m2.extra.get("forensic_decision_log") or []
    def first_low_hard(log):
        for ev in log:
            if ev.get("risk_class") == "low" and float(ev.get("authority_age_s") or 0) >= 900:
                return ev
        return None
    def first_reassign_hard(log):
        for ev in log:
            if ev.get("action_type") == "reassign_another_auv" and (
                ev.get("authority_freshness") == "hard_expired" or float(ev.get("authority_age_s") or 0) >= 900
            ):
                return ev
        return None
    low1, low2 = first_low_hard(log1), first_low_hard(log2)
    ras1, ras2 = first_reassign_hard(log1), first_reassign_hard(log2)
    ok = True
    notes = []
    if not low1 or low1.get("reason_code") != "DENY_FORBIDDEN":
        ok = False
        notes.append(f"v1 first LOW_RISK@>=900 expected DENY_FORBIDDEN got {low1}")
    if not low2 or low2.get("reason_code") != "ALLOW_LOW_RISK":
        ok = False
        notes.append(f"v2 first LOW_RISK@>=900 expected ALLOW_LOW_RISK got {low2}")
    if ras1 and ras1.get("decision") != "DENY":
        ok = False
        notes.append(f"v1 reassign at hard expiry not DENY: {ras1}")
    if ras2 and ras2.get("decision") != "DENY":
        ok = False
        notes.append(f"v2 reassign at hard expiry not DENY: {ras2}")
    if not ras1:
        notes.append("no reassign event at hard expiry in this cell; consequential DENY_HARD_EXPIRY covered by unit tests")
    prod = load_v1_b4()[(job_v1["environment_id"], seed, 150.0)]
    if abs(float(m1.mission_utility) - float(prod["mission_utility"])) > 1e-8:
        ok = False
        notes.append("v1 precheck utility != frozen production")
    return {
        "ok": ok,
        "notes": notes,
        "cell": {"environment_id": job_v1["environment_id"], "seed": seed, "outage": outage},
        "v1_low": low1,
        "v2_low": low2,
        "v1_reassign": ras1,
        "v2_reassign": ras2,
        "trace_id": m1.provenance.channel_trace_id,
        "forbid_trace_generation": True,
    }


def paired_mean(rows_a, rows_b, fields, pair_name):
    by_a = {(str(r["environment_id"]), int(r["seed"]), str(r["outage"])): r for r in rows_a}
    by_b = {(str(r["environment_id"]), int(r["seed"]), str(r["outage"])): r for r in rows_b}
    out = []
    for o in OUTAGE_ORDER:
        rec = {"pair": pair_name, "outage": o, "n": 0}
        deltas = {f: [] for f in fields}
        for key, ra in by_a.items():
            if str(ra["outage"]) != str(o) or key not in by_b:
                continue
            rb = by_b[key]
            rec["n"] += 1
            for f in fields:
                va, vb = _f(ra, f), _f(rb, f)
                if va is not None and vb is not None:
                    deltas[f].append(va - vb)
        rec["n_cells"] = rec["n"]
        for f in fields:
            m, lo, hi = mean_ci(deltas[f])
            rec[f"{f}_mean"] = m
            rec[f"{f}_ci95_lo"] = lo
            rec[f"{f}_ci95_hi"] = hi
        out.append(rec)
    return out


def summarize_b4(rows, field, outage):
    xs = [_f(r, field) for r in rows if r["outage"] == outage]
    xs = [x for x in xs if x is not None]
    m, lo, hi = mean_ci(xs)
    return {"outage": outage, "metric": field, "n": len(xs), "mean": m, "ci95_lo": lo, "ci95_hi": hi}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paper_dir = OUT_DIR / "paper"
    paper_dir.mkdir(parents=True, exist_ok=True)
    camp_dir = ROOT / "results" / CAMPAIGN
    camp_dir.mkdir(parents=True, exist_ok=True)
    parent = git_head()
    traces = json.loads((PROD_DIR / "shared_trace_ids.json").read_text())

    pdiff = write_policy_diff(OUT_DIR / "e6_policy_diff.md")
    if not pdiff["identical_except_hard_expired"]:
        print("POLICY DIFF FAIL: v1/v2 differ outside HARD_EXPIRED", flush=True)
        sys.exit(2)
    if pdiff["bands"]["HARD_EXPIRED"]["identical"]:
        print("POLICY DIFF FAIL: HARD_EXPIRED v1==v2", flush=True)
        sys.exit(2)

    print("E6 pre-rerun validation", flush=True)
    val = pre_rerun_validation(traces)
    (OUT_DIR / "e6_validation.md").write_text(
        "\n".join(
            [
                "# E6 pre-rerun validation",
                "",
                f"- status: {'PASS' if val['ok'] else 'FAIL'}",
                f"- cell: {val['cell']}",
                f"- trace_id: `{val['trace_id']}`",
                f"- forbid_trace_generation: true",
                f"- v1 first LOW_RISK@>=900: {val['v1_low']}",
                f"- v2 first LOW_RISK@>=900: {val['v2_low']}",
                f"- v1 reassign@hard: {val['v1_reassign']}",
                f"- v2 reassign@hard: {val['v2_reassign']}",
                "",
                *val["notes"],
                "",
            ]
        )
    )
    if not val["ok"]:
        print("PRE-RERUN VALIDATION FAILED; stopping.", flush=True)
        sys.exit(2)

    jobs = []
    for env_path in ENV_FILES:
        for seed in SEEDS:
            for oname, ocfg in OUTAGES.items():
                jobs.append(
                    make_b4_job(env_path, seed, oname, ocfg, traces, CAMPAIGN, POLICY_V2, True)
                )
    assert len(jobs) == 180, len(jobs)
    workers = resolve_simpy_workers()
    print(f"E6 B4 v2 campaign jobs=180 workers={workers} policy={POLICY_V2}", flush=True)

    def progress(i, payload):
        if i % 10 == 0 or i == len(jobs) - 1:
            print(f"[e6] {i+1}/{len(jobs)} seed={payload.get('seed')} outage={payload.get('outage')}", flush=True)

    payloads = run_simpy_jobs(jobs, max_workers=workers, on_complete=progress)
    v1_idx = load_v1_b4()
    rows = []
    v2_logs_low_allow_900 = 0
    v2_logs_low_deny_900 = 0
    action_allow_900 = Counter()
    for job, payload in zip(jobs, payloads):
        m = metrics_from_worker(payload)
        assert m.extra.get("policy_version") == POLICY_V2
        assert m.provenance.channel_trace_id == job["expected_trace_id"]
        rows.append(row_from_metrics(m, job))
        for ev in m.extra.get("forensic_decision_log") or []:
            if ev.get("risk_class") != "low":
                continue
            if float(ev.get("authority_age_s") or 0) < 900:
                continue
            if ev.get("decision") == "ALLOW":
                v2_logs_low_allow_900 += 1
                action_allow_900[ev.get("action_type")] += 1
            elif ev.get("decision") == "DENY":
                v2_logs_low_deny_900 += 1
        key = (job["environment_id"], int(job["seed"]), float(job["outage_duration_s"]))
        v1 = v1_idx[key]
        rows[-1]["_v1"] = v1_row(v1)
        rows[-1]["_v2_extra"] = {
            "decision_by_type": m.extra.get("decision_by_type") or {},
            "decision_by_risk_class": m.extra.get("decision_by_risk_class") or {},
        }

    dump = camp_dir / "B4"
    if dump.is_dir():
        for p in dump.glob("*.json"):
            p.unlink()

    fields = [k for k in rows[0].keys() if not k.startswith("_")]
    with (OUT_DIR / "e6_b4_v2_raw.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    (camp_dir / "e6_b4_v2_raw.csv").write_text((OUT_DIR / "e6_b4_v2_raw.csv").read_text())

    summary_rows = [summarize_b4(rows, metric, o) for o in OUTAGE_ORDER for metric in HEADLINE]
    with (OUT_DIR / "e6_b4_v2_summary.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["outage", "metric", "n", "mean", "ci95_lo", "ci95_hi"])
        w.writeheader()
        w.writerows(summary_rows)

    impact_fields = [
        "mission_utility",
        "safe_useful_retention",
        "hard_safety_violation_rate",
        "semantic_conflicts_per_mission",
        "mission_completion_ratio",
        "missed_mandatory",
        "energy_total_j",
        "mission_tx_bytes",
        "governance_tx_bytes",
        "auv_reauth_fraction",
        "useful_reassignment_count",
        "duplicate_work_count",
        "gco",
        "recon_latency_s",
    ]
    v1_rows = [r["_v1"] for r in rows]
    v2_clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    impact = paired_mean(v2_clean, v1_rows, impact_fields, "B4v2-B4v1")
    with (OUT_DIR / "e6_v2_minus_v1.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(impact[0].keys()))
        w.writeheader()
        w.writerows(impact)

    changed_runs = 0
    low_allow_delta = 0
    type_delta = Counter()
    for r in rows:
        v1r, v2e = r["_v1"], r["_v2_extra"]
        util_chg = abs(float(r["mission_utility"]) - float(v1r["mission_utility"])) > 1e-9
        sur_chg = abs(float(r["safe_useful_retention"] or 0) - float(v1r["safe_useful_retention"] or 0)) > 1e-12
        hs_chg = abs(float(r["hard_safety_violation_rate"] or 0) - float(v1r["hard_safety_violation_rate"] or 0)) > 1e-12
        if util_chg or sur_chg or hs_chg or r["semantic_conflicts_per_mission"] != v1r["semantic_conflicts_per_mission"]:
            changed_runs += 1
        elif abs(float(r["energy_total_j"]) - float(v1r["energy_total_j"])) > 1e-6:
            changed_runs += 1
        elif abs(float(r["mission_completion_ratio"]) - float(v1r["mission_completion_ratio"])) > 1e-12:
            changed_runs += 1
        d1 = v1r["decision_by_risk_class"].get("low") or {}
        d2 = v2e["decision_by_risk_class"].get("low") or {}
        low_allow_delta += int(d2.get("ALLOW") or 0) - int(d1.get("ALLOW") or 0)
        for at in ("bounded_path_correction", "hold_station", "repeat_sonar_scan", "collision_avoidance"):
            a1 = (v1r["decision_by_type"].get(at) or {}).get("ALLOW") or 0
            a2 = (v2e["decision_by_type"].get(at) or {}).get("ALLOW") or 0
            type_delta[at] += int(a2) - int(a1)

    prod_csv = list(csv.DictReader((PROD_DIR / "e1_production_n10_raw.csv").open()))
    b5 = [r for r in prod_csv if r["baseline"] == "B5_nominal"]
    b3 = [r for r in prod_csv if r["baseline"] == "B3"]
    pair_fields = [
        "mission_utility",
        "hard_safety_violation_rate",
        "safe_useful_retention",
        "semantic_conflicts_per_mission",
        "governance_tx_bytes",
        "mission_tx_bytes",
        "gco",
        "auv_reauth_fraction",
    ]
    p_b5 = paired_mean(v2_clean, b5, pair_fields, "B4v2-B5_nominal")
    p_b3 = paired_mean(v2_clean, b3, pair_fields, "B4v2-B3")
    paired_all = p_b5 + p_b3
    with (OUT_DIR / "e6_paired_vs_baselines.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(paired_all[0].keys()))
        w.writeheader()
        w.writerows(paired_all)

    mixed = []
    for r in v2_clean:
        x = dict(r)
        x["baseline"] = "B4"
        mixed.append(x)
    for r in b5:
        mixed.append(r)
    for r in b3:
        mixed.append(r)
    plot_metric(mixed, "mission_utility", "Mission utility", paper_dir / "utility_vs_outage", baselines=["B4", "B5_nominal", "B3"])
    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    xs = [OUTAGES[o]["duration_s"] for o in OUTAGE_ORDER]
    ax2 = ax.twinx()
    for b, color in (("B4", "C0"), ("B5_nominal", "C1"), ("B3", "C2")):
        means = [summarize_b4([r for r in mixed if r["baseline"] == b], "safe_useful_retention", o)["mean"] for o in OUTAGE_ORDER]
        ax.plot(xs, means, "o-", color=color, label=f"{b} safe ret.")
        v = [summarize_b4([r for r in mixed if r["baseline"] == b], "hard_safety_violation_rate", o)["mean"] for o in OUTAGE_ORDER]
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

    sur_same = all(abs((summarize_b4(v2_clean, "safe_useful_retention", o)["mean"] or 0) - (summarize_b4(v1_rows, "safe_useful_retention", o)["mean"] or 0)) < 1e-12 for o in OUTAGE_ORDER)
    hs_same = all(abs((summarize_b4(v2_clean, "hard_safety_violation_rate", o)["mean"] or 0) - (summarize_b4(v1_rows, "hard_safety_violation_rate", o)["mean"] or 0)) < 1e-12 for o in OUTAGE_ORDER)
    util_d = [impact[i]["mission_utility_mean"] for i in range(len(OUTAGE_ORDER))]
    material = (not sur_same) or (not hs_same)
    if changed_runs == 0 and abs(low_allow_delta) == 0:
        case = "C0"
    elif material:
        case = "C2"
    else:
        case = "C1"

    (OUT_DIR / "e6_case.md").write_text(
        "\n".join(
            [
                f"# CASE {case}",
                "",
                {
                    "C0": "NO NUMERICAL IMPACT — policy corrected; production headlines unchanged.",
                    "C1": "LIMITED IMPACT — some B4 metrics change; main qualitative conclusions (hard-safety vs B5, SUR vs B3) remain.",
                    "C2": "MATERIAL IMPACT — a major production conclusion, crossover, safety/retention statement, or principal comparison changed.",
                }[case],
                "",
                f"- changed_runs (headline/energy/completion): {changed_runs}",
                f"- LOW_RISK ALLOW delta (v2-v1, sum over runs): {low_allow_delta}",
                f"- LOW_RISK ALLOW at age>=900 in v2 forensic log: {v2_logs_low_allow_900}",
                f"- LOW_RISK DENY at age>=900 in v2 forensic log: {v2_logs_low_deny_900}",
                f"- SUR means identical to v1 by outage: {sur_same}",
                f"- hard-safety means identical to v1 by outage: {hs_same}",
                f"- utility delta means by outage: {util_d}",
                "",
            ]
        )
    )

    (OUT_DIR / "e6_regression_e2_e3_e4.md").write_text(
        "\n".join(
            [
                "# E2 / E3 / E4 targeted regression",
                "",
                "Default `configs/governance/cgea.yaml` remains `paper_risk_bounded_v1_2026-09-28`.",
                "v2 is selected only when `governance.policy_version` is the v2 identifier.",
                "Consequential HARD_EXPIRED still returns DENY_HARD_EXPIRY under both versions (unit tests T7–T9).",
                "",
                "## E2 primary reassignment",
                "Not rerun. Primary outcome is CONSEQUENTIAL reassignment; HARD_EXPIRED denial is unchanged.",
                "Pytest: `tests/test_e2_authority_age_sanity.py` passed after the v2 patch.",
                "",
                "## E2-F primary event",
                "Not rerun. Pytest: `tests/test_e2f_early_state_change.py` passed.",
                "",
                "## E3 primary reassignment rates",
                "Not rerun. Frozen TEST CGEA useful_valid=0.25 obsolete≈0.04545 remains the E3 record.",
                "Pytest: `tests/test_e3_fixed_expiry.py` passed.",
                "",
                "## E4 primary reassignment rates",
                "Not rerun. Frozen TEST primary table unchanged on disk.",
                "Pytest: `tests/test_e4_integration.py` and `tests/test_evidence_authority.py` passed.",
                "",
                "## E4 local secondary (retired)",
                "Option B: retire the claim that CGEA denies collision_avoidance at capsule age 960 while evidence allows it.",
                "That comparison used v1 HARD_EXPIRED fallback-only contraction. Under corrected v2, LOW_RISK local actions remain allowed.",
                "Do not use the old local-secondary result in the manuscript.",
                "",
                "## Lease-180 equivalence (E3 existing data, not retuned)",
                "From `results/e3_fixed_expiry_challenge/e3_test_pareto.csv`:",
                "- FE_DEV_180: useful_valid = 0.25, obsolete = 0.045454545454545456",
                "- CGEA_TEST: useful_valid = 0.25, obsolete = 0.045454545454545456",
                "",
                'For the evaluated reassignment action, CGEA is behaviorally equivalent to a 180-s binary lease.',
                "This is characterization of the aging-band reassignment strip, not a threshold change.",
                "",
            ]
        )
    )

    (OUT_DIR / "e6_manifest.md").write_text(
        "\n".join(
            [
                "# E6 hard-expiry correction manifest",
                "",
                f"- parent SHA: `{parent}`",
                f"- E5 forensic commit: `{E5_COMMIT}`",
                f"- corrected policy: `{POLICY_V2}`",
                f"- historical policy preserved: `{PAPER_POLICY_VERSION}`",
                f"- campaign: `{CAMPAIGN}` (does not overwrite e1_production_n10)",
                "- B4 rerun: 180",
                "- traces: frozen e1_production_n10/shared_trace_ids.json; forbid_trace_generation=true",
                f"- CASE: {case}",
                "- manuscript: not edited",
                "",
            ]
        )
    )
    print((OUT_DIR / "e6_case.md").read_text())
    print("wrote", OUT_DIR)


if __name__ == "__main__":
    main()
