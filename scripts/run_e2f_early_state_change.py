#!/usr/bin/env python3
"""E2-F: early mission-state change while authority is still FRESH.

60 runs. Reuses E1/E2 production GPU traces. Does not overwrite E2 artifacts.
"""

from __future__ import annotations

import argparse
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

from run_e1_pilot_v3 import ENV_FILES, load_base_cfg  # noqa: E402
from run_e2_authority_age_context import verify_traces  # noqa: E402
from cgea.experiments.e2_authority_age import (  # noqa: E402
    AUTHORITY_EPOCH_S,
    E2F_CHANGE_AGE_S,
    E2F_CHALLENGE_AGE_S,
    E2F_CHALLENGE_TIME_S,
    E2F_CONTEXT,
    E2F_RECOVERY_TIME_S,
    validate_freshness_table,
)
from cgea.experiments.parallel import (  # noqa: E402
    cfg_to_container,
    metrics_from_worker,
    resolve_simpy_workers,
    run_simpy_jobs,
)
from cgea.experiments.runner import SAFE_USEFUL_RETENTION_FREEZE_ID
from cgea.governance import PAPER_POLICY_VERSION
from cgea.mission.utility import UTILITY_FREEZE_ID
from cgea.types import git_commit

CAMPAIGN = os.environ.get("CGEA_CAMPAIGN", "e2f_early_state_change")
PARENT_SHA = os.environ.get("E2F_PARENT_SHA", "b020b05fd30ba074657a54f681c4b19a3085cdb2")
SEEDS = list(range(10))
VARIANTS = ["B4", "B4-NoFreshness"]
N_BOOT = 10_000
BOOT_SEED = 20260929
OUTAGE_START = 200.0
OUTAGE_END = 1200.0
E2_MANIFEST = ROOT / "results" / "e2_authority_age_context" / "e2_manifest.md"


def load_e2f_cfg(acoustic_path: Path):
    cfg = load_base_cfg(acoustic_path)
    e2f = OmegaConf.load(ROOT / "configs" / "experiment" / "e2f_early_state_change.yaml")
    cfg.experiment = OmegaConf.merge(cfg.experiment, e2f)
    cfg.experiment.name = CAMPAIGN
    cfg.experiment.log_consequential_decisions = True
    cfg.scenario.outage_disabled = False
    cfg.scenario.outage_start_s = OUTAGE_START
    cfg.scenario.outage_end_s = OUTAGE_END
    cfg.scenario.partition_groups = None
    cfg.force_regenerate_trace = False
    cfg.forbid_trace_generation = True
    return cfg


def iter_jobs(cfg, trace_ids, environment_id, seeds, variants):
    jobs = []
    e2_base = OmegaConf.to_container(cfg.experiment.e2_authority_age_context, resolve=True)
    for seed in seeds:
        for baseline in variants:
            e2 = dict(e2_base)
            e2.update(
                {
                    "enabled": True,
                    "challenge_time_s": E2F_CHALLENGE_TIME_S,
                    "authority_age_s": E2F_CHALLENGE_AGE_S,
                    "expected_freshness": "fresh",
                    "context_mode": E2F_CONTEXT,
                    "change_age_s": E2F_CHANGE_AGE_S,
                    "age_id": "fresh_120",
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
                    "age_id": "fresh_120",
                    "context_mode": E2F_CONTEXT,
                    "authority_age_s": E2F_CHALLENGE_AGE_S,
                    "challenge_time_s": E2F_CHALLENGE_TIME_S,
                    "expected_freshness": "fresh",
                    "cfg": cfg_to_container(cfg_run),
                }
            )
    return jobs


def event_from(m) -> dict:
    return dict(m.extra.get("e2_controlled_event") or {})


def _num(v):
    if v is None or v == "":
        return float("nan")
    if isinstance(v, bool):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _truthy(v) -> bool:
    return v in (True, 1, "True", "true", "1")


