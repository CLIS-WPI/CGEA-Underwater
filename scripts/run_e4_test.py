#!/usr/bin/env python3
"""E4 held-out TEST. Frozen budgets. No retune. No TeX. Primary = reassignment only."""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from omegaconf import OmegaConf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_e1_pilot_v3 import ENV_FILES, load_base_cfg  # noqa: E402
from run_e2_authority_age_context import verify_traces  # noqa: E402
from cgea.experiments.e3_fixed_expiry import (  # noqa: E402
    BOOT_SEED,
    CONTEXTS,
    N_BOOT,
    PROPOSAL_AGES,
    TEST_SEEDS,
    challenge_time_s,
    cgea_expected_freshness,
    context_status,
)
from cgea.experiments.e4_test import (  # noqa: E402
    LOCAL_SLICE_AGE_S,
    LOCAL_SLICE_CHALLENGE_S,
    assign_region,
)
from cgea.experiments.parallel import cfg_to_container, metrics_from_worker, resolve_simpy_workers, run_simpy_jobs  # noqa: E402
from cgea.types import git_commit  # noqa: E402

CAMPAIGN = os.environ.get("CGEA_CAMPAIGN", "e4_test")
OUTAGE_START = 200.0
OUTAGE_END = 1200.0
DURATION_S = 1400.0
DEV_SEEDS_FORBIDDEN = [0, 1, 2, 3, 4]
METHODS = (
    ("B4-NoFreshness", None, "B0"),
    ("B4-FixedExpiry", 400.0, "B1"),
    ("B4", None, "B2"),
    ("B4-Evidence", None, "B3"),
)
OUT = ROOT / "results" / "e4_test"


def _num(v):
    if v is None or v == "":
        return float("nan")
    if isinstance(v, bool):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_cfg(acoustic_path: Path):
    cfg = load_base_cfg(acoustic_path)
    e4 = OmegaConf.load(ROOT / "configs" / "experiment" / "e4_test.yaml")
    cfg.experiment = OmegaConf.merge(cfg.experiment, e4)
    cfg.experiment.name = CAMPAIGN
    cfg.experiment.log_consequential_decisions = True
    cfg.scenario.outage_disabled = False
    cfg.scenario.outage_start_s = OUTAGE_START
    cfg.scenario.outage_end_s = OUTAGE_END
    cfg.force_regenerate_trace = False
    cfg.forbid_trace_generation = True
    return cfg


def _job(cfg, seed, env_id, tid, baseline, ttl, method_id, e2_update, extra_job):
    e2_base = OmegaConf.to_container(cfg.experiment.e2_authority_age_context, resolve=True)
    e2 = dict(e2_base)
    e2.update(e2_update)
    cfg_run = OmegaConf.merge(
        cfg,
        {
            "seed": int(seed),
            "experiment": {"name": CAMPAIGN, "log_consequential_decisions": True, "e2_authority_age_context": e2},
            "scenario": {
                "outage_disabled": False,
                "outage_start_s": OUTAGE_START,
                "outage_end_s": OUTAGE_END,
                "partition_groups": None,
            },
            "force_regenerate_trace": False,
            "forbid_trace_generation": True,
        },
    )
    job = {
        "seed": int(seed),
        "outage": "1000",
        "baseline": baseline,
        "environment_id": env_id,
        "expected_trace_id": tid,
        "method_id": method_id,
        "lease_ttl_s": ttl,
        "cfg": cfg_to_container(cfg_run),
    }
    job.update(extra_job)
    return job


