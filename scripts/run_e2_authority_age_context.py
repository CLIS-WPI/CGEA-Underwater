#!/usr/bin/env python3
"""E2: authority age × mission context. 3 env × 10 seeds × 4 ages × 2 contexts × 2 variants.

Reuses E1 production GPU traces. Does not retune B4 or overwrite E1 results.
"""

from __future__ import annotations

import argparse
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

from run_e1_pilot_v2 import _mission_kwargs  # noqa: E402
from run_e1_pilot_v3 import ENV_FILES, load_base_cfg  # noqa: E402
from cgea.experiments.e2_authority_age import AGE_CONDITIONS, CONTEXT_MODES, validate_freshness_table  # noqa: E402
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

CAMPAIGN = os.environ.get("CGEA_CAMPAIGN", "e2_authority_age_context")
SEEDS = list(range(10))
VARIANTS = ["B4", "B4-NoFreshness"]
N_BOOT = 10_000
BOOT_SEED = 20260929
OUTAGE_START = 200.0
OUTAGE_END = 1200.0

PRODUCTION_TRACES = {
    "paper_ssp_200m_v1": {
        0: "tr_54fb653caded_gpu",
        1: "tr_3bf719402f6c_gpu",
        2: "tr_0ab8a47a1225_gpu",
        3: "tr_f9f4d2c6f6d1_gpu",
        4: "tr_5bf96997e3af_gpu",
        5: "tr_15e068cb7ee2_gpu",
        6: "tr_554048377072_gpu",
        7: "tr_58d2b05ddd66_gpu",
        8: "tr_ca1dfa6822d6_gpu",
        9: "tr_f9d255d666ea_gpu",
    },
    "paper_ssp_200m_moderate_v1": {
        0: "tr_e481a0d054ed_gpu",
        1: "tr_a88224c7b787_gpu",
        2: "tr_4370fea4680c_gpu",
        3: "tr_20211a97cb5f_gpu",
        4: "tr_6f36c0ce7c2d_gpu",
        5: "tr_5d331a011f4e_gpu",
        6: "tr_fb806e94e0f6_gpu",
        7: "tr_0339ceb0e831_gpu",
        8: "tr_6d424feb0439_gpu",
        9: "tr_777f672e9af8_gpu",
    },
    "paper_ssp_200m_strong_v1": {
        0: "tr_41578464d64c_gpu",
        1: "tr_6c0fce5b4aa8_gpu",
        2: "tr_e18b2f198ce8_gpu",
        3: "tr_90c59e32088b_gpu",
        4: "tr_d1ea9e6ed537_gpu",
        5: "tr_656c2a3d7e5e_gpu",
        6: "tr_59abcdc50842_gpu",
        7: "tr_4e1f28b2f305_gpu",
        8: "tr_cdd5f1950a3c_gpu",
        9: "tr_b3e3101c8c2a_gpu",
    },
}

VALID_USEFUL_CELLS = {
    ("benign_static", "fresh_120"),
    ("benign_static", "aging_240"),
    ("benign_static", "stale_520"),
    ("benign_static", "hard_960"),
    ("target_recovers_age300", "fresh_120"),
    ("target_recovers_age300", "aging_240"),
}
STALE_PROTECT_CELLS = {
    ("target_recovers_age300", "stale_520"),
    ("target_recovers_age300", "hard_960"),
}

AGE_ORDER = ["fresh_120", "aging_240", "stale_520", "hard_960"]
AGE_XS = [120, 240, 520, 960]


def load_e2_cfg(acoustic_path: Path):
    cfg = load_base_cfg(acoustic_path)
    e2 = OmegaConf.load(ROOT / "configs" / "experiment" / "e2_authority_age_context.yaml")
    cfg.experiment = OmegaConf.merge(cfg.experiment, e2)
    cfg.experiment.name = CAMPAIGN
    cfg.experiment.log_consequential_decisions = True
    cfg.scenario.outage_disabled = False
    cfg.scenario.outage_start_s = OUTAGE_START
    cfg.scenario.outage_end_s = OUTAGE_END
    cfg.scenario.partition_groups = None
    cfg.force_regenerate_trace = False
    cfg.forbid_trace_generation = True
    return cfg


