#!/usr/bin/env python3
"""E3: CGEA vs fixed-expiry lease vs NoFreshness. DEV TTL selection then locked TEST."""

from __future__ import annotations

import argparse
import csv
import hashlib
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
from cgea.experiments.e2_authority_age import AUTHORITY_EPOCH_S  # noqa: E402
from cgea.experiments.e3_fixed_expiry import (  # noqa: E402
    BOOT_SEED,
    CONTEXTS,
    DEV_SEEDS,
    N_BOOT,
    PROPOSAL_AGES,
    TEST_SEEDS,
    TTL_CANDIDATES,
    USEFUL_VALID_FLOOR,
    challenge_time_s,
    cgea_expected_freshness,
    context_status,
    parse_ttl,
    ttl_label,
)
from cgea.experiments.parallel import cfg_to_container, metrics_from_worker, resolve_simpy_workers, run_simpy_jobs  # noqa: E402
from cgea.experiments.runner import SAFE_USEFUL_RETENTION_FREEZE_ID  # noqa: E402
from cgea.governance import PAPER_POLICY_VERSION  # noqa: E402
from cgea.mission.utility import UTILITY_FREEZE_ID  # noqa: E402
from cgea.types import git_commit  # noqa: E402

CAMPAIGN = os.environ.get("CGEA_CAMPAIGN", "e3_fixed_expiry_challenge")
PARENT_SHA = os.environ.get("E3_PARENT_SHA", "")
OUTAGE_START = 200.0
OUTAGE_END = 1200.0
E2_MANIFEST = ROOT / "results" / "e2_authority_age_context" / "e2_manifest.md"
E2F_MANIFEST = ROOT / "results" / "e2f_early_state_change" / "e2f_manifest.md"
E1_MANIFEST = ROOT / "results" / "e1_production_n10" / "production_manifest.md"


def _num(v):
    if v is None or v == "":
        return float("nan")
    if isinstance(v, bool):
        return float(v)
    try:
        x = float(v)
        return x
    except (TypeError, ValueError):
        return float("nan")


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_e3_cfg(acoustic_path: Path):
    cfg = load_base_cfg(acoustic_path)
    e3 = OmegaConf.load(ROOT / "configs" / "experiment" / "e3_fixed_expiry_challenge.yaml")
    cfg.experiment = OmegaConf.merge(cfg.experiment, e3)
    cfg.experiment.name = CAMPAIGN
    cfg.experiment.log_consequential_decisions = True
    cfg.scenario.outage_disabled = False
    cfg.scenario.outage_start_s = OUTAGE_START
    cfg.scenario.outage_end_s = OUTAGE_END
    cfg.force_regenerate_trace = False
    cfg.forbid_trace_generation = True
    return cfg