def iter_primary_jobs(cfg, trace_ids, environment_id, seeds):
    jobs = []
    for seed in seeds:
        for ctx, rec_age in CONTEXTS.items():
            for age in PROPOSAL_AGES:
                if rec_age is not None and abs(float(age) - float(rec_age)) < 1e-9:
                    continue
                for baseline, ttl, method_id in METHODS:
                    jobs.append(
                        _job(
                            cfg,
                            seed,
                            environment_id,
                            trace_ids[int(seed)],
                            baseline,
                            ttl,
                            method_id,
                            {
                                "enabled": True,
                                "challenge_time_s": challenge_time_s(age),
                                "authority_age_s": age,
                                "expected_freshness": cgea_expected_freshness(age),
                                "context_mode": ctx,
                                "change_age_s": rec_age if rec_age is not None else 1e18,
                                "age_id": f"age_{int(age)}",
                                "lease_ttl_s": float(ttl) if ttl is not None else None,
                                "e4_cell": {
                                    "id": "PRIMARY",
                                    "peer_refresh_s": None,
                                    "controlled_action": "reassign_another_auv",
                                },
                            },
                            {
                                "slice": "primary_reassign",
                                "context_mode": ctx,
                                "authority_age_s": age,
                                "recovery_age_s": rec_age,
                            },
                        )
                    )
    return jobs


def iter_local_jobs(cfg, trace_ids, environment_id, seeds):
    jobs = []
    for seed in seeds:
        for baseline, ttl, method_id in METHODS:
            jobs.append(
                _job(
                    cfg,
                    seed,
                    environment_id,
                    trace_ids[int(seed)],
                    baseline,
                    ttl,
                    method_id,
                    {
                        "enabled": True,
                        "challenge_time_s": LOCAL_SLICE_CHALLENGE_S,
                        "authority_age_s": LOCAL_SLICE_AGE_S,
                        "expected_freshness": "hard_expired",
                        "context_mode": "benign_static",
                        "change_age_s": 1e18,
                        "age_id": "local_hard_960",
                        "lease_ttl_s": float(ttl) if ttl is not None else None,
                        "e4_cell": {
                            "id": "LOCAL_F",
                            "peer_refresh_s": None,
                            "controlled_action": "collision_avoidance",
                        },
                    },
                    {
                        "slice": "local_secondary",
                        "context_mode": "benign_static",
                        "authority_age_s": LOCAL_SLICE_AGE_S,
                        "recovery_age_s": None,
                    },
                )
            )
    return jobs


def evidence_ages(ev: dict, fallback_age: float) -> tuple[float, float]:
    ages = ev.get("evidence_ages") or {}
    if isinstance(ages, str):
        try:
            ages = json.loads(ages)
        except json.JSONDecodeError:
            ages = {}
    peer = next((float(v) for k, v in ages.items() if str(k).startswith("PEER_AVAILABILITY:")), None)
    seg = next((float(v) for k, v in ages.items() if str(k).startswith("SEGMENT_ASSIGNMENT:")), None)
    if peer is None:
        peer = float(fallback_age)
    if seg is None:
        seg = float(fallback_age)
    return peer, seg


def row_from(m, job: dict) -> dict:
    extra = m.extra
    ev = dict(extra.get("e2_controlled_event") or {})
    ctx = job["context_mode"]
    age = float(job["authority_age_s"])
    status = context_status(ctx, age) if job["slice"] == "primary_reassign" else "local"
    peer_age, seg_age = evidence_ages(ev, age)
    region = assign_region(peer_age, seg_age, status) if status in ("valid", "obsolete") else "LOCAL"
    return {
        "run_id": f"{job['environment_id']}|{job['seed']}|{job['slice']}|{ctx}|{int(age)}|{job['method_id']}",
        "environment_id": job["environment_id"],
        "seed": job["seed"],
        "trace_id": m.provenance.channel_trace_id,
        "expected_trace_id": job["expected_trace_id"],
        "slice": job["slice"],
        "context_mode": ctx,
        "authority_age_s": age,
        "recovery_age_s": job["recovery_age_s"],
        "cell_status": status,
        "region": region,
        "method_id": job["method_id"],
        "baseline": m.provenance.baseline,
        "decision": ev.get("decision"),
        "reason_code": ev.get("reason_code"),
        "controlled_action": ev.get("controlled_action"),
        "authority_mode": ev.get("authority_mode"),
        "peer_age_s": peer_age,
        "assignment_age_s": seg_age,
        "local_target_failed": ev.get("local_target_failed"),
        "oracle_global_target_failed": ev.get("oracle_global_target_failed", ev.get("global_target_failed")),
        "mission_beneficial": ev.get("mission_beneficial"),
        "violates_frozen_risk": ev.get("violates_frozen_risk"),
        "executed": ev.get("executed"),
        "controlled_safe_useful_execution": ev.get("controlled_safe_useful_execution"),
        "obsolete_reassignment_execution": ev.get("obsolete_reassignment_execution"),
        "ownership_override_of_available_target": ev.get("ownership_override_of_available_target"),
        "mission_utility": m.mission_utility,
        "hard_safety_violation_count": extra.get("hard_safety_violation_count"),
        "useful_reassignment_count": extra.get("useful_reassignment_count"),
        "duplicate_work_count": extra.get("duplicate_work_count"),
        "missed_mandatory": extra.get("missed_mandatory"),
        "semantic_conflicts_per_mission": extra.get("semantic_conflicts_per_mission", m.conflict_count),
        "contradictory_reassignment": extra.get("contradictory_reassignment"),
        "governance_bytes": extra.get("governance_tx_bytes", m.governance_bytes),
        "governance_airtime_s": extra.get("governance_airtime_s"),
        "mission_airtime_s": extra.get("mission_airtime_s"),
        "total_airtime_s": extra.get("total_airtime_s"),
        "governance_airtime_frac": extra.get("governance_airtime_frac"),
        "mission_airtime_frac": extra.get("mission_airtime_frac"),
        "total_airtime_frac": extra.get("total_airtime_frac"),
        "evidence_refresh_airtime_s": 0.0,
        "evidence_refresh_bytes": 0,
        "e2_n_controlled_proposals": extra.get("e2_n_controlled_proposals"),
        "git_commit": m.provenance.git_commit,
    }