def verify_traces(cfg, seeds) -> dict[int, str]:
    env_id = str(cfg.acoustic.environment_id)
    expected = PRODUCTION_TRACES[env_id]
    ids = {}
    for seed in seeds:
        want = expected[int(seed)]
        cfg_s = OmegaConf.merge(cfg, {"seed": int(seed), "forbid_trace_generation": True})
        world = build_pipeline_mission(**_mission_kwargs(cfg_s))
        positions = {aid: a.position for aid, a in world.auvs.items()}
        trace = ensure_trace(cfg_s, positions)
        if trace.trace_id != want:
            raise RuntimeError(f"trace mismatch env={env_id} seed={seed}: got {trace.trace_id} want {want}")
        ids[int(seed)] = trace.trace_id
    return ids


def iter_jobs(cfg, trace_ids, environment_id, seeds, age_ids, contexts, variants):
    jobs = []
    e2_base = OmegaConf.to_container(cfg.experiment.e2_authority_age_context, resolve=True)
    for seed in seeds:
        for age_id in age_ids:
            spec = AGE_CONDITIONS[age_id]
            for context in contexts:
                for baseline in variants:
                    e2 = dict(e2_base)
                    e2.update(
                        {
                            "enabled": True,
                            "challenge_time_s": spec["challenge_time_s"],
                            "authority_age_s": spec["authority_age_s"],
                            "expected_freshness": spec["expected_freshness"],
                            "context_mode": context,
                            "age_id": age_id,
                        }
                    )
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
                            "age_id": age_id,
                            "context_mode": context,
                            "authority_age_s": spec["authority_age_s"],
                            "challenge_time_s": spec["challenge_time_s"],
                            "expected_freshness": spec["expected_freshness"],
                            "cfg": cfg_to_container(cfg_run),
                        }
                    )
    return jobs


def event_from(m) -> dict:
    ev = dict(m.extra.get("e2_controlled_event") or {})
    return ev


