#!/usr/bin/env python3
"""E4 DEV-sanity: campaign machinery only. Frozen 40/60/60/300/600. No TEST. No Pareto."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from omegaconf import OmegaConf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_e1_pilot_v3 import ENV_FILES, load_base_cfg  # noqa: E402
from run_e2_authority_age_context import PRODUCTION_TRACES, verify_traces  # noqa: E402
from cgea.experiments.e4_dev_sanity import CELLS, DEV_SEEDS, METHODS, TEST_SEEDS_FORBIDDEN, first_stale_type  # noqa: E402
from cgea.experiments.parallel import cfg_to_container, metrics_from_worker, resolve_simpy_workers, run_simpy_jobs  # noqa: E402
from cgea.types import git_commit  # noqa: E402

CAMPAIGN = os.environ.get("CGEA_CAMPAIGN", "e4_dev_sanity")
OUTAGE_START = 200.0
OUTAGE_END = 1200.0
OUT = ROOT / "results" / "e4_dev_sanity"

EVIDENCE_REASONS = {
    "DENY_MISSING_EVIDENCE",
    "DENY_STALE_EVIDENCE",
    "DENY_INVALID_EVIDENCE",
    "DEFER_EVIDENCE_REFRESH",
}


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
    e4 = OmegaConf.load(ROOT / "configs" / "experiment" / "e4_dev_sanity.yaml")
    cfg.experiment = OmegaConf.merge(cfg.experiment, e4)
    cfg.experiment.name = CAMPAIGN
    cfg.experiment.log_consequential_decisions = True
    cfg.scenario.outage_disabled = False
    cfg.scenario.outage_start_s = OUTAGE_START
    cfg.scenario.outage_end_s = OUTAGE_END
    cfg.force_regenerate_trace = False
    cfg.forbid_trace_generation = True
    return cfg


def iter_jobs(cfg, trace_ids, environment_id, seeds, cell_ids, methods):
    jobs = []
    e2_base = OmegaConf.to_container(cfg.experiment.e2_authority_age_context, resolve=True)
    for seed in seeds:
        for cell_id in cell_ids:
            spec = CELLS[cell_id]
            for baseline, ttl, method_id in methods:
                e2 = dict(e2_base)
                e2.update(
                    {
                        "enabled": True,
                        "challenge_time_s": spec["challenge_time_s"],
                        "authority_age_s": spec["authority_age_s"],
                        "expected_freshness": spec["expected_freshness"],
                        "context_mode": spec["context_mode"],
                        "change_age_s": spec["change_age_s"],
                        "age_id": f"e4_{cell_id}",
                        "lease_ttl_s": float(ttl) if ttl is not None else None,
                        "e4_cell": {
                            "id": cell_id,
                            "name": spec["name"],
                            "peer_refresh_s": spec["peer_refresh_s"],
                            "controlled_action": spec["controlled_action"],
                        },
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
                        "cell_id": cell_id,
                        "method_id": method_id,
                        "lease_ttl_s": ttl,
                        "cfg": cfg_to_container(cfg_run),
                    }
                )
    return jobs


def row_from(m, job: dict) -> dict:
    extra = m.extra
    ev = dict(extra.get("e2_controlled_event") or {})
    eval_ = ev.get("evidence_evaluation") or {}
    return {
        "run_id": f"{job['environment_id']}|{job['seed']}|{job['cell_id']}|{job['method_id']}",
        "environment_id": job["environment_id"],
        "seed": job["seed"],
        "trace_id": m.provenance.channel_trace_id,
        "expected_trace_id": job["expected_trace_id"],
        "cell_id": job["cell_id"],
        "method_id": job["method_id"],
        "baseline": m.provenance.baseline,
        "git_commit": m.provenance.git_commit,
        "n_controlled": extra.get("e2_n_controlled_proposals"),
        "decision": ev.get("decision"),
        "reason_code": ev.get("reason_code"),
        "controlled_action": ev.get("controlled_action"),
        "authority_mode": ev.get("authority_mode"),
        "actual_freshness_band": ev.get("actual_freshness_band"),
        "authority_age_s": ev.get("authority_age_s"),
        "proposal_time_s": ev.get("proposal_time_s"),
        "snapshot_timestamp": ev.get("snapshot_timestamp"),
        "peer_refresh_time_s": ev.get("peer_refresh_time_s"),
        "global_change_time": ev.get("global_change_time"),
        "local_target_failed": ev.get("local_target_failed"),
        "oracle_global_target_failed": ev.get("oracle_global_target_failed"),
        "oracle_global_segment_owner": ev.get("oracle_global_segment_owner"),
        "obsolete_execution": ev.get("obsolete_reassignment_execution"),
        "violates_frozen_risk": ev.get("violates_frozen_risk"),
        "hard_safety_violation_count": extra.get("hard_safety_violation_count"),
        "evidence_policy_id": ev.get("evidence_policy_id"),
        "evidence_ages": json.dumps(ev.get("evidence_ages") or {}, sort_keys=True),
        "evidence_gaps": json.dumps(ev.get("evidence_gaps") or [], sort_keys=True),
        "failed_evidence_type": first_stale_type(eval_),
        "executed": ev.get("executed"),
    }


def expected_b3(cell_id: str, row: dict) -> list[str]:
    fails = []
    if row["reason_code"] == "DENY_HARD_EXPIRY":
        fails.append("S2 DENY_HARD_EXPIRY")
    if row["authority_mode"] != "evidence":
        fails.append("S1 authority_mode not evidence")
    ages = json.loads(row["evidence_ages"] or "{}")
    peer = ages.get("PEER_AVAILABILITY:auv_05")
    seg = ages.get("SEGMENT_ASSIGNMENT:seg")
    # object id is live segment
    peer = next((v for k, v in ages.items() if k.startswith("PEER_AVAILABILITY:")), None)
    seg = next((v for k, v in ages.items() if k.startswith("SEGMENT_ASSIGNMENT:")), None)
    if cell_id == "A":
        if row["decision"] != "ALLOW":
            fails.append(f"A expected ALLOW got {row['decision']} {row['reason_code']}")
        if peer is None or seg is None or peer > 300 or seg > 600:
            fails.append(f"A ages {ages}")
    elif cell_id == "B":
        if row["reason_code"] != "DENY_STALE_EVIDENCE":
            fails.append(f"B expected DENY_STALE got {row['reason_code']}")
        if row["failed_evidence_type"] != "PEER_AVAILABILITY":
            fails.append(f"B failed type {row['failed_evidence_type']}")
        if peer is None or peer <= 300 or (seg is not None and seg >= 600):
            fails.append(f"B ages {ages}")
    elif cell_id == "C":
        if row["reason_code"] != "DENY_STALE_EVIDENCE":
            fails.append(f"C expected DENY_STALE got {row['reason_code']}")
        if row["failed_evidence_type"] != "SEGMENT_ASSIGNMENT":
            fails.append(f"C failed type {row['failed_evidence_type']}")
        if peer is None or seg is None or peer >= 300 or seg <= 600:
            fails.append(f"C ages {ages}")
    elif cell_id == "D":
        if row["decision"] != "ALLOW":
            fails.append(f"D expected ALLOW got {row['decision']} {row['reason_code']}")
        if row["oracle_global_target_failed"] not in (False, 0, "False"):
            fails.append("D oracle still failed")
        if row["local_target_failed"] not in (True, 1, "True"):
            fails.append("D local not failed (S4/S5)")
        if int(row["obsolete_execution"] or 0) != 1:
            fails.append(f"D obsolete flag {row['obsolete_execution']}")
        if row.get("peer_refresh_time_s") not in (None, "", "null"):
            fails.append("D unexpected peer refresh")
    elif cell_id == "E":
        if row["reason_code"] != "DENY_STALE_EVIDENCE":
            fails.append(f"E expected DENY_STALE got {row['reason_code']}")
        if row["failed_evidence_type"] != "PEER_AVAILABILITY":
            fails.append(f"E first stale {row['failed_evidence_type']}")
        if peer is None or seg is None or peer <= 300 or seg <= 600:
            fails.append(f"E ages {ages}")
    elif cell_id == "F":
        if row["decision"] != "ALLOW":
            fails.append(f"F expected ALLOW got {row['decision']} {row['reason_code']}")
        if row["reason_code"] == "DENY_HARD_EXPIRY":
            fails.append("F S2 hard expiry")
        if row["controlled_action"] != "collision_avoidance":
            fails.append(f"F action {row['controlled_action']}")
    if int(row.get("hard_safety_violation_count") or 0) != 0:
        fails.append("S6 hard-safety")
    if int(row.get("n_controlled") or 0) != 1:
        fails.append(f"S8 n_controlled={row.get('n_controlled')}")
    return fails


def expected_hist(cell_id: str, method_id: str, row: dict) -> list[str]:
    fails = []
    if row["reason_code"] in EVIDENCE_REASONS:
        fails.append(f"{method_id} used evidence reason {row['reason_code']}")
    if method_id == "B2" and cell_id == "A" and row["reason_code"] != "DENY_FORBIDDEN":
        fails.append(f"B2 A expected DENY_FORBIDDEN got {row['reason_code']}")
    if method_id == "B1" and cell_id == "A" and row["decision"] != "ALLOW":
        fails.append(f"B1 A expected ALLOW got {row['decision']} {row['reason_code']}")
    if method_id == "B0" and cell_id == "A" and row["decision"] != "ALLOW":
        fails.append(f"B0 A expected ALLOW got {row['decision']} {row['reason_code']}")
    if method_id == "B2" and cell_id == "F" and row["reason_code"] != "DENY_FORBIDDEN":
        fails.append(f"B2 F expected DENY_FORBIDDEN got {row['reason_code']}")
    if int(row.get("n_controlled") or 0) != 1:
        fails.append(f"{method_id} S8 n_controlled")
    if int(row["seed"]) in TEST_SEEDS_FORBIDDEN:
        fails.append("S9 TEST seed")
    if row["trace_id"] != row["expected_trace_id"]:
        fails.append("S10 trace mismatch")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", default="A,B,C,D,E,F")
    ap.add_argument("--seeds", default="0,1,2,3,4")
    args = ap.parse_args()
    cell_ids = [c.strip() for c in args.cells.split(",") if c.strip()]
    seeds = [int(x) for x in args.seeds.split(",") if x.strip()]
    if any(s in TEST_SEEDS_FORBIDDEN for s in seeds):
        raise SystemExit("TEST seeds 5–9 are forbidden")
    parent = git_commit(ROOT)
    OUT.mkdir(parents=True, exist_ok=True)

    all_jobs = []
    for env_path in ENV_FILES:
        cfg = load_cfg(env_path)
        env_id = str(cfg.acoustic.environment_id)
        traces = verify_traces(cfg, seeds)
        all_jobs.extend(iter_jobs(cfg, traces, env_id, seeds, cell_ids, METHODS))

    if len(all_jobs) != len(seeds) * len(cell_ids) * len(METHODS) * len(ENV_FILES):
        raise SystemExit("job count mismatch")

    workers = resolve_simpy_workers()
    print(f"[e4-dev-sanity] jobs={len(all_jobs)} workers={workers} parent={parent}", flush=True)
    payloads = run_simpy_jobs(all_jobs, max_workers=workers)
    rows = [row_from(metrics_from_worker(p), job) for p, job in zip(payloads, all_jobs)]

    mismatches: list[str] = []
    for r in rows:
        if int(r["seed"]) in TEST_SEEDS_FORBIDDEN:
            mismatches.append(f"{r['run_id']}: S9")
        if r["trace_id"] != r["expected_trace_id"]:
            mismatches.append(f"{r['run_id']}: S10")
        if r["method_id"] == "B3":
            mismatches.extend(f"{r['run_id']}: {m}" for m in expected_b3(r["cell_id"], r))
        else:
            mismatches.extend(f"{r['run_id']}: {m}" for m in expected_hist(r["cell_id"], r["method_id"], r))

    write_csv(OUT / "e4_dev_sanity_raw.csv", rows)
    events = []
    for r in rows:
        events.append(
            {
                "run_id": r["run_id"],
                "cell_id": r["cell_id"],
                "method_id": r["method_id"],
                "decision": r["decision"],
                "reason_code": r["reason_code"],
                "evidence_ages": r["evidence_ages"],
                "failed_evidence_type": r["failed_evidence_type"],
                "oracle_global_target_failed": r["oracle_global_target_failed"],
                "obsolete_execution": r["obsolete_execution"],
                "violates_frozen_risk": r["violates_frozen_risk"],
                "proposal_time_s": r["proposal_time_s"],
                "snapshot_timestamp": r["snapshot_timestamp"],
                "peer_refresh_time_s": r["peer_refresh_time_s"],
                "global_change_time": r["global_change_time"],
            }
        )
    write_csv(OUT / "e4_dev_sanity_event_log.csv", events)

    by_cell_method = defaultdict(list)
    for r in rows:
        by_cell_method[(r["cell_id"], r["method_id"])].append(r)

    case_lines = [
        "# E4 DEV-sanity case report",
        "",
        "Not a scientific claim. Frozen budgets 40/60/60/300/600.",
        "",
    ]
    for cell_id, spec in CELLS.items():
        if cell_id not in cell_ids:
            continue
        case_lines.append(f"## Cell {cell_id}: {spec['name']}")
        case_lines.append("")
        case_lines.append(
            f"- challenge={spec['challenge_time_s']} epoch=180 peer_refresh={spec['peer_refresh_s']} "
            f"change_age={spec['change_age_s']} action={spec['controlled_action']}"
        )
        case_lines.append("")
        case_lines.append("| method | decision (mode) | reason | failed type | obsolete |")
        case_lines.append("|--------|-----------------|--------|-------------|----------|")
        for _, _, mid in METHODS:
            rs = by_cell_method[(cell_id, mid)]
            decs = sorted({(x["decision"], x["reason_code"], x["failed_evidence_type"]) for x in rs})
            obs = sorted({str(x["obsolete_execution"]) for x in rs})
            mode = decs[0] if len(decs) == 1 else decs
            case_lines.append(f"| {mid} | {mode} |  |  | {obs} |")
        case_lines.append("")
        b3s = by_cell_method[(cell_id, "B3")]
        if b3s:
            ages = b3s[0]["evidence_ages"]
            case_lines.append(f"B3 example evidence_ages: `{ages}`")
            case_lines.append(
                f"B3 n={len(b3s)} decision={b3s[0]['decision']} reason={b3s[0]['reason_code']} "
                f"failed={b3s[0]['failed_evidence_type']} obsolete={b3s[0]['obsolete_execution']} "
                f"oracle_failed={b3s[0]['oracle_global_target_failed']}"
            )
        case_lines.append("")

    (OUT / "e4_dev_sanity_case.md").write_text("\n".join(case_lines) + "\n")
    verdict = "PASS" if not mismatches else "FAIL"
    val = [
        f"# E4 DEV-sanity validation",
        "",
        f"**Parent SHA:** `{parent}`",
        f"**Verdict:** {verdict}",
        f"**Runs:** {len(rows)}",
        f"**DEV seeds:** {seeds}",
        f"**Mismatches:** {len(mismatches)}",
        "",
        "## S1–S10",
        "",
        f"- S9 TEST seeds: {'PASS' if all(int(r['seed']) not in TEST_SEEDS_FORBIDDEN for r in rows) else 'FAIL'}",
        f"- S10 traces: {'PASS' if all(r['trace_id']==r['expected_trace_id'] for r in rows) else 'FAIL'}",
        f"- S8 controlled=1: {'PASS' if all(int(r['n_controlled'] or 0)==1 for r in rows) else 'FAIL'}",
        f"- S2 B3 no HARD_EXPIRY: {'PASS' if all(r['reason_code']!='DENY_HARD_EXPIRY' for r in rows if r['method_id']=='B3') else 'FAIL'}",
        f"- S6 hard-safety 0: {'PASS' if all(int(r['hard_safety_violation_count'] or 0)==0 for r in rows) else 'FAIL'}",
        "",
    ]
    if mismatches:
        val.append("## Mismatches")
        val.extend(f"- {m}" for m in mismatches[:80])
        if len(mismatches) > 80:
            val.append(f"- … {len(mismatches)-80} more")
    (OUT / "e4_dev_sanity_validation.md").write_text("\n".join(val) + "\n")
    man = [
        f"# E4 DEV-sanity manifest",
        "",
        f"- time: {datetime.now(timezone.utc).isoformat()}",
        f"- parent: `{parent}`",
        f"- campaign: `{CAMPAIGN}`",
        f"- jobs: {len(rows)}",
        f"- seeds: {seeds}",
        f"- cells: {cell_ids}",
        f"- methods: B0 NoFreshness, B1 lease-400, B2 CGEA, B3 Evidence",
        f"- forbid_trace_generation: true",
        f"- verdict: {verdict}",
        f"- no TEST seeds, no budget retune, no paper figure",
        "",
    ]
    (OUT / "e4_dev_sanity_manifest.md").write_text("\n".join(man) + "\n")
    print(f"[e4-dev-sanity] {verdict} mismatches={len(mismatches)} out={OUT}", flush=True)
    if mismatches:
        for m in mismatches[:30]:
            print(" ", m, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