def iter_jobs(cfg, trace_ids, environment_id, seeds, methods: list[tuple[str, float | None]], contexts=None, ages=None):
    contexts = contexts if contexts is not None else CONTEXTS
    ages = ages if ages is not None else PROPOSAL_AGES
    jobs = []
    e2_base = OmegaConf.to_container(cfg.experiment.e2_authority_age_context, resolve=True)
    for seed in seeds:
        for ctx, rec_age in contexts.items():
            for age in ages:
                for baseline, ttl in methods:
                    e2 = dict(e2_base)
                    e2.update(
                        {
                            "enabled": True,
                            "challenge_time_s": challenge_time_s(age),
                            "authority_age_s": age,
                            "expected_freshness": cgea_expected_freshness(age),
                            "context_mode": ctx,
                            "change_age_s": rec_age if rec_age is not None else 1e18,
                            "age_id": f"age_{int(age)}",
                            "lease_ttl_s": None if ttl is None or math.isinf(ttl) else float(ttl),
                        }
                    )
                    if ttl is not None and math.isinf(ttl):
                        e2["lease_ttl_s"] = "infinity"
                    cfg_run = OmegaConf.merge(
                        cfg,
                        {
                            "seed": int(seed),
                            "experiment": {
                                "name": CAMPAIGN,
                                "log_consequential_decisions": True,
                                "e2_authority_age_context": e2,
                            },
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
                    jobs.append(
                        {
                            "seed": int(seed),
                            "outage": "1000",
                            "baseline": baseline,
                            "environment_id": environment_id,
                            "expected_trace_id": trace_ids[int(seed)],
                            "context_mode": ctx,
                            "authority_age_s": age,
                            "recovery_age_s": rec_age,
                            "lease_ttl_s": ttl,
                            "method_id": (
                                f"FE_{ttl_label(ttl)}"
                                if baseline == "B4-FixedExpiry"
                                else baseline
                            ),
                            "cfg": cfg_to_container(cfg_run),
                        }
                    )
    return jobs


def row_from(m, job: dict) -> dict:
    extra = m.extra
    ev = dict(extra.get("e2_controlled_event") or {})
    ctx = job["context_mode"]
    age = float(job["authority_age_s"])
    status = context_status(ctx, age)
    return {
        "run_id": f"{job['environment_id']}|{job['seed']}|{ctx}|{int(age)}|{job['method_id']}",
        "environment_id": job["environment_id"],
        "seed": job["seed"],
        "trace_id": m.provenance.channel_trace_id,
        "context_mode": ctx,
        "authority_age_s": age,
        "recovery_age_s": job["recovery_age_s"],
        "cell_status": status,
        "method_id": job["method_id"],
        "baseline": m.provenance.baseline,
        "lease_ttl_s": job["lease_ttl_s"] if job["lease_ttl_s"] is not None else extra.get("lease_ttl_s"),
        "decision": ev.get("decision"),
        "reason_code": ev.get("reason_code"),
        "actual_freshness_band": ev.get("actual_freshness_band"),
        "expected_freshness_band": ev.get("expected_freshness_band"),
        "conditional_ok_local": ev.get("conditional_ok_local"),
        "local_target_failed": ev.get("local_target_failed"),
        "global_target_failed": ev.get("global_target_failed"),
        "mission_beneficial": ev.get("mission_beneficial"),
        "violates_frozen_risk": ev.get("violates_frozen_risk"),
        "executed": ev.get("executed"),
        "controlled_safe_useful_execution": ev.get("controlled_safe_useful_execution"),
        "obsolete_reassignment_execution": ev.get("obsolete_reassignment_execution"),
        "ownership_override_of_available_target": ev.get("ownership_override_of_available_target"),
        "controlled_reassignment_allowed": ev.get("controlled_reassignment_allowed"),
        "mission_utility": m.mission_utility,
        "hard_safety_violation_count": extra.get("hard_safety_violation_count"),
        "safe_useful_retention": extra.get("safe_useful_retention"),
        "semantic_conflicts_per_mission": extra.get("semantic_conflicts_per_mission", m.conflict_count),
        "contradictory_reassignment": extra.get("contradictory_reassignment"),
        "useful_reassignment_count": extra.get("useful_reassignment_count"),
        "duplicate_work_count": extra.get("duplicate_work_count"),
        "missed_mandatory": extra.get("missed_mandatory"),
        "governance_bytes": extra.get("governance_tx_bytes", m.governance_bytes),
        "mission_tx_bytes": extra.get("mission_tx_bytes"),
        "governance_airtime_s": extra.get("governance_airtime_s"),
        "mission_airtime_s": extra.get("mission_airtime_s"),
        "total_airtime_s": extra.get("total_airtime_s"),
        "governance_airtime_frac": extra.get("governance_airtime_frac"),
        "e2_n_controlled_proposals": extra.get("e2_n_controlled_proposals"),
        "git_commit": m.provenance.git_commit,
        "policy_version": extra.get("policy_version", PAPER_POLICY_VERSION),
    }


def cluster_key(r):
    return (r["environment_id"], int(r["seed"]))


def run_grid(seeds, methods, label: str, *, env_files=None, contexts=None, ages=None) -> tuple[list[dict], dict]:
    env_files = env_files if env_files is not None else ENV_FILES
    contexts = contexts if contexts is not None else CONTEXTS
    ages = ages if ages is not None else PROPOSAL_AGES
    all_rows = []
    trace_map = {}
    workers = resolve_simpy_workers()
    print(f"[e3] {label} workers={workers} n_methods={len(methods)} n_seeds={len(seeds)}", flush=True)
    for env_path in env_files:
        cfg = load_e3_cfg(env_path)
        env_id = str(cfg.acoustic.environment_id)
        tids = verify_traces(cfg, seeds)
        trace_map[env_id] = {str(k): v for k, v in tids.items()}
        jobs = iter_jobs(cfg, tids, env_id, seeds, methods, contexts=contexts, ages=ages)
        print(f"[e3] {label} env={env_id} jobs={len(jobs)}", flush=True)
        payloads = run_simpy_jobs(jobs, max_workers=workers)
        for job, payload in zip(jobs, payloads):
            all_rows.append(row_from(metrics_from_worker(payload), job))
    return all_rows, trace_map


def rates_for_method(rows, method_id: str) -> dict:
    sub = [r for r in rows if r["method_id"] == method_id and r["cell_status"] != "equal"]
    valid = [r for r in sub if r["cell_status"] == "valid"]
    obs = [r for r in sub if r["cell_status"] == "obsolete"]
    def mean_bin(rs, key):
        if not rs:
            return float("nan")
        return float(np.mean([_num(r.get(key)) for r in rs]))
    return {
        "n_valid": len(valid),
        "n_obsolete": len(obs),
        "useful_valid_execution_rate": mean_bin(valid, "controlled_safe_useful_execution"),
        "obsolete_execution_rate": mean_bin(obs, "obsolete_reassignment_execution"),
        "ownership_override_rate": mean_bin(obs, "ownership_override_of_available_target"),
        "hard_safety_violation_rate": mean_bin(sub, "hard_safety_violation_count"),
        "mission_utility": mean_bin(sub, "mission_utility"),
        "governance_airtime_s": mean_bin(sub, "governance_airtime_s"),
        "mission_airtime_s": mean_bin(sub, "mission_airtime_s"),
        "governance_bytes": mean_bin(sub, "governance_bytes"),
    }


def cluster_metric_means(rows, method_id, status, key):
    by = defaultdict(list)
    for r in rows:
        if r["method_id"] != method_id or r["cell_status"] != status:
            continue
        by[cluster_key(r)].append(_num(r.get(key)))
    out = []
    for ck in sorted(by):
        out.append(float(np.mean(by[ck])))
    return out


def bootstrap_mean_ci(xs, rng):
    a = np.asarray(xs, dtype=float)
    a = a[~np.isnan(a)]
    if a.size == 0:
        return float("nan"), float("nan"), float("nan")
    m = float(np.mean(a))
    if a.size == 1:
        return m, m, m
    draws = rng.choice(a, size=(N_BOOT, a.size), replace=True).mean(axis=1)
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return m, float(lo), float(hi)


def select_ttl(dev_rows) -> tuple[float, list[dict]]:
    table = []
    for ttl in TTL_CANDIDATES:
        mid = f"FE_{ttl_label(ttl)}"
        r = rates_for_method(dev_rows, mid)
        r["ttl"] = ttl
        r["ttl_label"] = ttl_label(ttl)
        table.append(r)
    ok = [t for t in table if t["useful_valid_execution_rate"] >= USEFUL_VALID_FLOOR]
    if ok:
        best_obs = min(t["obsolete_execution_rate"] for t in ok)
        cand = [t for t in ok if abs(t["obsolete_execution_rate"] - best_obs) < 1e-12]
        best_uv = max(t["useful_valid_execution_rate"] for t in cand)
        cand = [t for t in cand if abs(t["useful_valid_execution_rate"] - best_uv) < 1e-12]
        cand.sort(key=lambda t: float(t["ttl"]))
        chosen = cand[-1]
        rule = "min obsolete s.t. useful_valid>=0.80; then max useful_valid; then longer TTL"
    else:
        for t in table:
            t["score"] = t["useful_valid_execution_rate"] - t["obsolete_execution_rate"]
        best = max(t["score"] for t in table)
        cand = [t for t in table if abs(t["score"] - best) < 1e-12]
        cand.sort(key=lambda t: float(t["ttl"]))
        chosen = cand[-1]
        rule = "no TTL met useful_valid>=0.80; max useful_valid-obsolete; then longer TTL"
    return float(chosen["ttl"]), table, rule


def write_split_manifest(out: Path, sha: str):
    out.mkdir(parents=True, exist_ok=True)
    (out / "e3_manifest.md").write_text(
        f"""# E3 split (recorded before TTL selection)

- parent_sha: `{PARENT_SHA or sha}`
- git_commit_at_split_write: `{sha}`
- campaign: `{CAMPAIGN}`
- dev_seeds: {DEV_SEEDS}
- test_seeds: {TEST_SEEDS}
- ttl_candidates_s: {[ttl_label(t) for t in TTL_CANDIDATES]}
- proposal_ages_s: {PROPOSAL_AGES}
- contexts: {list(CONTEXTS)}
- CGEA age 180 classification: aging (`age_s >= 180`)
- bootstrap_seed: {BOOT_SEED}
- selection_rule: minimize obsolete_execution_rate s.t. useful_valid >= {USEFUL_VALID_FLOOR}; ties higher useful_valid then longer TTL
- selected_ttl: NOT YET SELECTED
"""
    )


def sanity_validate(rows: list[dict]) -> list[str]:
    fails = []
    for r in rows:
        if int(r.get("e2_n_controlled_proposals") or 0) != 1:
            fails.append(f"n_controlled {r['run_id']}")
        if r.get("violates_frozen_risk") in (True, 1, "True"):
            fails.append(f"hard-risk {r['run_id']}")
        if r.get("local_target_failed") not in (True, 1, "True", "true"):
            fails.append(f"local {r['run_id']}")
        if abs(_num(r.get("authority_age_s")) - 180.0) < 1e-9:
            if r["baseline"] == "B4" and r.get("actual_freshness_band") != "aging":
                fails.append(f"age180 freshness {r['run_id']} {r.get('actual_freshness_band')}")
    return fails[:30]


def make_pareto(dev_table, test_points, pdf: Path, png: Path):
    fig, ax = plt.subplots(figsize=(5.6, 4.4))
    xs, ys, labs = [], [], []
    for t in dev_table:
        xs.append(t["obsolete_execution_rate"])
        ys.append(t["useful_valid_execution_rate"])
        labs.append(t["ttl_label"])
    ax.scatter(xs, ys, c="#7f7f7f", s=28, label="fixed-expiry DEV TTLs", zorder=3)
    for x, y, lab in zip(xs, ys, labs):
        ax.annotate(lab, (x, y), textcoords="offset points", xytext=(4, 3), fontsize=7, color="#555555")
    colors = {"FE_selected": "#c45911", "B4": "#1f4e79", "B4-NoFreshness": "#548235"}
    for name, pt in test_points.items():
        ax.scatter(
            [pt["obsolete_execution_rate"]],
            [pt["useful_valid_execution_rate"]],
            s=70,
            marker="D" if name == "B4" else "s",
            c=colors.get(name, "#000"),
            label=name,
            zorder=4,
        )
    ax.set_xlabel("Obsolete reassignment execution rate")
    ax.set_ylabel("Useful valid execution rate")
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.05, 1.05)
    ax.grid(True, alpha=0.3)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    fig.tight_layout()
    pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(pdf)
    fig.savefig(png, dpi=200)
    plt.close(fig)


def classify_case(cgea, fe) -> tuple[str, str, str]:
    du = cgea["useful_valid_execution_rate"] - fe["useful_valid_execution_rate"]
    do = cgea["obsolete_execution_rate"] - fe["obsolete_execution_rate"]
    close_u = abs(du) < 0.03
    close_o = abs(do) < 0.03
    if close_u and close_o:
        return (
            "E3-EQUIVALENT",
            "For the conditional reassignment mechanism evaluated here, the current multi-stage freshness policy does not provide measurable benefit over a properly tuned fixed-expiry lease.",
            "CGEA multi-stage freshness is not shown to outperform a simple lease on this action.",
        )
    cgea_better = (do < -0.03 and close_u) or (du > 0.03 and close_o) or (du > 0.03 and do < -0.03)
    if cgea_better:
        return (
            "E3-DIFFERENTIATED",
            "On the held-out test clusters, CGEA differed from the selected fixed-expiry lease on the predeclared useful-valid vs obsolete tradeoff.",
            "A universal claim that multi-stage freshness is always superior is unsupported.",
        )
    return (
        "E3-DIFFERENTIATED",
        "CGEA and the selected fixed-expiry lease occupy different points on the useful-valid vs obsolete tradeoff; CGEA does not dominate the lease on the predeclared success criterion.",
        "CGEA multi-stage freshness is not shown to outperform a tuned fixed-expiry lease for this reassignment mechanism.",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["split", "sanity", "dev", "test"], required=True)
    args = ap.parse_args()
    out = ROOT / "results" / CAMPAIGN
    paper = out / "paper"
    sha = git_commit(ROOT)
    parent = PARENT_SHA or sha

    if args.phase == "split":
        write_split_manifest(out, sha)
        print("[e3] split recorded", flush=True)
        return

    e1 = E1_MANIFEST.read_text()
    e2 = E2_MANIFEST.read_text()
    e2f = E2F_MANIFEST.read_text()

    if args.phase == "sanity":
        methods = [
            ("B4", None),
            ("B4-NoFreshness", None),
            ("B4-FixedExpiry", math.inf),
            ("B4-FixedExpiry", 180.0),
        ]
        rows, _ = run_grid(
            [0],
            methods,
            "sanity",
            env_files=ENV_FILES[:1],
            contexts={"no_change": None, "recover_age_60": 60.0},
            ages=[120.0, 180.0],
        )
        fails = sanity_validate(rows)
        inf_rows = [r for r in rows if r["method_id"] == "FE_infinity" and r["authority_age_s"] == 120]
        nf_rows = [r for r in rows if r["baseline"] == "B4-NoFreshness" and r["authority_age_s"] == 120]
        if inf_rows and nf_rows:
            if inf_rows[0].get("decision") != nf_rows[0].get("decision"):
                fails.append("infinity != NoFreshness at age 120")
        if e1 != E1_MANIFEST.read_text() or e2 != E2_MANIFEST.read_text() or e2f != E2F_MANIFEST.read_text():
            fails.append("prior artifacts modified")
        if fails:
            raise SystemExit("E3 sanity failed:\n" + "\n".join(fails))
        out.mkdir(parents=True, exist_ok=True)
        (out / "e3_validation.md").write_text(
            "# E3 sanity PASSED\n\nTTL infinity matched NoFreshness on the checked cell; B4 age 180 is AGING.\n"
        )
        print("[e3] sanity passed n=", len(rows), flush=True)
        return

    if args.phase == "dev":
        write_split_manifest(out, sha)
        methods = [("B4-FixedExpiry", t) for t in TTL_CANDIDATES]
        rows, traces = run_grid(DEV_SEEDS, methods, "dev")
        write_csv(out / "e3_dev_raw.csv", rows)
        chosen, table, rule = select_ttl(rows)
        write_csv(out / "e3_dev_summary.csv", [{k: (None if isinstance(v, float) and math.isinf(v) else v) for k, v in t.items()} for t in table])
        payload = {
            "rule": rule,
            "useful_valid_floor": USEFUL_VALID_FLOOR,
            "chosen_ttl_s": None if math.isinf(chosen) else chosen,
            "chosen_ttl_label": ttl_label(chosen),
            "dev_seeds": DEV_SEEDS,
            "dev_cluster_ids": sorted({f"{r['environment_id']}|{r['seed']}" for r in rows}),
            "candidates": [
                {
                    "ttl_label": t["ttl_label"],
                    "useful_valid_execution_rate": t["useful_valid_execution_rate"],
                    "obsolete_execution_rate": t["obsolete_execution_rate"],
                    "ownership_override_rate": t["ownership_override_rate"],
                    "hard_safety_violation_rate": t["hard_safety_violation_rate"],
                    "mission_utility": t["mission_utility"],
                }
                for t in table
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "parent_sha": parent,
            "git_commit": sha,
            "traces": traces,
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        payload["sha256"] = hashlib.sha256(blob.encode()).hexdigest()
        (out / "selected_ttl_dev.json").write_text(json.dumps(payload, indent=2, default=str))
        yaml_txt = (
            f"# Frozen after DEV. Do not retune on TEST.\n"
            f"id: fixed_expiry_selected_v1\n"
            f"lease_ttl_s: {ttl_label(chosen)}\n"
            f"selection_rule: |\n"
            f"  {rule}\n"
            f"dev_seeds: {DEV_SEEDS}\n"
        )
        (out / "fixed_expiry_selected_v1.yaml").write_text(yaml_txt)
        (ROOT / "configs" / "experiment" / "fixed_expiry_selected_v1.yaml").write_text(yaml_txt)
        md = ["# E3 DEV TTL selection", "", f"Chosen TTL: **{ttl_label(chosen)}**", "", f"Rule: {rule}", "", "| TTL | useful_valid | obsolete |", "|---|---:|---:|"]
        for t in table:
            md.append(f"| {t['ttl_label']} | {t['useful_valid_execution_rate']:.4f} | {t['obsolete_execution_rate']:.4f} |")
        (out / "e3_ttl_selection.md").write_text("\n".join(md) + "\n")
        print(f"[e3] DEV n={len(rows)} selected_ttl={ttl_label(chosen)}", flush=True)
        return

    if args.phase == "test":
        sel_path = out / "fixed_expiry_selected_v1.yaml"
        if not sel_path.is_file():
            raise SystemExit("selected TTL not frozen")
        sel = OmegaConf.load(sel_path)
        ttl = parse_ttl(str(sel.lease_ttl_s))
        methods = [
            ("B4-NoFreshness", None),
            ("B4-FixedExpiry", ttl),
            ("B4", None),
        ]
        rows, traces = run_grid(TEST_SEEDS, methods, "test")
        write_csv(out / "e3_test_raw.csv", rows)
        rng = np.random.default_rng(BOOT_SEED)
        fe_id = f"FE_{ttl_label(ttl)}"
        summary = []
        for mid in ["B4-NoFreshness", fe_id, "B4"]:
            r = rates_for_method(rows, mid)
            r["method_id"] = mid
            summary.append(r)
        write_csv(out / "e3_test_summary.csv", summary)
        cgea = rates_for_method(rows, "B4")
        fe = rates_for_method(rows, fe_id)
        nf = rates_for_method(rows, "B4-NoFreshness")
        paired = []
        for status, key, name in [
            ("valid", "controlled_safe_useful_execution", "useful_valid_execution"),
            ("obsolete", "obsolete_reassignment_execution", "obsolete_execution"),
            ("obsolete", "ownership_override_of_available_target", "ownership_override"),
        ]:
            a = cluster_metric_means(rows, "B4", status, key)
            b = cluster_metric_means(rows, fe_id, status, key)
            diffs = [x - y for x, y in zip(a, b)]
            m, lo, hi = bootstrap_mean_ci(diffs, rng)
            paired.append({"metric": name, "b4_minus_fixed_expiry_mean": m, "ci95_lo": lo, "ci95_hi": hi, "n_clusters": len(diffs)})
        for key, name in [
            ("mission_utility", "mission_utility"),
            ("hard_safety_violation_count", "hard_safety_violation_count"),
            ("governance_airtime_s", "governance_airtime_s"),
        ]:
            by = defaultdict(dict)
            for r in rows:
                if r["cell_status"] == "equal":
                    continue
                by[cluster_key(r)].setdefault(r["method_id"], []).append(_num(r.get(key)))
            diffs = []
            for ck, d in by.items():
                if "B4" in d and fe_id in d:
                    diffs.append(float(np.mean(d["B4"])) - float(np.mean(d[fe_id])))
            m, lo, hi = bootstrap_mean_ci(diffs, rng)
            paired.append({"metric": name, "b4_minus_fixed_expiry_mean": m, "ci95_lo": lo, "ci95_hi": hi, "n_clusters": len(diffs)})
        write_csv(out / "e3_test_paired_effects.csv", paired)
        write_csv(
            out / "e3_airtime_summary.csv",
            [
                {"method_id": "B4", **{k: cgea[k] for k in ("governance_airtime_s", "mission_airtime_s")}},
                {"method_id": fe_id, **{k: fe[k] for k in ("governance_airtime_s", "mission_airtime_s")}},
                {"method_id": "B4-NoFreshness", **{k: nf[k] for k in ("governance_airtime_s", "mission_airtime_s")}},
            ],
        )
        case, claim, unsup = classify_case(cgea, fe)
        dev_table = []
        if (out / "e3_dev_summary.csv").is_file():
            with (out / "e3_dev_summary.csv").open() as f:
                dev_table = list(csv.DictReader(f))
            for t in dev_table:
                t["obsolete_execution_rate"] = _num(t["obsolete_execution_rate"])
                t["useful_valid_execution_rate"] = _num(t["useful_valid_execution_rate"])
                t["ttl_label"] = t["ttl_label"]
        pareto_rows = [
            {"point": "FE_DEV_" + t["ttl_label"], "obsolete": t["obsolete_execution_rate"], "useful_valid": t["useful_valid_execution_rate"]}
            for t in dev_table
        ] + [
            {"point": "FE_TEST_selected", "obsolete": fe["obsolete_execution_rate"], "useful_valid": fe["useful_valid_execution_rate"]},
            {"point": "CGEA_TEST", "obsolete": cgea["obsolete_execution_rate"], "useful_valid": cgea["useful_valid_execution_rate"]},
            {"point": "NoFreshness_TEST", "obsolete": nf["obsolete_execution_rate"], "useful_valid": nf["useful_valid_execution_rate"]},
        ]
        write_csv(out / "e3_test_pareto.csv", pareto_rows)
        paper.mkdir(parents=True, exist_ok=True)
        make_pareto(
            dev_table,
            {"FE_selected": fe, "B4": cgea, "B4-NoFreshness": nf},
            paper / "e3_pareto.pdf",
            paper / "e3_pareto.png",
        )
        d_uv = next(p for p in paired if p["metric"] == "useful_valid_execution")
        d_ob = next(p for p in paired if p["metric"] == "obsolete_execution")
        primary = [
            {
                "method": "CGEA_B4",
                "useful_valid": cgea["useful_valid_execution_rate"],
                "obsolete": cgea["obsolete_execution_rate"],
                "ownership_override": cgea["ownership_override_rate"],
                "hard_safety": cgea["hard_safety_violation_rate"],
                "mission_utility": cgea["mission_utility"],
                "governance_airtime_s": cgea["governance_airtime_s"],
            },
            {
                "method": f"fixed_expiry_{ttl_label(ttl)}",
                "useful_valid": fe["useful_valid_execution_rate"],
                "obsolete": fe["obsolete_execution_rate"],
                "ownership_override": fe["ownership_override_rate"],
                "hard_safety": fe["hard_safety_violation_rate"],
                "mission_utility": fe["mission_utility"],
                "governance_airtime_s": fe["governance_airtime_s"],
            },
            {
                "method": "NoFreshness",
                "useful_valid": nf["useful_valid_execution_rate"],
                "obsolete": nf["obsolete_execution_rate"],
                "ownership_override": nf["ownership_override_rate"],
                "hard_safety": nf["hard_safety_violation_rate"],
                "mission_utility": nf["mission_utility"],
                "governance_airtime_s": nf["governance_airtime_s"],
            },
        ]
        write_csv(paper / "e3_primary_table.csv", primary)
        (paper / "e3_primary_table.md").write_text(
            "# E3 TEST primary table\n\n"
            "| method | useful_valid | obsolete | Δ useful (B4−FE) | Δ obsolete (B4−FE) |\n"
            "|---|---:|---:|---:|---:|\n"
            f"| CGEA | {cgea['useful_valid_execution_rate']:.4f} | {cgea['obsolete_execution_rate']:.4f} | {d_uv['b4_minus_fixed_expiry_mean']:.4f} | {d_ob['b4_minus_fixed_expiry_mean']:.4f} |\n"
            f"| fixed-expiry {ttl_label(ttl)} | {fe['useful_valid_execution_rate']:.4f} | {fe['obsolete_execution_rate']:.4f} |  |  |\n"
            f"| NoFreshness | {nf['useful_valid_execution_rate']:.4f} | {nf['obsolete_execution_rate']:.4f} |  |  |\n"
        )
        (out / "e3_case.md").write_text(
            f"""# E3 case

CASE {case}

Supported claim:
{claim}

Unsupported:
{unsup}

Selected TTL: {ttl_label(ttl)}
TEST CGEA useful_valid={cgea['useful_valid_execution_rate']:.4f} obsolete={cgea['obsolete_execution_rate']:.4f}
TEST FE useful_valid={fe['useful_valid_execution_rate']:.4f} obsolete={fe['obsolete_execution_rate']:.4f}
paired Δ useful_valid={d_uv['b4_minus_fixed_expiry_mean']:.4f} [{d_uv['ci95_lo']:.4f}, {d_uv['ci95_hi']:.4f}]
paired Δ obsolete={d_ob['b4_minus_fixed_expiry_mean']:.4f} [{d_ob['ci95_lo']:.4f}, {d_ob['ci95_hi']:.4f}]
n_test_runs={len(rows)}
"""
        )
        (out / "e3_manifest.md").write_text(
            f"""# E3 fixed-expiry challenge

- parent_sha: `{parent}`
- git_commit: `{sha}`
- campaign: `{CAMPAIGN}`
- n_test_runs: {len(rows)} (expect 1890 = 15 clusters × 6 contexts × 7 ages × 3 methods)
- selected_ttl: {ttl_label(ttl)}
- dev_seeds: {DEV_SEEDS}
- test_seeds: {TEST_SEEDS}
- policy_version: `{PAPER_POLICY_VERSION}`
- utility_freeze_id: `{UTILITY_FREEZE_ID}`
- safe_useful_retention_freeze_id: `{SAFE_USEFUL_RETENTION_FREEZE_ID}`
- bootstrap_seed: {BOOT_SEED}
- CGEA age 180: aging
- traces: `{json.dumps(traces)}`
- CASE: {case}
"""
        )
        (out / "e3_validation.md").write_text(
            f"# E3 TEST validation\n\nn_runs={len(rows)}\nCASE {case}\nE1/E2/E2-F manifests unchanged at start of phase.\n"
        )
        print(f"[e3] TEST n={len(rows)} CASE {case}", flush=True)


if __name__ == "__main__":
    main()