def row_from(m, job: dict) -> dict:
    extra = m.extra
    ev = event_from(m)
    ub = extra.get("utility_breakdown") or {}
    return {
        "run_id": f"{job['environment_id']}|{job['seed']}|{job['context_mode']}|{job['age_id']}|{m.provenance.baseline}",
        "environment_id": job["environment_id"],
        "seed": job["seed"],
        "trace_id": m.provenance.channel_trace_id,
        "context_mode": job["context_mode"],
        "age_id": job["age_id"],
        "baseline": m.provenance.baseline,
        "ablation_variant": extra.get("ablation_variant"),
        "authority_epoch_s": ev.get("authority_epoch_s"),
        "challenge_time_s": ev.get("challenge_time_s", job["challenge_time_s"]),
        "authority_age_s": ev.get("authority_age_s", job["authority_age_s"]),
        "expected_freshness_band": ev.get("expected_freshness_band", job["expected_freshness"]),
        "actual_freshness_band": ev.get("actual_freshness_band"),
        "proposer_id": ev.get("proposer_id"),
        "target_auv": ev.get("target_auv"),
        "segment_id": ev.get("segment_id"),
        "capsule_id": extra.get("e2_capsule_id") or ev.get("capsule_id"),
        "local_target_failed": ev.get("local_target_failed"),
        "local_segment_mandatory": ev.get("local_segment_mandatory"),
        "local_segment_incomplete": ev.get("local_segment_incomplete"),
        "local_segment_owner": ev.get("local_segment_owner"),
        "local_snapshot_time": ev.get("local_snapshot_time"),
        "local_snapshot_age": ev.get("local_snapshot_age"),
        "local_snapshot_version": ev.get("local_snapshot_version"),
        "global_target_failed": ev.get("global_target_failed"),
        "global_segment_mandatory": ev.get("global_segment_mandatory"),
        "global_segment_incomplete": ev.get("global_segment_incomplete"),
        "global_segment_owner": ev.get("global_segment_owner"),
        "global_state_changed": ev.get("global_state_changed"),
        "global_change_time": ev.get("global_change_time"),
        "conditional_ok_local": ev.get("conditional_ok_local"),
        "decision": ev.get("decision"),
        "reason_code": ev.get("reason_code"),
        "connectivity_state": ev.get("connectivity_state"),
        "freshness_mode": ev.get("freshness_mode"),
        "mission_beneficial": ev.get("mission_beneficial"),
        "violates_frozen_risk": ev.get("violates_frozen_risk"),
        "oracle_reason": ev.get("oracle_reason"),
        "executed": ev.get("executed"),
        "controlled_reassignment_allowed": ev.get("controlled_reassignment_allowed"),
        "controlled_safe_useful_execution": ev.get("controlled_safe_useful_execution"),
        "obsolete_reassignment_execution": ev.get("obsolete_reassignment_execution"),
        "controlled_false_denial": ev.get("controlled_false_denial"),
        "ownership_override_of_available_target": ev.get("ownership_override_of_available_target"),
        "useful_reassignment_delta": ev.get("useful_reassignment_delta"),
        "duplicate_work_delta": ev.get("duplicate_work_delta"),
        "contradictory_reassignment_delta": ev.get("contradictory_reassignment_delta"),
        "missed_mandatory_delta": ev.get("missed_mandatory_delta"),
        "utility_before": ev.get("utility_before"),
        "utility_after": ev.get("utility_after"),
        "mission_utility": m.mission_utility,
        "hard_safety_violation_rate": extra.get("violation_per_proposal"),
        "hard_safety_violation_count": extra.get("hard_safety_violation_count"),
        "safe_useful_retention": extra.get("safe_useful_retention"),
        "semantic_conflicts_per_mission": extra.get("semantic_conflicts_per_mission", m.conflict_count),
        "contradictory_reassignment": extra.get("contradictory_reassignment"),
        "useful_reassignment_count": extra.get("useful_reassignment_count"),
        "duplicate_work_count": extra.get("duplicate_work_count"),
        "missed_mandatory": extra.get("missed_mandatory"),
        "anomalies_resolved": ub.get("anomalies_resolved"),
        "exclusion_violations": ub.get("exclusion_violations"),
        "reserve_violations": ub.get("reserve_violations"),
        "completed_mandatory": ub.get("completed_mandatory"),
        "utility_breakdown": json.dumps(ub),
        "policy_version": extra.get("policy_version", PAPER_POLICY_VERSION),
        "utility_freeze_id": extra.get("utility_freeze_id", UTILITY_FREEZE_ID),
        "safe_useful_retention_freeze_id": extra.get(
            "safe_useful_retention_freeze_id", SAFE_USEFUL_RETENTION_FREEZE_ID
        ),
        "git_commit": m.provenance.git_commit,
        "e2_n_controlled_proposals": extra.get("e2_n_controlled_proposals"),
    }


def _num(v):
    if v is None or v == "":
        return float("nan")
    try:
        return float(v)
    except (TypeError, ValueError):
        if isinstance(v, bool):
            return float(v)
        return float("nan")


def cluster_key(row):
    return (row["environment_id"], int(row["seed"]))


def paired_cluster_means(rows, metric, context, age_id):
    by = defaultdict(dict)
    for r in rows:
        if r["context_mode"] != context or r["age_id"] != age_id:
            continue
        by[cluster_key(r)][r["baseline"]] = _num(r.get(metric))
    diffs = []
    for ck, d in sorted(by.items()):
        if "B4" in d and "B4-NoFreshness" in d:
            diffs.append(d["B4"] - d["B4-NoFreshness"])
    return diffs


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


def cluster_bootstrap_paired(rows, metric, cells, rng):
    """Sample 30 (env,seed) clusters; carry selected cells' paired diffs."""
    clusters = sorted({cluster_key(r) for r in rows})
    by = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        by[cluster_key(r)][(r["context_mode"], r["age_id"])][r["baseline"]] = _num(r.get(metric))
    n = len(clusters)
    obs = []
    for ck in clusters:
        vals = []
        for cell in cells:
            d = by[ck].get(cell, {})
            if "B4" in d and "B4-NoFreshness" in d:
                vals.append(d["B4"] - d["B4-NoFreshness"])
        obs.append(float(np.mean(vals)) if vals else float("nan"))
    obs = np.asarray(obs, dtype=float)
    m = float(np.nanmean(obs))
    draws = []
    for _ in range(N_BOOT):
        idx = rng.choice(n, size=n, replace=True)
        draws.append(float(np.nanmean(obs[idx])))
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return m, float(lo), float(hi), obs.tolist()