def cluster_key(r):
    return (r["environment_id"], int(r["seed"]))


def primary(rows):
    return [r for r in rows if r["slice"] == "primary_reassign" and r["cell_status"] != "equal"]


def rates_for_method(rows, method_id: str) -> dict:
    sub = [r for r in primary(rows) if r["method_id"] == method_id]
    valid = [r for r in sub if r["cell_status"] == "valid"]
    obs = [r for r in sub if r["cell_status"] == "obsolete"]

    def mean_bin(rs, key):
        if not rs:
            return float("nan")
        return float(np.mean([_num(r.get(key)) for r in rs]))

    return {
        "method_id": method_id,
        "n_valid": len(valid),
        "n_obsolete": len(obs),
        "useful_valid_execution_rate": mean_bin(valid, "controlled_safe_useful_execution"),
        "obsolete_execution_rate": mean_bin(obs, "obsolete_reassignment_execution"),
        "ownership_override_rate": mean_bin(obs, "ownership_override_of_available_target"),
        "hard_safety_violation_rate": mean_bin(sub, "hard_safety_violation_count"),
        "mission_utility": mean_bin(sub, "mission_utility"),
        "useful_reassignment_count": mean_bin(sub, "useful_reassignment_count"),
        "missed_mandatory": mean_bin(sub, "missed_mandatory"),
        "contradictory_reassignment": mean_bin(sub, "contradictory_reassignment"),
        "duplicate_work_count": mean_bin(sub, "duplicate_work_count"),
        "semantic_conflicts": mean_bin(sub, "semantic_conflicts_per_mission"),
        "governance_bytes": mean_bin(sub, "governance_bytes"),
        "governance_airtime_s": mean_bin(sub, "governance_airtime_s"),
        "evidence_refresh_airtime_s": mean_bin(sub, "evidence_refresh_airtime_s"),
        "evidence_refresh_bytes": mean_bin(sub, "evidence_refresh_bytes"),
        "mission_airtime_s": mean_bin(sub, "mission_airtime_s"),
        "total_airtime_s": mean_bin(sub, "total_airtime_s"),
        "governance_airtime_frac": mean_bin(sub, "governance_airtime_frac"),
        "total_airtime_frac": mean_bin(sub, "total_airtime_frac"),
    }


def cluster_metric_means(rows, method_id, status, key):
    by = defaultdict(list)
    for r in primary(rows):
        if r["method_id"] != method_id or r["cell_status"] != status:
            continue
        by[cluster_key(r)].append(_num(r.get(key)))
    return [float(np.mean(by[ck])) for ck in sorted(by)]