def row_from(m, job: dict) -> dict:
    extra = m.extra
    ev = event_from(m)
    ub = extra.get("utility_breakdown") or {}
    return {
        "run_id": f"{job['environment_id']}|{job['seed']}|{E2F_CONTEXT}|fresh_120|{m.provenance.baseline}",
        "environment_id": job["environment_id"],
        "seed": job["seed"],
        "trace_id": m.provenance.channel_trace_id,
        "context_mode": job["context_mode"],
        "age_id": "fresh_120",
        "baseline": m.provenance.baseline,
        "ablation_variant": extra.get("ablation_variant"),
        "authority_epoch_s": ev.get("authority_epoch_s"),
        "challenge_time_s": ev.get("challenge_time_s", job["challenge_time_s"]),
        "authority_age_s": ev.get("authority_age_s", job["authority_age_s"]),
        "expected_freshness_band": ev.get("expected_freshness_band", "fresh"),
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
        "energy_propulsion_j": m.energy_propulsion_j,
        "energy_communication_j": m.energy_communication_j,
        "energy_compute_j": m.energy_compute_j,
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


def cluster_key(row):
    return (row["environment_id"], int(row["seed"]))


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


def paired_diffs(rows, metric):
    by = defaultdict(dict)
    for r in rows:
        by[cluster_key(r)][r["baseline"]] = _num(r.get(metric))
    diffs = []
    for ck in sorted(by):
        d = by[ck]
        if "B4" in d and "B4-NoFreshness" in d:
            diffs.append(d["B4"] - d["B4-NoFreshness"])
    return diffs


def write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def e2_fingerprint() -> str:
    return E2_MANIFEST.read_text()


def sanity_validate(rows: list[dict]) -> list[str]:
    fails = []
    if not rows:
        return ["no rows"]
    keyed = defaultdict(dict)
    for r in rows:
        if int(r.get("e2_n_controlled_proposals") or 0) != 1:
            fails.append(f"n_controlled!=1 {r['run_id']}")
        if _num(r.get("authority_epoch_s")) != 180.0:
            fails.append(f"epoch {r['run_id']}")
        if _num(r.get("challenge_time_s")) != 300.0:
            fails.append(f"challenge {r['run_id']}")
        if _num(r.get("authority_age_s")) != 120.0:
            fails.append(f"age {r['run_id']}")
        if r.get("actual_freshness_band") != "fresh":
            fails.append(f"freshness {r['run_id']} got {r.get('actual_freshness_band')}")
        if not _truthy(r.get("local_target_failed")):
            fails.append(f"local still failed {r['run_id']}")
        if _truthy(r.get("global_target_failed")):
            fails.append(f"global still failed {r['run_id']}")
        if _num(r.get("global_change_time")) != 240.0:
            fails.append(f"recovery time {r['run_id']}")
        if not _truthy(r.get("global_state_changed")):
            fails.append(f"no change {r['run_id']}")
        if _truthy(r.get("mission_beneficial")):
            fails.append(f"oracle beneficial {r['run_id']}")
        if _truthy(r.get("violates_frozen_risk")):
            fails.append(f"hard-risk {r['run_id']}")
        if _num(r.get("local_snapshot_time")) != 180.0:
            fails.append(f"snapshot time {r['run_id']}")
        if not _truthy(r.get("conditional_ok_local")):
            fails.append(f"local cond_ok false {r['run_id']}")
        keyed[cluster_key(r)][r["baseline"]] = r
    for k, d in keyed.items():
        if "B4" in d and "B4-NoFreshness" in d:
            a, b = d["B4"], d["B4-NoFreshness"]
            if a.get("trace_id") != b.get("trace_id"):
                fails.append(f"trace mismatch {k}")
            for field in (
                "local_snapshot_version",
                "local_target_failed",
                "local_segment_owner",
                "segment_id",
                "global_target_failed",
                "global_segment_owner",
                "global_change_time",
            ):
                if a.get(field) != b.get(field):
                    fails.append(f"mismatch {field} {k}")
            if a.get("freshness_mode") == b.get("freshness_mode"):
                fails.append(f"freshness_mode identical {k}")
    return fails[:40]


def classify_case(rows: list[dict]) -> tuple[str, str, str]:
    by = defaultdict(dict)
    for r in rows:
        by[cluster_key(r)][r["baseline"]] = r
    b4_exec = []
    nf_exec = []
    for d in by.values():
        if "B4" not in d or "B4-NoFreshness" not in d:
            continue
        b4_exec.append(_num(d["B4"].get("obsolete_reassignment_execution")))
        nf_exec.append(_num(d["B4-NoFreshness"].get("obsolete_reassignment_execution")))
    b4_m = float(np.mean(b4_exec)) if b4_exec else float("nan")
    nf_m = float(np.mean(nf_exec)) if nf_exec else float("nan")
    f1 = (
        "Fresh authority does not guarantee fresh mission state. In the controlled "
        "early-change condition, both B4 and the same governor without freshness "
        "execute the reassignment because the authority remains within its Fresh "
        "window even though the local mission snapshot is already obsolete."
    )
    lim = (
        "Freshness bounds the lifetime of delegated execution authority; it does not "
        "detect arbitrary mission-state changes within that lifetime."
    )
    if b4_m >= 1.0 - 1e-9 and nf_m >= 1.0 - 1e-9:
        return "F1", f1, lim
    if b4_m <= 1e-9 and nf_m >= 1.0 - 1e-9:
        return "F2", "B4 blocked while FRESH; inspect reason codes before attributing freshness.", lim
    if b4_m <= 1e-9 and nf_m <= 1e-9:
        return "F3", "Both variants blocked; scenario or governor path requires investigation.", lim
    return "F2", "Mixed FRESH outcomes; inspect reason codes before attributing freshness.", lim


def write_manifest(path: Path, *, sha: str, n: int, traces: dict, case: str):
    path.write_text(
        f"""# E2-F early-state-change falsification

- git_commit: `{sha}`
- parent_sha: `{PARENT_SHA}`
- campaign: `{CAMPAIGN}`
- n_runs: {n} (expect 60 = 3 env × 10 seeds × 1 context × 1 age × 2 variants)
- policy_version: `{PAPER_POLICY_VERSION}`
- utility_freeze_id: `{UTILITY_FREEZE_ID}`
- safe_useful_retention_freeze_id: `{SAFE_USEFUL_RETENTION_FREEZE_ID}`
- outage: 200–1200 s
- authority_epoch_s: {AUTHORITY_EPOCH_S}
- recovery_time_s: {E2F_RECOVERY_TIME_S} (authority age {E2F_CHANGE_AGE_S} s)
- proposal_time_s: {E2F_CHALLENGE_TIME_S} (authority age {E2F_CHALLENGE_AGE_S} s)
- expected_freshness: fresh
- proposer: auv_06
- target: auv_05
- variants: B4, B4-NoFreshness (`b4_no_freshness_v1`)
- bootstrap: cluster-preserving over 30 (environment, seed) units, {N_BOOT} resamples, seed {BOOT_SEED}
- traces: `{json.dumps(traces)}`
- CASE: {case}

Does not overwrite E1 or E2 production artifacts. Do not retune 180/400/900.
"""
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sanity", action="store_true")
    args = ap.parse_args()
    validate_freshness_table()
    e2_before = e2_fingerprint()

    out = ROOT / "results" / CAMPAIGN
    paper = out / "paper"
    out.mkdir(parents=True, exist_ok=True)
    paper.mkdir(parents=True, exist_ok=True)

    seeds = [0] if args.sanity else SEEDS
    envs = ENV_FILES[:1] if args.sanity else ENV_FILES
    variants = VARIANTS
    all_rows = []
    trace_map = {}
    workers = resolve_simpy_workers()
    sha = git_commit(ROOT)
    print(f"[e2f] workers={workers} sanity={args.sanity} git={sha}", flush=True)

    for env_path in envs:
        cfg = load_e2f_cfg(env_path)
        env_id = str(cfg.acoustic.environment_id)
        print(f"[e2f] env={env_id}", flush=True)
        tids = verify_traces(cfg, seeds)
        trace_map[env_id] = {str(k): v for k, v in tids.items()}
        jobs = iter_jobs(cfg, tids, env_id, seeds, variants)
        payloads = run_simpy_jobs(jobs, max_workers=workers)
        for job, payload in zip(jobs, payloads):
            m = metrics_from_worker(payload)
            all_rows.append(row_from(m, job))

    if e2_fingerprint() != e2_before:
        raise SystemExit("E2 artifacts were modified; aborting")

    fails = sanity_validate(all_rows)
    if fails:
        (out / "e2f_validation.md").write_text("# E2-F sanity FAILED\n\n" + "\n".join(f"- {x}" for x in fails) + "\n")
        raise SystemExit("E2-F sanity failed:\n" + "\n".join(fails))

    if args.sanity:
        (out / "e2f_validation.md").write_text(
            "# E2-F sanity PASSED (dry-run subset)\n\n"
            f"n_runs={len(all_rows)}\n"
            "Full 60-run campaign was not started by --sanity.\n"
        )
        write_csv(out / "e2f_sanity_raw.csv", all_rows)
        print("[e2f] sanity passed", flush=True)
        return

    rng = np.random.default_rng(BOOT_SEED)
    write_csv(out / "e2f_raw.csv", all_rows)
    (out / "e2f_raw.json").write_text(json.dumps(all_rows, indent=2, default=str))
    write_csv(out / "e2f_event_log.csv", all_rows)

    paired_rows = []
    for metric in (
        "obsolete_reassignment_execution",
        "ownership_override_of_available_target",
        "controlled_reassignment_allowed",
        "controlled_false_denial",
        "mission_utility",
        "hard_safety_violation_count",
        "safe_useful_retention",
    ):
        diffs = paired_diffs(all_rows, metric)
        m, lo, hi = bootstrap_mean_ci(diffs, rng)
        paired_rows.append(
            {
                "metric": metric,
                "n_clusters": len(diffs),
                "b4_minus_nofreshness_mean": m,
                "ci95_lo": lo,
                "ci95_hi": hi,
                "all_clusters_identical": bool(len(set(np.round(diffs, 12))) <= 1),
            }
        )
    write_csv(out / "e2f_paired_effects.csv", paired_rows)

    def rate(var, metric):
        xs = [_num(r.get(metric)) for r in all_rows if r["baseline"] == var]
        return float(np.mean(xs)) if xs else float("nan")

    b4_obs = rate("B4", "obsolete_reassignment_execution")
    nf_obs = rate("B4-NoFreshness", "obsolete_reassignment_execution")
    b4_ow = rate("B4", "ownership_override_of_available_target")
    nf_ow = rate("B4-NoFreshness", "ownership_override_of_available_target")
    d_obs = next(x for x in paired_rows if x["metric"] == "obsolete_reassignment_execution")
    d_util = next(x for x in paired_rows if x["metric"] == "mission_utility")
    hs = float(np.mean([_num(r["hard_safety_violation_count"]) for r in all_rows]))

    def codes(var):
        sub = [r for r in all_rows if r["baseline"] == var]
        return sorted({f"{r.get('decision')}|{r.get('reason_code')}" for r in sub})

    case, claim, limitation = classify_case(all_rows)
    write_manifest(out / "e2f_manifest.md", sha=sha, n=len(all_rows), traces=trace_map, case=case)
    write_manifest(paper / "e2f_manifest.md", sha=sha, n=len(all_rows), traces=trace_map, case=case)

    primary = [
        {
            "context_mode": E2F_CONTEXT,
            "authority_age_s": E2F_CHALLENGE_AGE_S,
            "recovery_time_s": E2F_RECOVERY_TIME_S,
            "challenge_time_s": E2F_CHALLENGE_TIME_S,
            "B4_obsolete": b4_obs,
            "NF_obsolete": nf_obs,
            "delta_obsolete": d_obs["b4_minus_nofreshness_mean"],
            "B4_ownership_override": b4_ow,
            "NF_ownership_override": nf_ow,
            "B4_decisions": ";".join(codes("B4")),
            "NF_decisions": ";".join(codes("B4-NoFreshness")),
            "CASE": case,
        }
    ]
    write_csv(paper / "e2f_primary_table.csv", primary)
    (paper / "e2f_primary_table.md").write_text(
        "# E2-F primary table\n\n"
        f"| context | age | B4 obsolete | NF obsolete | Δ | B4 override | NF override | CASE |\n"
        f"|---|---:|---:|---:|---:|---:|---:|---|\n"
        f"| {E2F_CONTEXT} | 120 | {b4_obs:.3f} | {nf_obs:.3f} | {d_obs['b4_minus_nofreshness_mean']:.3f} "
        f"| {b4_ow:.3f} | {nf_ow:.3f} | {case} |\n"
    )

    (out / "e2f_case.md").write_text(
        f"""# E2-F case

CASE {case}

Supported claim:
{claim}

Limitation:
{limitation}

B4 obsolete_reassignment_execution: {b4_obs:.4f}
NoFreshness obsolete_reassignment_execution: {nf_obs:.4f}
paired Δ obsolete (B4 − NF): {d_obs['b4_minus_nofreshness_mean']:.4f}  95% CI [{d_obs['ci95_lo']:.4f}, {d_obs['ci95_hi']:.4f}]
all_clusters_identical: {d_obs['all_clusters_identical']}

B4 ownership_override: {b4_ow:.4f}
NoFreshness ownership_override: {nf_ow:.4f}

B4 decisions: {codes("B4")}
NoFreshness decisions: {codes("B4-NoFreshness")}

Hard-safety violation count (mean): {hs:.6f}
Mission utility paired B4 − NoFreshness: {d_util['b4_minus_nofreshness_mean']:.4f} [{d_util['ci95_lo']:.4f}, {d_util['ci95_hi']:.4f}]

n_runs={len(all_rows)}
bootstrap_seed={BOOT_SEED}
"""
    )
    (out / "e2f_validation.md").write_text(
        f"""# E2-F validation PASSED

- n_runs: {len(all_rows)}
- authority_epoch_s = 180
- global recovery t = 240
- proposal t = 300, authority age = 120, B4 freshness = FRESH
- local_target_failed remains true; global_target_failed is false
- local snapshot used for conditional_ok; oracle uses global world
- exactly one controlled proposal per run
- paired variants share snapshot, global state, and trace
- E2 production artifacts unchanged
- CASE {case}
"""
    )
    print(f"[e2f] done n={len(all_rows)} CASE {case}", flush=True)


if __name__ == "__main__":
    main()