def classify_case(useful_mean, useful_lo, useful_hi, obsolete_mean, obsolete_lo, obsolete_hi, env_spread):
    cost = useful_mean < -1e-9 and useful_hi < 0
    cost_weak = useful_mean < -1e-9
    protect = obsolete_mean < -1e-9 and obsolete_hi < 0
    protect_weak = obsolete_mean < -1e-9
    no_protect = abs(obsolete_mean) < 1e-9 or obsolete_lo >= 0
    no_cost = abs(useful_mean) < 1e-9 or useful_lo >= 0
    if env_spread:
        return "D", (
            "Freshness effects are context-dependent and do not support a universal benefit."
        )
    if cost and protect:
        return "A", (
            "Freshness is useful conditionally: it exchanges availability for protection "
            "against execution based on obsolete mission context."
        )
    if cost_weak and no_protect:
        return "B", (
            "The evaluated freshness threshold is conservative; a protection benefit was not demonstrated."
        )
    if protect and no_cost:
        return "C", (
            "Freshness suppresses obsolete execution with little measured availability cost "
            "in this controlled workload."
        )
    if cost_weak and protect_weak:
        return "A", (
            "Freshness is useful conditionally: it exchanges availability for protection "
            "against execution based on obsolete mission context."
        )
    return "D", (
        "Freshness effects are context-dependent and do not support a universal benefit."
    )


def write_csv(path: Path, rows: list[dict], fieldnames=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fields = fieldnames or list(rows[0].keys())
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def make_figure(summary_rows, out_pdf: Path, out_png: Path):
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 5.4), sharex=True)
    contexts = ["benign_static", "target_recovers_age300"]
    titles = {
        "benign_static": "Benign / no change",
        "target_recovers_age300": "Target recovers at age 300 s",
    }
    metrics = [
        ("controlled_safe_useful_execution", "Safe useful execution"),
        ("obsolete_reassignment_execution", "Obsolete reassignment"),
    ]
    colors = {"B4": "#1f4e79", "B4-NoFreshness": "#c45911"}
    markers = {"B4": "o", "B4-NoFreshness": "s"}
    for i, ctx in enumerate(contexts):
        for j, (metric, ylab) in enumerate(metrics):
            ax = axes[i][j]
            for var in VARIANTS:
                xs, ys = [], []
                for age_id, x in zip(AGE_ORDER, AGE_XS):
                    for r in summary_rows:
                        if r["context_mode"] == ctx and r["age_id"] == age_id and r["baseline"] == var:
                            xs.append(x)
                            ys.append(_num(r[f"{metric}_mean"]))
                ax.plot(xs, ys, marker=markers[var], color=colors[var], label=var, linewidth=1.6)
            ax.set_xticks(AGE_XS)
            ax.set_ylim(-0.05, 1.05)
            ax.grid(True, alpha=0.3)
            if i == 1:
                ax.set_xlabel("Authority age (s)")
            if j == 0:
                ax.set_ylabel(ylab)
            else:
                ax.set_ylabel(ylab)
            if i == 0 and j == 1:
                ax.legend(frameon=False, fontsize=8)
            ax.set_title(titles[ctx] if j == 0 else "", loc="left", fontsize=9)
    fig.suptitle("E2  Freshness contraction vs risk-bounded governor without freshness", fontsize=10)
    fig.tight_layout()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_pdf)
    fig.savefig(out_png, dpi=200)
    plt.close(fig)