def bootstrap_mean_ci(xs, rng):
    a = np.asarray(xs, dtype=float)
    a = a[~np.isnan(a)]
    if a.size == 0:
        return float("nan"), float("nan"), float("nan"), "empty"
    m = float(np.mean(a))
    if a.size == 1:
        return m, m, m, "n1"
    if float(np.max(a) - np.min(a)) < 1e-15:
        return m, m, m, "deterministic_identity"
    draws = rng.choice(a, size=(N_BOOT, a.size), replace=True).mean(axis=1)
    lo, hi = float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))
    return m, lo, hi, "bootstrap"


def region_rates(rows, method_id, region):
    sub = [r for r in primary(rows) if r["method_id"] == method_id and r["region"] == region]
    valid = [r for r in sub if r["cell_status"] == "valid"]
    obs = [r for r in sub if r["cell_status"] == "obsolete"]

    def mean_bin(rs, key):
        if not rs:
            return float("nan")
        return float(np.mean([_num(r.get(key)) for r in rs]))

    return {
        "region": region,
        "method_id": method_id,
        "n": len(sub),
        "n_valid": len(valid),
        "n_obsolete": len(obs),
        "useful_valid_execution_rate": mean_bin(valid, "controlled_safe_useful_execution"),
        "obsolete_execution_rate": mean_bin(obs, "obsolete_reassignment_execution"),
        "ownership_override_rate": mean_bin(obs, "ownership_override_of_available_target"),
    }


def _ge(a, b):
    return (not math.isnan(a)) and (not math.isnan(b)) and a + 1e-12 >= b


def _gt(a, b):
    return (not math.isnan(a)) and (not math.isnan(b)) and a > b + 1e-12


def _le(a, b):
    return (not math.isnan(a)) and (not math.isnan(b)) and a <= b + 1e-12


def _lt(a, b):
    return (not math.isnan(a)) and (not math.isnan(b)) and a < b - 1e-12


def frontier_vs_both(b3, other_a, other_b) -> str | None:
    u, o = b3["useful_valid_execution_rate"], b3["obsolete_execution_rate"]
    u1, o1 = other_a["useful_valid_execution_rate"], other_a["obsolete_execution_rate"]
    u2, o2 = other_b["useful_valid_execution_rate"], other_b["obsolete_execution_rate"]
    if _gt(u, u1) and _gt(u, u2) and _le(o, o1) and _le(o, o2):
        return "A"
    if _lt(o, o1) and _lt(o, o2) and _ge(u, u1) and _ge(u, u2):
        return "B"
    return None


def region_frontier(b3, b1, b2) -> str | None:
    u, o = b3["useful_valid_execution_rate"], b3["obsolete_execution_rate"]
    parts = []
    for other in (b1, b2):
        ou, oo = other["useful_valid_execution_rate"], other["obsolete_execution_rate"]
        if math.isnan(u) and math.isnan(o):
            return None
        if math.isnan(o) and not math.isnan(u):
            if not _gt(u, ou):
                return None
            parts.append("uv")
            continue
        if math.isnan(u) and not math.isnan(o):
            if not _lt(o, oo):
                return None
            parts.append("ob")
            continue
        if (_gt(u, ou) and _le(o, oo)) or (_lt(o, oo) and _ge(u, ou)):
            parts.append("pareto")
        else:
            return None
    return "C" if parts else None


def classify(b0, b1, b2, b3, region_rows) -> tuple[str, str, str]:
    if b3["hard_safety_violation_rate"] > max(b1["hard_safety_violation_rate"], b2["hard_safety_violation_rate"]) + 1e-12:
        return (
            "E4-FAIL",
            "Hard-safety worsened on held-out TEST.",
            "B3 is not a safer policy on the frozen hard-safety classes.",
        )
    crit = frontier_vs_both(b3, b1, b2)
    if crit:
        return (
            "E4-DOMINANT",
            f"On held-out TEST reassignment, B3 meets preregistered success criterion {crit} versus both B1 lease-400 and B2 CGEA.",
            "Fresh evidence is not truth; thresholds are not shown to be optimal; exclusive ownership is not solved.",
        )
    regional = False
    for reg in ("R1", "R2", "R3", "R4"):
        rb3 = next(x for x in region_rows if x["region"] == reg and x["method_id"] == "B3")
        rb1 = next(x for x in region_rows if x["region"] == reg and x["method_id"] == "B1")
        rb2 = next(x for x in region_rows if x["region"] == reg and x["method_id"] == "B2")
        if region_frontier(rb3, rb1, rb2):
            regional = True
            break
    if regional:
        return (
            "E4-REGIONAL",
            "B3 improves a predeclared context region on the reassignment metrics without dominating the overall TEST frontier versus both B1 and B2.",
            "Overall superiority of B3 on reassignment is not established.",
        )
    return (
        "E4-INTERIOR",
        "B3 occupies another useful-valid vs obsolete tradeoff point on held-out reassignment TEST and does not improve the primary frontier versus both B1 and B2.",
        "B3 is not shown to dominate lease-400 or CGEA on the preregistered primary criterion.",
    )


def make_pareto(points: dict[str, dict], pdf: Path, png: Path) -> None:
    fig, ax = plt.subplots(figsize=(5.2, 4.2))
    style = {
        "B0": ("#548235", "s", "B0 NoFreshness"),
        "B1": ("#c45911", "D", "B1 lease-400"),
        "B2": ("#1f4e79", "o", "B2 CGEA"),
        "B3": ("#7030a0", "^", "B3 Evidence"),
    }
    for mid, (c, m, lab) in style.items():
        pt = points[mid]
        ax.scatter([pt["obsolete_execution_rate"]], [pt["useful_valid_execution_rate"]], s=80, c=c, marker=m, label=lab, zorder=4)
    ax.set_xlabel("Obsolete reassignment execution rate")
    ax.set_ylabel("Useful-valid execution rate")
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.05, 1.05)
    ax.grid(True, alpha=0.3)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(pdf)
    fig.savefig(png, dpi=200)
    plt.close(fig)


def main() -> int:
    if any(s in DEV_SEEDS_FORBIDDEN for s in TEST_SEEDS):
        raise SystemExit("TEST seed list invalid")
    parent = git_commit(ROOT)
    OUT.mkdir(parents=True, exist_ok=True)
    paper = OUT / "paper"
    all_jobs = []
    for env_path in ENV_FILES:
        cfg = load_cfg(env_path)
        env_id = str(cfg.acoustic.environment_id)
        tids = verify_traces(cfg, TEST_SEEDS)
        all_jobs.extend(iter_primary_jobs(cfg, tids, env_id, TEST_SEEDS))
        all_jobs.extend(iter_local_jobs(cfg, tids, env_id, TEST_SEEDS))

    n_primary = sum(1 for j in all_jobs if j["slice"] == "primary_reassign")
    n_local = sum(1 for j in all_jobs if j["slice"] == "local_secondary")
    workers = resolve_simpy_workers()
    print(f"[e4-test] parent={parent} primary={n_primary} local={n_local} workers={workers}", flush=True)
    payloads = run_simpy_jobs(all_jobs, max_workers=workers)
    rows = [row_from(metrics_from_worker(p), job) for p, job in zip(payloads, all_jobs)]
    if any(int(r["seed"]) in DEV_SEEDS_FORBIDDEN for r in rows):
        raise SystemExit("DEV seed leaked into TEST")
    if any(r["trace_id"] != r["expected_trace_id"] for r in rows):
        raise SystemExit("trace mismatch")
    if any(int(r.get("e2_n_controlled_proposals") or 0) != 1 for r in rows):
        raise SystemExit("controlled proposal count != 1")

    write_csv(OUT / "e4_test_raw.csv", rows)
    write_csv(
        OUT / "e4_test_event_log.csv",
        [
            {
                k: r[k]
                for k in (
                    "run_id",
                    "slice",
                    "method_id",
                    "context_mode",
                    "authority_age_s",
                    "cell_status",
                    "region",
                    "decision",
                    "reason_code",
                    "peer_age_s",
                    "assignment_age_s",
                    "executed",
                    "controlled_safe_useful_execution",
                    "obsolete_reassignment_execution",
                    "oracle_global_target_failed",
                    "local_target_failed",
                )
            }
            for r in rows
        ],
    )

    summary = [rates_for_method(rows, mid) for _, _, mid in METHODS]
    write_csv(OUT / "e4_test_summary.csv", summary)
    by_mid = {s["method_id"]: s for s in summary}

    rng = np.random.default_rng(BOOT_SEED)
    paired = []
    for other in ("B1", "B2"):
        for status, key, name in [
            ("valid", "controlled_safe_useful_execution", "useful_valid_execution"),
            ("obsolete", "obsolete_reassignment_execution", "obsolete_execution"),
            ("obsolete", "ownership_override_of_available_target", "ownership_override"),
        ]:
            a = cluster_metric_means(rows, "B3", status, key)
            b = cluster_metric_means(rows, other, status, key)
            diffs = [x - y for x, y in zip(a, b)]
            m, lo, hi, kind = bootstrap_mean_ci(diffs, rng)
            paired.append(
                {
                    "contrast": f"B3-{other}",
                    "metric": name,
                    "mean": m,
                    "ci95_lo": lo,
                    "ci95_hi": hi,
                    "n_clusters": len(diffs),
                    "ci_note": (
                        "deterministic identity across the 15 held-out clusters"
                        if kind == "deterministic_identity"
                        else kind
                    ),
                }
            )
        for key, name in [
            ("hard_safety_violation_count", "hard_safety"),
            ("mission_utility", "mission_utility"),
            ("governance_airtime_s", "governance_airtime_s"),
        ]:
            by = defaultdict(dict)
            for r in primary(rows):
                by[cluster_key(r)].setdefault(r["method_id"], []).append(_num(r.get(key)))
            diffs = []
            for ck, d in by.items():
                if "B3" in d and other in d:
                    diffs.append(float(np.mean(d["B3"])) - float(np.mean(d[other])))
            m, lo, hi, kind = bootstrap_mean_ci(diffs, rng)
            paired.append(
                {
                    "contrast": f"B3-{other}",
                    "metric": name,
                    "mean": m,
                    "ci95_lo": lo,
                    "ci95_hi": hi,
                    "n_clusters": len(diffs),
                    "ci_note": (
                        "deterministic identity across the 15 held-out clusters"
                        if kind == "deterministic_identity"
                        else kind
                    ),
                }
            )
    write_csv(OUT / "e4_test_paired_effects.csv", paired)

    region_rows = []
    for reg in ("R1", "R2", "R3", "R4"):
        for _, _, mid in METHODS:
            region_rows.append(region_rates(rows, mid, reg))
    write_csv(OUT / "e4_test_region_summary.csv", region_rows)

    air = []
    for s in summary:
        air.append(
            {
                "method_id": s["method_id"],
                "governance_airtime_s": s["governance_airtime_s"],
                "evidence_refresh_airtime_s": s["evidence_refresh_airtime_s"],
                "mission_airtime_s": s["mission_airtime_s"],
                "total_airtime_s": s["total_airtime_s"],
                "governance_airtime_frac": s["governance_airtime_frac"],
                "total_airtime_frac": s["total_airtime_frac"],
                "duration_s": DURATION_S,
            }
        )
    write_csv(OUT / "e4_test_airtime.csv", air)

    local_rows = [r for r in rows if r["slice"] == "local_secondary"]
    local_sum = []
    for _, _, mid in METHODS:
        sub = [r for r in local_rows if r["method_id"] == mid]
        local_sum.append(
            {
                "method_id": mid,
                "n": len(sub),
                "allow_rate": float(np.mean([1.0 if r["decision"] == "ALLOW" else 0.0 for r in sub])) if sub else float("nan"),
                "reason_mode": sorted({r["reason_code"] for r in sub}),
            }
        )

    case, claim, unsup = classify(by_mid["B0"], by_mid["B1"], by_mid["B2"], by_mid["B3"], region_rows)

    make_pareto(by_mid, paper / "e4_pareto.pdf", paper / "e4_pareto.png")
    primary_tbl = [
        {
            "method": mid,
            "useful_valid": by_mid[mid]["useful_valid_execution_rate"],
            "obsolete": by_mid[mid]["obsolete_execution_rate"],
            "ownership_override": by_mid[mid]["ownership_override_rate"],
            "hard_safety": by_mid[mid]["hard_safety_violation_rate"],
            "mission_utility": by_mid[mid]["mission_utility"],
        }
        for mid in ("B0", "B1", "B2", "B3")
    ]
    write_csv(paper / "e4_primary_table.csv", primary_tbl)
    write_csv(paper / "e4_region_table.csv", region_rows)
    (paper / "e4_primary_table.md").write_text(
        "# E4 TEST primary reassignment table\n\n"
        "Held-out seeds 5–9. Local actions excluded.\n\n"
        "| method | useful_valid | obsolete | ownership | hard-safety |\n"
        "|---|---:|---:|---:|---:|\n"
        + "\n".join(
            f"| {r['method']} | {r['useful_valid']:.4f} | {r['obsolete']:.4f} | {r['ownership_override']:.4f} | {r['hard_safety']:.4f} |"
            for r in primary_tbl
        )
        + f"\n\n**Case:** {case}\n"
    )

    def fmt_pair(metric, contrast):
        p = next(x for x in paired if x["metric"] == metric and x["contrast"] == contrast)
        return p

    case_md = [
        "# E4 TEST case",
        "",
        f"**Classification:** {case}",
        "",
        "Primary analysis is `reassign_another_auv` only. Local slice is secondary.",
        "",
        "## Primary rates",
        "",
        "| method | useful-valid | obsolete |",
        "|---|---:|---:|",
    ]
    for mid in ("B0", "B1", "B2", "B3"):
        case_md.append(
            f"| {mid} | {by_mid[mid]['useful_valid_execution_rate']:.4f} | {by_mid[mid]['obsolete_execution_rate']:.4f} |"
        )
    case_md += ["", "## Supported claim", "", claim, "", "## Unsupported", "", unsup, ""]
    (OUT / "e4_test_case.md").write_text("\n".join(case_md) + "\n")

    val = [
        "# E4 TEST validation",
        "",
        f"**Parent SHA:** `{parent}`",
        f"**Primary runs:** {n_primary}",
        f"**Local secondary runs:** {n_local}",
        f"**TEST seeds:** {TEST_SEEDS}",
        f"**Case:** {case}",
        "",
        "- forbid_trace_generation: true",
        "- no DEV seeds 0–4",
        "- one controlled proposal per cell",
        "- local events not pooled into primary rates",
        "- budgets frozen 40/60/60/300/600",
        "",
        "## Local secondary allow rates",
        "",
    ]
    for s in local_sum:
        val.append(f"- {s['method_id']}: allow_rate={s['allow_rate']:.4f} reasons={s['reason_mode']}")
    (OUT / "e4_test_validation.md").write_text("\n".join(val) + "\n")
    (OUT / "e4_test_manifest.md").write_text(
        "\n".join(
            [
                "# E4 TEST manifest",
                "",
                f"- time: {datetime.now(timezone.utc).isoformat()}",
                f"- parent: `{parent}`",
                f"- campaign: `{CAMPAIGN}`",
                f"- primary_jobs: {n_primary}",
                f"- local_jobs: {n_local}",
                f"- test_seeds: {TEST_SEEDS}",
                f"- methods: B0 NoFreshness, B1 lease-400, B2 CGEA, B3 Evidence",
                f"- bootstrap: {N_BOOT} seed {BOOT_SEED}",
                f"- case: {case}",
                f"- no manuscript edit",
                "",
            ]
        )
        + "\n"
    )
    print(f"[e4-test] {case} primary={n_primary} local={n_local} out={OUT}", flush=True)
    print(
        f"[e4-test] B0 uv/ob={by_mid['B0']['useful_valid_execution_rate']:.4f}/{by_mid['B0']['obsolete_execution_rate']:.4f} "
        f"B1={by_mid['B1']['useful_valid_execution_rate']:.4f}/{by_mid['B1']['obsolete_execution_rate']:.4f} "
        f"B2={by_mid['B2']['useful_valid_execution_rate']:.4f}/{by_mid['B2']['obsolete_execution_rate']:.4f} "
        f"B3={by_mid['B3']['useful_valid_execution_rate']:.4f}/{by_mid['B3']['obsolete_execution_rate']:.4f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