def sanity_validate(rows: list[dict]) -> list[str]:
    fails = []
    if not rows:
        return ["no rows"]
    by_run = defaultdict(list)
    for r in rows:
        by_run[r["run_id"]].append(r)
        if int(r.get("e2_n_controlled_proposals") or 0) != 1:
            fails.append(f"n_controlled!=1 {r['run_id']}")
        if r.get("actual_freshness_band") != r.get("expected_freshness_band"):
            fails.append(
                f"freshness {r['run_id']} expected {r.get('expected_freshness_band')} got {r.get('actual_freshness_band')}"
            )
        if r.get("local_target_failed") not in (True, 1, "True", "true"):
            fails.append(f"local_target_failed {r['run_id']}")
        if _num(r.get("local_snapshot_time")) != 180.0:
            fails.append(f"snapshot time {r['run_id']}")
        if r.get("violates_frozen_risk") in (True, 1, "True"):
            fails.append(f"hard-risk true {r['run_id']}")
        ctx = r["context_mode"]
        age = r["age_id"]
        ben = r.get("mission_beneficial") in (True, 1, "True", "true")
        if ctx == "benign_static" and not ben:
            fails.append(f"benign not beneficial {r['run_id']}")
        if ctx == "target_recovers_age300":
            if age in ("fresh_120", "aging_240") and not ben:
                fails.append(f"recover early not beneficial {r['run_id']}")
            if age in ("stale_520", "hard_960") and ben:
                fails.append(f"recover late still beneficial {r['run_id']}")
            changed = r.get("global_state_changed") in (True, 1, "True", "true")
            gfail = r.get("global_target_failed") in (True, 1, "True", "true")
            if age in ("stale_520", "hard_960"):
                if not changed:
                    fails.append(f"no global change {r['run_id']}")
                if gfail:
                    fails.append(f"global still failed {r['run_id']}")
                if _num(r.get("global_change_time")) != 480.0:
                    fails.append(f"change time {r['run_id']}")
            if age in ("fresh_120", "aging_240") and not gfail:
                fails.append(f"global recovered too early {r['run_id']}")
    # paired local snapshot identity
    keyed = defaultdict(dict)
    for r in rows:
        k = (r["environment_id"], r["seed"], r["context_mode"], r["age_id"])
        keyed[k][r["baseline"]] = r
    for k, d in keyed.items():
        if "B4" in d and "B4-NoFreshness" in d:
            a, b = d["B4"], d["B4-NoFreshness"]
            for field in (
                "local_snapshot_version",
                "local_target_failed",
                "local_segment_owner",
                "segment_id",
                "global_target_failed",
                "global_segment_owner",
            ):
                if a.get(field) != b.get(field):
                    fails.append(f"mismatch {field} {k}")
            if a.get("freshness_mode") == b.get("freshness_mode"):
                fails.append(f"freshness_mode identical {k}: {a.get('freshness_mode')}")
    return fails[:40]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sanity", action="store_true")
    args = ap.parse_args()
    validate_freshness_table()

    out = ROOT / "results" / CAMPAIGN
    paper = out / "paper"
    out.mkdir(parents=True, exist_ok=True)
    paper.mkdir(parents=True, exist_ok=True)

    seeds = [0] if args.sanity else SEEDS
    envs = ENV_FILES[:1] if args.sanity else ENV_FILES
    ages = AGE_ORDER
    contexts = list(CONTEXT_MODES)
    variants = VARIANTS

    all_rows = []
    trace_map = {}
    workers = resolve_simpy_workers()
    print(f"[e2] workers={workers} sanity={args.sanity}", flush=True)

    for env_path in envs:
        cfg = load_e2_cfg(env_path)
        env_id = str(cfg.acoustic.environment_id)
        print(f"[e2] env={env_id}", flush=True)
        tids = verify_traces(cfg, seeds)
        trace_map[env_id] = {str(k): v for k, v in tids.items()}
        jobs = iter_jobs(cfg, tids, env_id, seeds, ages, contexts, variants)
        payloads = run_simpy_jobs(jobs, max_workers=workers)
        for job, payload in zip(jobs, payloads):
            m = metrics_from_worker(payload)
            all_rows.append(row_from(m, job))

    fails = sanity_validate(all_rows)
    if fails:
        report = out / "e2_validation.md"
        report.write_text("# E2 sanity FAILED\n\n" + "\n".join(f"- {x}" for x in fails) + "\n")
        raise SystemExit("E2 sanity failed:\n" + "\n".join(fails))

    if args.sanity:
        (out / "e2_validation.md").write_text(
            "# E2 sanity PASSED (dry-run subset)\n\n"
            f"n_runs={len(all_rows)}\n"
            "Full 480-run campaign was not started by --sanity.\n"
        )
        write_csv(out / "e2_sanity_raw.csv", all_rows)
        print("[e2] sanity passed", flush=True)
        return

    rng = np.random.default_rng(BOOT_SEED)
    write_csv(out / "e2_raw.csv", all_rows)
    (out / "e2_raw.json").write_text(json.dumps(all_rows, indent=2, default=str))
    write_csv(out / "e2_controlled_event_log.csv", all_rows)

    summary = []
    for ctx in CONTEXT_MODES:
        for age_id in AGE_ORDER:
            for var in VARIANTS:
                sub = [r for r in all_rows if r["context_mode"] == ctx and r["age_id"] == age_id and r["baseline"] == var]
                def mci(key):
                    xs = [_num(r.get(key)) for r in sub]
                    a = np.asarray(xs, dtype=float)
                    return float(np.nanmean(a)), *bootstrap_mean_ci(xs, rng)[1:]
                su_m, su_lo, su_hi = mci("controlled_safe_useful_execution")
                ob_m, ob_lo, ob_hi = mci("obsolete_reassignment_execution")
                al_m, al_lo, al_hi = mci("controlled_reassignment_allowed")
                fd_m, fd_lo, fd_hi = mci("controlled_false_denial")
                ow_m, ow_lo, ow_hi = mci("ownership_override_of_available_target")
                ut_m, ut_lo, ut_hi = mci("mission_utility")
                hs_m, hs_lo, hs_hi = mci("hard_safety_violation_count")
                sr_m, sr_lo, sr_hi = mci("safe_useful_retention")
                summary.append(
                    {
                        "context_mode": ctx,
                        "age_id": age_id,
                        "authority_age_s": AGE_CONDITIONS[age_id]["authority_age_s"],
                        "baseline": var,
                        "n": len(sub),
                        "controlled_safe_useful_execution_mean": su_m,
                        "controlled_safe_useful_execution_lo": su_lo,
                        "controlled_safe_useful_execution_hi": su_hi,
                        "obsolete_reassignment_execution_mean": ob_m,
                        "obsolete_reassignment_execution_lo": ob_lo,
                        "obsolete_reassignment_execution_hi": ob_hi,
                        "controlled_reassignment_allowed_mean": al_m,
                        "controlled_false_denial_mean": fd_m,
                        "ownership_override_mean": ow_m,
                        "mission_utility_mean": ut_m,
                        "mission_utility_lo": ut_lo,
                        "mission_utility_hi": ut_hi,
                        "hard_safety_violation_count_mean": hs_m,
                        "safe_useful_retention_mean": sr_m,
                    }
                )
    write_csv(out / "e2_context_age_summary.csv", summary)

    paired_rows = []
    for ctx in CONTEXT_MODES:
        for age_id in AGE_ORDER:
            for metric in (
                "controlled_safe_useful_execution",
                "obsolete_reassignment_execution",
                "controlled_false_denial",
                "ownership_override_of_available_target",
                "mission_utility",
                "hard_safety_violation_count",
                "safe_useful_retention",
            ):
                diffs = paired_cluster_means(all_rows, metric, ctx, age_id)
                m, lo, hi = bootstrap_mean_ci(diffs, rng)
                paired_rows.append(
                    {
                        "context_mode": ctx,
                        "age_id": age_id,
                        "metric": metric,
                        "n_clusters": len(diffs),
                        "b4_minus_nofreshness_mean": m,
                        "ci95_lo": lo,
                        "ci95_hi": hi,
                    }
                )
    write_csv(out / "e2_cluster_paired_effects.csv", paired_rows)

    ub_rows = []
    for r in all_rows:
        ub_rows.append(
            {
                "run_id": r["run_id"],
                "environment_id": r["environment_id"],
                "seed": r["seed"],
                "context_mode": r["context_mode"],
                "age_id": r["age_id"],
                "baseline": r["baseline"],
                "mission_utility": r["mission_utility"],
                "utility_breakdown": r["utility_breakdown"],
                "completed_mandatory": r["completed_mandatory"],
                "anomalies_resolved": r["anomalies_resolved"],
                "exclusion_violations": r["exclusion_violations"],
                "reserve_violations": r["reserve_violations"],
                "missed_mandatory": r["missed_mandatory"],
            }
        )
    write_csv(out / "e2_utility_breakdown.csv", ub_rows)

    useful_m, useful_lo, useful_hi, useful_obs = cluster_bootstrap_paired(
        all_rows, "controlled_safe_useful_execution", VALID_USEFUL_CELLS, rng
    )
    obsolete_m, obsolete_lo, obsolete_hi, obsolete_obs = cluster_bootstrap_paired(
        all_rows, "obsolete_reassignment_execution", STALE_PROTECT_CELLS, rng
    )
    env_means = defaultdict(list)
    for r, ck_obs in zip(
        sorted({cluster_key(r) for r in all_rows}),
        useful_obs,
    ):
        pass
    by_env = defaultdict(list)
    clusters = sorted({cluster_key(r) for r in all_rows})
    for ck, u, o in zip(clusters, useful_obs, obsolete_obs):
        by_env[ck[0]].append((u, o))
    env_spread = False
    signs_u = []
    for env_id, pairs in by_env.items():
        mu = float(np.nanmean([p[0] for p in pairs]))
        signs_u.append(np.sign(mu) if abs(mu) > 1e-9 else 0.0)
    if len(set(signs_u) - {0.0}) > 1:
        env_spread = True

    case, claim = classify_case(useful_m, useful_lo, useful_hi, obsolete_m, obsolete_lo, obsolete_hi, env_spread)

    primary = []
    for ctx in CONTEXT_MODES:
        for age_id in AGE_ORDER:
            rec = {"context_mode": ctx, "age_id": age_id, "authority_age_s": AGE_CONDITIONS[age_id]["authority_age_s"]}
            for var in VARIANTS:
                s = next(x for x in summary if x["context_mode"] == ctx and x["age_id"] == age_id and x["baseline"] == var)
                rec[f"{var}_safe_useful"] = s["controlled_safe_useful_execution_mean"]
                rec[f"{var}_obsolete"] = s["obsolete_reassignment_execution_mean"]
            dsu = next(
                x
                for x in paired_rows
                if x["context_mode"] == ctx
                and x["age_id"] == age_id
                and x["metric"] == "controlled_safe_useful_execution"
            )
            dob = next(
                x
                for x in paired_rows
                if x["context_mode"] == ctx
                and x["age_id"] == age_id
                and x["metric"] == "obsolete_reassignment_execution"
            )
            rec["delta_safe_useful_B4_minus_NF"] = dsu["b4_minus_nofreshness_mean"]
            rec["delta_safe_useful_ci95_lo"] = dsu["ci95_lo"]
            rec["delta_safe_useful_ci95_hi"] = dsu["ci95_hi"]
            rec["delta_obsolete_B4_minus_NF"] = dob["b4_minus_nofreshness_mean"]
            rec["delta_obsolete_ci95_lo"] = dob["ci95_lo"]
            rec["delta_obsolete_ci95_hi"] = dob["ci95_hi"]
            primary.append(rec)
    write_csv(paper / "e2_primary_table.csv", primary)

    md_lines = [
        "# E2 primary table (cluster-paired B4 − B4-NoFreshness)",
        "",
        "| context | age | B4 useful | NF useful | Δ useful | B4 obsolete | NF obsolete | Δ obsolete |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rec in primary:
        md_lines.append(
            "| {context_mode} | {age_id} | {B4_safe_useful:.3f} | {B4-NoFreshness_safe_useful:.3f} | "
            "{delta_safe_useful_B4_minus_NF:.3f} | {B4_obsolete:.3f} | {B4-NoFreshness_obsolete:.3f} | "
            "{delta_obsolete_B4_minus_NF:.3f} |".format(**rec)
        )
    (paper / "e2_primary_table.md").write_text("\n".join(md_lines) + "\n")

    make_figure(summary, paper / "e2_authority_age_context.pdf", paper / "e2_authority_age_context.png")

    hs = float(np.mean([_num(r["hard_safety_violation_count"]) for r in all_rows]))
    util_d = next(
        (
            x
            for x in paired_rows
            if x["metric"] == "mission_utility" and x["age_id"] == "aging_240" and x["context_mode"] == "benign_static"
        ),
        None,
    )
    all_util = paired_cluster_means  # noqa: F841

    util_diffs_all = []
    byu = defaultdict(dict)
    for r in all_rows:
        byu[cluster_key(r)][r["baseline"]] = byu[cluster_key(r)].get(r["baseline"], [])
    # mean utility per cluster-baseline across all cells
    util_map = defaultdict(lambda: defaultdict(list))
    for r in all_rows:
        util_map[cluster_key(r)][r["baseline"]].append(_num(r["mission_utility"]))
    udiffs = []
    for ck, d in util_map.items():
        if "B4" in d and "B4-NoFreshness" in d:
            udiffs.append(float(np.mean(d["B4"])) - float(np.mean(d["B4-NoFreshness"])))
    um, ulo, uhi = bootstrap_mean_ci(udiffs, rng)

    case_md = f"""# E2 case

CASE {case}

Supported claim:
{claim}

Primary estimand 1 (valid-context safe useful execution, B4 − NoFreshness):
mean={useful_m:.4f}  95% CI [{useful_lo:.4f}, {useful_hi:.4f}]

Primary estimand 2 (stale-context obsolete execution, B4 − NoFreshness):
mean={obsolete_m:.4f}  95% CI [{obsolete_lo:.4f}, {obsolete_hi:.4f}]

Hard-safety violation count (mean over runs): {hs:.6f}
Mission utility paired B4 − NoFreshness (cluster mean over all cells): {um:.4f} [{ulo:.4f}, {uhi:.4f}]

n_runs={len(all_rows)}
bootstrap_seed={BOOT_SEED}
n_boot={N_BOOT}
"""
    (out / "e2_case.md").write_text(case_md)
    (paper / "e2_case.md").write_text(case_md)

    manifest = f"""# E2 authority-age × mission-context

- git_commit: `{git_commit(ROOT)}`
- campaign: `{CAMPAIGN}`
- n_runs: {len(all_rows)} (expect 480 = 3 env × 10 seeds × 4 ages × 2 contexts × 2 variants)
- policy_version: `{PAPER_POLICY_VERSION}`
- utility_freeze_id: `{UTILITY_FREEZE_ID}`
- safe_useful_retention_freeze_id: `{SAFE_USEFUL_RETENTION_FREEZE_ID}`
- outage: 200–1200 s (fixed; not swept)
- authority_epoch_s: 180
- change_age_s: 300 (global recovery at t=480 in `target_recovers_age300` only)
- proposer: auv_06
- target: auv_05
- variants: B4, B4-NoFreshness (`b4_no_freshness_v1`)
- bootstrap: cluster-preserving over 30 (environment, seed) units, {N_BOOT} resamples, seed {BOOT_SEED}
- traces: `{json.dumps(trace_map)}`
- CASE: {case}

Do not retune 180/400/900 from these numbers. Do not overwrite E1 production.
"""
    (out / "e2_manifest.md").write_text(manifest)
    (paper / "e2_manifest.md").write_text(manifest)

    (out / "e2_validation.md").write_text(
        f"""# E2 validation PASSED

- n_runs: {len(all_rows)}
- exactly one controlled reassignment proposal per run
- B4 and B4-NoFreshness local snapshots match
- B4 and B4-NoFreshness global target/owner match
- freshness_mode differs (continuous vs disabled)
- local snapshot captured at t=180
- target_recovers_age300: global recovery at t=480; local_target_failed remains true
- freshness bands: 120 fresh, 240 aging, 520 stale, 960 hard_expired
- oracle beneficial as predeclared
- reassignment violates_frozen_risk is false
- production traces reused and matched the E1 manifest
- CASE {case}
"""
    )
    print(f"[e2] done n={len(all_rows)} CASE {case}", flush=True)


if __name__ == "__main__":
    main()
