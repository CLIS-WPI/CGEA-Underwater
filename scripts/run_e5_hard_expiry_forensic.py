#!/usr/bin/env python3
"""E5-FORENSIC: B4 hard-expiry / LOW_RISK action audit.

FORENSIC REPLAY — NOT NEW SCIENTIFIC EXPERIMENT.

Does not modify policy, governor thresholds, production results, or manuscript.
Replay is B4-only, 180 cells, forbid_trace_generation, frozen production traces.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from omegaconf import OmegaConf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_e1_pilot_v2 import OUTAGES  # noqa: E402
from run_e1_pilot_v3 import ENV_FILES, load_base_cfg  # noqa: E402
from run_e1_production_n10 import SEEDS  # noqa: E402
from cgea.experiments.parallel import (  # noqa: E402
    cfg_to_container,
    metrics_from_worker,
    resolve_simpy_workers,
    run_simpy_jobs,
)
from cgea.governance import PAPER_POLICY_VERSION
from cgea.mission import LOW_RISK_ACTIONS, ActionType, RiskClass
from cgea.types import git_commit

PROD_DIR = ROOT / "results" / "e1_production_n10"
OUT_DIR = ROOT / "results" / "e5_hard_expiry_forensic"
PROD_CODE_SHA = "85424607cd80fc940be0d3f9bf743dcc9e509f91"
PROD_RESULTS_SHA = "b3466232232ec9863f0c7fd55f25e54ed48e1ab8"
CAMPAIGN = "e5_hard_expiry_forensic"
LOW_TYPES = {a.value for a in LOW_RISK_ACTIONS}
NAMED_TYPES = [
    "collision_avoidance",
    "bounded_path_correction",
    "repeat_sonar_scan",
    "local_inference",
    "hold_station",
    "surfacing_safe_mode",
]
HEADLINE_FLOAT = [
    ("mission_utility", "mission_utility"),
    ("safe_useful_retention", "extra.safe_useful_retention"),
    ("hard_safety_violation_rate", "extra.violation_per_proposal"),
    ("auv_reauth_fraction", "extra.auv_reauth_fraction"),
]
HEADLINE_INT = [
    ("hard_safety_violation_count", "extra.hard_safety_violation_count"),
    ("semantic_conflicts", "extra.semantic_conflicts_per_mission"),
    ("governance_tx_bytes", "extra.governance_tx_bytes"),
    ("mission_tx_bytes", "extra.mission_tx_bytes"),
]


def _git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _get(d: dict, path: str):
    cur = d
    for p in path.split("."):
        if p == "extra":
            cur = cur.get("extra") or {}
        else:
            cur = cur.get(p)
    return cur


def parse_outage_duration(notes: str | None) -> float | None:
    if not notes:
        return None
    prefix = "outage_duration_s="
    if prefix in notes:
        return float(notes.split(prefix, 1)[1].split()[0])
    return None


def load_production_b4() -> list[dict]:
    rows = []
    for path in sorted((PROD_DIR / "B4").glob("B4_*.json")):
        d = json.loads(path.read_text())
        rows.append(d)
    return rows


def production_aggregate(prod: list[dict]) -> dict:
    n_prop = 0
    n_low = 0
    n_allow = n_deny = n_defer = 0
    by_type = defaultdict(lambda: {"proposed": 0, "ALLOW": 0, "DENY": 0, "DEFER": 0})
    reason_tot = Counter()
    low_deny = 0
    collision = 0
    for d in prod:
        extra = d.get("extra") or {}
        pbt = extra.get("proposed_by_type") or {}
        dbt = extra.get("decision_by_type") or {}
        dbr = extra.get("decision_by_risk_class") or {}
        reasons = extra.get("reason_code_counts") or {}
        for k, v in reasons.items():
            reason_tot[k] += int(v)
        for at, n in pbt.items():
            nn = int(n)
            n_prop += nn
            by_type[at]["proposed"] += nn
            if at in LOW_TYPES:
                n_low += nn
            if at == "collision_avoidance":
                collision += nn
        for at, decs in dbt.items():
            for dec, n in (decs or {}).items():
                by_type[at][dec] += int(n)
        low = dbr.get("low") or {}
        n_allow += int(low.get("ALLOW") or 0) + int((dbr.get("consequential") or {}).get("ALLOW") or 0)
        n_deny += int(low.get("DENY") or 0) + int((dbr.get("consequential") or {}).get("DENY") or 0)
        n_defer += int(low.get("DEFER") or 0) + int((dbr.get("consequential") or {}).get("DEFER") or 0)
        low_deny += int(low.get("DENY") or 0)
    timeline_low_hard = 0
    timeline_disagree = 0
    timeline_n = 0
    for d in prod:
        extra = d.get("extra") or {}
        for ev in extra.get("authority_timeline") or []:
            timeline_n += 1
            age = float(ev.get("authority_age_s") or 0.0)
            fr = str(ev.get("authority_freshness") or ev.get("freshness") or "")
            hard_label = fr == "hard_expired"
            hard_age = age >= 900.0
            if hard_label != hard_age:
                timeline_disagree += 1
            at = ev.get("action_type")
            if at in LOW_TYPES and (hard_label or hard_age):
                timeline_low_hard += 1
    return {
        "n_runs": len(prod),
        "n_prop": n_prop,
        "n_low": n_low,
        "n_allow": n_allow,
        "n_deny": n_deny,
        "n_defer": n_defer,
        "low_deny": low_deny,
        "collision_proposed": collision,
        "by_type": {k: dict(v) for k, v in by_type.items()},
        "reason_tot": dict(reason_tot),
        "timeline_n": timeline_n,
        "timeline_low_hard": timeline_low_hard,
        "timeline_disagree": timeline_disagree,
        "has_all_auv_decision_log": False,
        "has_consequential_decision_log": any(
            (d.get("extra") or {}).get("consequential_decision_log") for d in prod
        ),
    }


def missing_production_fields() -> list[str]:
    return [
        "per-AUV proposal logs for all 12 AUVs (authority_timeline is auv_08 only)",
        "proposal time, connectivity, and authority_age for every LOW_RISK decision",
        "reason_code on each LOW_RISK event (only aggregate reason_code_counts)",
        "freshness label on each LOW_RISK event",
        "consequential_decision_log is empty / unused in production extra",
    ]


def attribution(reason: str, freshness: str, risk: str, connectivity: str) -> str:
    if risk != RiskClass.LOW.value:
        return "not_low_risk"
    if reason == "DENY_FORBIDDEN" and freshness == "hard_expired":
        return "A_hard_expiry_capsule_contraction"
    if reason == "DENY_GEOFENCE":
        return "B_geofence"
    if reason == "DENY_ENERGY":
        return "C_energy"
    if reason == "DENY_CONFIDENCE":
        return "D_confidence"
    if "RECOVERING" in reason or connectivity == "recovering":
        return "E_RECOVERING"
    if reason == "DENY_HARD_EXPIRY":
        return "F_DENY_HARD_EXPIRY_consequential_gate"
    return f"F_other:{reason}"


def iter_replay_jobs(traces: dict) -> list[dict]:
    jobs = []
    for env_path in ENV_FILES:
        cfg = load_base_cfg(env_path)
        env_id = str(cfg.acoustic.environment_id)
        for seed in SEEDS:
            for outage_name, ocfg in OUTAGES.items():
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
                            "forensic_log_all_decisions": True,
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
                        "baseline": "B4",
                        "environment_id": env_id,
                        "expected_trace_id": traces[env_id][str(seed)],
                        "cfg": cfg_to_container(cfg_run),
                    }
                )
    return jobs


def index_production(prod: list[dict]) -> dict:
    out = {}
    for d in prod:
        p = d["provenance"]
        dur = parse_outage_duration(p.get("notes"))
        key = (p["environment_id"], int(p["random_seed"]), float(dur))
        out[key] = d
    return out


def near_eq(a, b, atol=1e-9, rtol=1e-12) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return a == b
    return abs(fa - fb) <= atol + rtol * max(abs(fa), abs(fb))


def validate_replay(job: dict, replay: dict, prod: dict) -> list[str]:
    fails = []
    extra_r = replay.get("extra") or {}
    extra_p = prod.get("extra") or {}
    if replay["provenance"]["channel_trace_id"] != prod["provenance"]["channel_trace_id"]:
        fails.append("trace_id")
    for name, path in HEADLINE_FLOAT:
        if not near_eq(_get(replay, path), _get(prod, path), atol=1e-8):
            fails.append(f"{name}:{_get(replay, path)!r}!={_get(prod, path)!r}")
    for name, path in HEADLINE_INT:
        if int(_get(replay, path) or 0) != int(_get(prod, path) or 0):
            fails.append(f"{name}:{_get(replay, path)!r}!={_get(prod, path)!r}")
    def _counts(d):
        return {k: int(v) for k, v in (d or {}).items() if int(v) != 0}

    def _nested_dec(d):
        out = {}
        for at, decs in (d or {}).items():
            nd = {k: int(v) for k, v in (decs or {}).items() if int(v) != 0}
            if nd:
                out[at] = nd
        return out

    if _counts(extra_r.get("reason_code_counts")) != _counts(extra_p.get("reason_code_counts")):
        fails.append("reason_code_counts")
    if _counts(extra_r.get("proposed_by_type")) != _counts(extra_p.get("proposed_by_type")):
        fails.append("proposed_by_type")
    if _nested_dec(extra_r.get("decision_by_type")) != _nested_dec(extra_p.get("decision_by_type")):
        fails.append("decision_by_type")
    if _nested_dec(extra_r.get("decision_by_risk_class")) != _nested_dec(extra_p.get("decision_by_risk_class")):
        fails.append("decision_by_risk_class")
    if extra_r.get("decisions_allow") != extra_p.get("decisions_allow"):
        fails.append("decisions_allow")
    if extra_r.get("decisions_deny") != extra_p.get("decisions_deny"):
        fails.append("decisions_deny")
    if extra_r.get("decisions_defer") != extra_p.get("decisions_defer"):
        fails.append("decisions_defer")
    return fails


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def historical_semantics() -> tuple[str, str]:
    hist = _git(["show", f"{PROD_CODE_SHA}:src/cgea/governance/__init__.py"])
    cur = (ROOT / "src/cgea/governance/__init__.py").read_text()
    hist_snip = ""
    cur_snip = ""
    for label, text in (("historical", hist), ("current", cur)):
        i = text.find("if now >= capsule.hard_expiry or freshness == AuthorityFreshness.HARD_EXPIRED:")
        snip = text[i : i + 280] if i >= 0 else "MISSING"
        if label == "historical":
            hist_snip = snip
        else:
            cur_snip = snip
    b2 = (ROOT / "src/cgea/baselines/__init__.py").read_text()
    same_gov = (
        "data[\"allowed_actions\"] = [capsule.fallback_action]" in hist
        and "data[\"allowed_actions\"] = [capsule.fallback_action]" in cur
        and "if freshness == AuthorityFreshness.HARD_EXPIRED and proposal.risk_class == RiskClass.CONSEQUENTIAL:"
        in hist
        and "if freshness == AuthorityFreshness.HARD_EXPIRED and proposal.risk_class == RiskClass.CONSEQUENTIAL:"
        in cur
        and "if action in eff.forbidden_actions:" in hist
        and hist.find("HARD_EXPIRED and proposal.risk_class")
        < hist.find("if action in eff.forbidden_actions:")
    )
    b2_unrestricted = "class B2Unrestricted" in b2 and "ReasonCode.ALLOW_AUTHORIZED" in b2
    status = "SAME" if same_gov else "DIFFERENT"
    evidence = (
        f"Historical B4 uses ExecutionGovernor.decide + contract_capsule at {PROD_CODE_SHA}. "
        f"HARD_EXPIRED keeps only fallback_action and forbids every other ActionType; "
        f"CONSEQUENTIAL is DENY_HARD_EXPIRY before the forbidden check; LOW_RISK then hits DENY_FORBIDDEN. "
        f"Current B4 governor path is the same. Current B2Unrestricted always ALLOW_AUTHORIZED and does not "
        f"implement hard-expiry contraction (comparison requested vs B2 fallback-only is therefore not applicable; "
        f"governor semantics vs historical B4: {status}).\n\n"
        f"HISTORICAL snippet:\n{hist_snip}\n\nCURRENT snippet:\n{cur_snip}\n\n"
        f"B2 unrestricted present: {b2_unrestricted}"
    )
    return status, evidence


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    parent = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    hist_status, hist_ev = historical_semantics()

    prod = load_production_b4()
    assert len(prod) == 180, len(prod)
    pagg = production_aggregate(prod)
    missing = missing_production_fields()
    artifacts_sufficient = False

    traces = json.loads((PROD_DIR / "shared_trace_ids.json").read_text())
    jobs = iter_replay_jobs(traces)
    assert len(jobs) == 180, len(jobs)
    workers = resolve_simpy_workers()
    print(
        f"FORENSIC REPLAY — NOT NEW SCIENTIFIC EXPERIMENT  parent={parent} workers={workers} jobs=180",
        flush=True,
    )

    def progress(i, payload):
        if i % 10 == 0 or i == len(jobs) - 1:
            print(
                f"[e5] {i+1}/{len(jobs)} seed={payload.get('seed')} outage={payload.get('outage')}",
                flush=True,
            )

    payloads = run_simpy_jobs(jobs, max_workers=workers, on_complete=progress)
    prod_idx = index_production(prod)
    val_rows = []
    all_fail = []
    events = []
    for job, payload in zip(jobs, payloads):
        m = metrics_from_worker(payload)
        replay = m.model_dump(mode="json")
        key = (job["environment_id"], int(job["seed"]), float(job["outage_duration_s"]))
        prod_m = prod_idx[key]
        fails = validate_replay(job, replay, prod_m)
        val_rows.append(
            {
                "environment_id": job["environment_id"],
                "seed": job["seed"],
                "outage": job["outage"],
                "outage_duration_s": job["outage_duration_s"],
                "trace_id": replay["provenance"]["channel_trace_id"],
                "n_fails": len(fails),
                "fails": ";".join(fails),
                "mission_utility_replay": replay["mission_utility"],
                "mission_utility_prod": prod_m["mission_utility"],
            }
        )
        if fails:
            all_fail.append((key, fails))
        extra = replay.get("extra") or {}
        for ev in extra.get("forensic_decision_log") or []:
            rec = {
                "environment_id": job["environment_id"],
                "seed": job["seed"],
                "outage": job["outage"],
                "outage_duration_s": job["outage_duration_s"],
                "trace_id": replay["provenance"]["channel_trace_id"],
                **ev,
            }
            rec["hard_expired_label"] = rec.get("authority_freshness") == "hard_expired"
            rec["age_ge_900"] = float(rec.get("authority_age_s") or 0) >= 900.0
            rec["freshness_age_disagree"] = rec["hard_expired_label"] != rec["age_ge_900"]
            rec["is_low_risk"] = rec.get("risk_class") == "low" or rec.get("action_type") in LOW_TYPES
            rec["attribution"] = attribution(
                str(rec.get("reason_code")),
                str(rec.get("authority_freshness")),
                str(rec.get("risk_class")),
                str(rec.get("connectivity")),
            )
            events.append(rec)

    replay_ok = not all_fail
    val_path = OUT_DIR / "e5_replay_validation.md"
    if not replay_ok:
        val_path.write_text(
            "# FORENSIC REPLAY — NOT NEW SCIENTIFIC EXPERIMENT\n\n"
            "STATUS: FAIL — replay does not reproduce frozen B4 headlines. "
            "Do not use replay for forensic inference.\n\n"
            + "\n".join(f"- {k}: {f}" for k, f in all_fail[:50])
        )
        (OUT_DIR / "e5_case.md").write_text(
            "CASE UNDETERMINED — replay validation failed. Production event logs remain insufficient.\n"
        )
        print("REPLAY VALIDATION FAILED; stopping.", flush=True)
        sys.exit(2)

    n_prop = len(events)
    low = [e for e in events if e["is_low_risk"]]
    age900 = [e for e in events if e["age_ge_900"]]
    hard = [e for e in events if e["hard_expired_label"] or e["age_ge_900"]]
    low900 = [e for e in low if e["age_ge_900"]]
    low_hard = [e for e in low if e["hard_expired_label"] or e["age_ge_900"]]
    disagree = [e for e in events if e["freshness_age_disagree"]]
    allow_all = sum(1 for e in events if e["decision"] == "ALLOW")
    deny_all = sum(1 for e in events if e["decision"] == "DENY")
    defer_all = sum(1 for e in events if e["decision"] == "DEFER")
    low_allow_900 = [e for e in low900 if e["decision"] == "ALLOW"]
    low_deny_900 = [e for e in low900 if e["decision"] == "DENY"]
    denied_lr_hard = [
        e
        for e in low
        if e["decision"] == "DENY" and (e["hard_expired_label"] or e["age_ge_900"])
    ]
    contraction = [
        e for e in denied_lr_hard if e["attribution"] == "A_hard_expiry_capsule_contraction"
    ]
    ca900 = [e for e in events if e["action_type"] == "collision_avoidance" and e["age_ge_900"]]
    ca_deny = [e for e in ca900 if e["decision"] == "DENY"]

    if not low_hard:
        case = "H0"
    elif not contraction:
        case = "H1"
    elif not ca_deny:
        case = "H2"
    else:
        case = "H3"

    field = [
        "environment_id",
        "seed",
        "outage",
        "outage_duration_s",
        "t_s",
        "auv_id",
        "action_type",
        "risk_class",
        "decision",
        "reason_code",
        "authority_freshness",
        "authority_age_s",
        "connectivity",
        "supervisor_reachable",
        "hard_expired_label",
        "age_ge_900",
        "freshness_age_disagree",
        "attribution",
        "trace_id",
    ]
    write_csv(OUT_DIR / "e5_low_risk_events.csv", low, field)
    write_csv(OUT_DIR / "e5_hard_expired_events.csv", hard, field)
    write_csv(OUT_DIR / "e5_denied_low_risk_events.csv", denied_lr_hard, field)
    write_csv(OUT_DIR / "e5_replay_raw.csv", events, field)
    write_csv(
        OUT_DIR / "e5_replay_validation.csv",
        val_rows,
        list(val_rows[0].keys()) if val_rows else [],
    )

    by_action = Counter(e["action_type"] for e in contraction)
    by_reason = Counter(e["reason_code"] for e in denied_lr_hard)
    by_attr = Counter(e["attribution"] for e in denied_lr_hard)
    affected_keys = sorted(
        {(e["environment_id"], int(e["seed"]), e["outage"], float(e["outage_duration_s"])) for e in contraction}
    )
    affected_outages = sorted({k[2] for k in affected_keys})
    type_table = []
    for at in NAMED_TYPES + sorted(LOW_TYPES - set(NAMED_TYPES)):
        sub = [e for e in events if e["action_type"] == at]
        sub_low = [e for e in sub if e["is_low_risk"]]
        type_table.append(
            {
                "action_type": at,
                "in_LOW_RISK_ACTIONS": at in LOW_TYPES,
                "n_proposed": len(sub),
                "n_age_ge_900": sum(1 for e in sub if e["age_ge_900"]),
                "n_hard_expired_label": sum(1 for e in sub if e["hard_expired_label"]),
                "ALLOW": sum(1 for e in sub if e["decision"] == "ALLOW"),
                "DENY": sum(1 for e in sub if e["decision"] == "DENY"),
                "DEFER": sum(1 for e in sub if e["decision"] == "DEFER"),
                "DENY_at_age_ge_900": sum(1 for e in sub if e["decision"] == "DENY" and e["age_ge_900"]),
                "contraction_denials": sum(1 for e in contraction if e["action_type"] == at),
            }
        )

    # Hypothetical-fix impact: LOW_RISK denials under contraction could change executed local motion.
    # SUR / hard-safety are consequential-only in extra.safe_useful_* and violation_* .
    possible_metrics = []
    if case in ("H2", "H3"):
        possible_metrics = [
            "mission utility (local path/hold execution can change utility_breakdown terms)",
            "mission completion (if bounded_path_correction denials alter inspection progress)",
            "energy (propulsion if path-correction vs hold substitution)",
        ]
        # SUR/hard-safety/conflicts/gov TX: no logged LOW_RISK contribution
        possible_metrics.append(
            "SUR: not expected from logged definitions (safe_useful_* counts consequential only)"
        )
        possible_metrics.append(
            "hard-safety: not expected (violation counters are consequential-class)"
        )
        possible_metrics.append(
            "semantic conflicts / governance TX / mission TX: no logged dependence on LOW_RISK authorization"
        )

    ca_sentence = (
        "Production B4 denied collision-avoidance proposals under hard expiry."
        if ca_deny
        else "No production B4 collision_avoidance proposal occurred at authority_age >= 900."
    )

    val_path.write_text(
        "\n".join(
            [
                "# FORENSIC REPLAY — NOT NEW SCIENTIFIC EXPERIMENT",
                "",
                "STATUS: PASS",
                "",
                f"- replay runs: {len(jobs)} B4 cells",
                f"- headline + reason/type counters: exact match vs frozen e1_production_n10 B4",
                f"- failed cells: 0",
                f"- forbid_trace_generation: true",
                f"- production traces: results/e1_production_n10/shared_trace_ids.json",
                "",
            ]
        )
    )

    (OUT_DIR / "e5_case.md").write_text(
        "\n".join(
            [
                f"# CASE {case}",
                "",
                {
                    "H0": "NO EXPOSURE — no LOW_RISK proposal while B4 authority was HARD_EXPIRED.",
                    "H1": "EXPOSED BUT NOT DENIED — LOW_RISK proposals under HARD_EXPIRED, none denied due to hard-expiry contraction.",
                    "H2": "LOW-RISK DENIAL, NON-COLLISION — at least one LOW_RISK action denied due to hard-expiry contraction; no collision-avoidance denial.",
                    "H3": "COLLISION AVOIDANCE DENIED — at least one collision_avoidance proposal denied due to hard-expiry contraction.",
                }[case],
                "",
                f"- N(B4 ∧ LOW_RISK ∧ HARD_EXPIRED/age>=900 ∧ DENY ∧ contraction) = {len(contraction)}",
                f"- collision_avoidance at age>=900: {len(ca900)}",
                f"- collision_avoidance denied at age>=900: {len(ca_deny)}",
                f"- {ca_sentence}",
                "",
                "Do not implement a policy fix in this audit.",
                "",
            ]
        )
    )

    (OUT_DIR / "e5_manuscript_consistency.md").write_text(
        "\n".join(
            [
                "# Manuscript consistency (no TeX edits)",
                "",
                "Files checked: `CGEA_Updated_Overleaf_Final.tex` (workspace root) and `cgea-underwater/paper/overleaf/main.tex`.",
                "",
                "## Sentences that are incorrect or incomplete vs implementation",
                "",
                "### CGEA_Updated_Overleaf_Final.tex",
                "",
                "Line 27 (abstract): “A risk-bounded paper policy allows low-risk actions, conditionally permits bounded task reassignment, and forbids four high-impact action classes.”",
                "Issue: hard-expiry `contract_capsule` forbids every ActionType except `fallback_action` (`surfacing_safe_mode`), including LOW_RISK classes. The abstract states low-risk as allowed without the HARD_EXPIRED exception.",
                "",
                "Line 71: “Allowed: low-risk actions such as collision avoidance, bounded path correction, repeated sonar scan, and local inference;”",
                "Issue: these are paper-capsule grants for non-HARD_EXPIRED freshness. At HARD_EXPIRED they are placed in `forbidden_actions`. Collision avoidance is listed as allowed but was never proposed in production B4; path correction and hold_station were denied via DENY_FORBIDDEN after contraction.",
                "",
                "Line 76: “The freshness thresholds are also frozen: aging at 180 s, stale at 400 s, hard expiry at 900 s …”",
                "Issue: does not state that hard expiry replaces the allowed set with only the fallback action rather than only stripping consequential authority.",
                "",
                "Line 161: “encodes bounded execution authority in local capsules, contracts that authority with freshness”",
                "Issue: readers can infer that contraction is limited to consequential authority. Implementation at HARD_EXPIRED admits no LOW_RISK execution except if the action is the configured fallback (surfacing_safe_mode, which is not in LOW_RISK_ACTIONS).",
                "",
                "### cgea-underwater/paper/overleaf/main.tex",
                "",
                "Line 33: “Low-risk inspection actions are allowed when not otherwise expired.”",
                "This is closer to code. Still incomplete: STALE keeps LOW_RISK; HARD_EXPIRED does not. “Expired” is ambiguous between stale and hard expiry.",
                "",
                "Line 35: “Freshness bands … contract allowed sets further.”",
                "True, but does not disclose that HARD_EXPIRED forbids collision avoidance / path correction / hold / scan, not only consequential classes.",
                "",
                "Line 57: “contracts further when stale.”",
                "Stale forbids consequential and keeps LOW_RISK. Hard expiry is a stricter, different contraction. This sentence understates HARD_EXPIRED.",
                "",
            ]
        )
    )

    (OUT_DIR / "e5_summary.md").write_text(
        "\n".join(
            [
                "# E5 hard-expiry / LOW_RISK forensic summary",
                "",
                "FORENSIC REPLAY — NOT NEW SCIENTIFIC EXPERIMENT",
                "",
                f"- audit parent SHA: `{parent}`",
                f"- production code SHA: `{PROD_CODE_SHA}`",
                f"- production results SHA: `{PROD_RESULTS_SHA}`",
                f"- production artifacts sufficient for event-level attribution: no",
                f"- replay required: yes",
                f"- replay validation: PASS",
                f"- HISTORICAL_HARD_EXPIRY_SEMANTICS: {hist_status}",
                f"- CASE: {case}",
                "",
                "## Taxonomy",
                f"- LOW_RISK_ACTIONS: {sorted(LOW_TYPES)}",
                f"- surfacing_safe_mode in LOW_RISK_ACTIONS: {ActionType.SURFACING_SAFE_MODE.value in LOW_TYPES}",
                "",
                "## Required counts (B4 forensic replay, validated against frozen headlines)",
                f"1. total proposals: {n_prop}",
                f"2. total LOW_RISK proposals: {len(low)}",
                f"3. total proposals at authority_age >= 900: {len(age900)}",
                f"4. total LOW_RISK proposals at authority_age >= 900: {len(low900)}",
                f"5. number ALLOW (all proposals): {allow_all}",
                f"6. number DENY (all proposals): {deny_all}",
                f"7. number DEFER (all proposals): {defer_all}",
                "",
                f"- LOW_RISK ALLOW at age>=900: {len(low_allow_900)}",
                f"- LOW_RISK DENY at age>=900: {len(low_deny_900)}",
                f"- freshness vs age>=900 disagreements: {len(disagree)}",
                f"- LOW_RISK under HARD_EXPIRED label or age>=900: {len(low_hard)}",
                f"- contraction denials (A): {len(contraction)}",
                "",
                "## Denied LOW_RISK at hard expiry by action_type",
                json.dumps(dict(by_action), indent=2),
                "",
                "## Denied LOW_RISK at hard expiry by reason_code",
                json.dumps(dict(by_reason), indent=2),
                "",
                "## Attribution",
                json.dumps(dict(by_attr), indent=2),
                "",
                "## Action-type table",
                json.dumps(type_table, indent=2),
                "",
                "## Collision-avoidance check",
                ca_sentence,
                f"- collision_avoidance proposals at age>=900: {len(ca900)}",
                f"- decisions: {Counter(e['decision'] for e in ca900)}",
                f"- reason codes: {Counter(e['reason_code'] for e in ca900)}",
                "",
                "## Affected runs (contraction denials)",
                f"- n_runs: {len(affected_keys)} / 180",
                f"- outages: {affected_outages}",
                f"- cells: {affected_keys}",
                "",
                "## Possible affected manuscript metrics if LOW_RISK were preserved at hard expiry",
                "\n".join(f"- {x}" for x in possible_metrics) or "- none (H0/H1)",
                "",
                "## Production-only aggregates (insufficient event logs)",
                json.dumps({k: pagg[k] for k in pagg if k != "by_type"}, indent=2, default=str),
                "",
                "## Historical evidence",
                hist_ev,
                "",
            ]
        )
    )

    (OUT_DIR / "e5_manifest.md").write_text(
        "\n".join(
            [
                "# E5-FORENSIC manifest",
                "",
                "FORENSIC REPLAY — NOT NEW SCIENTIFIC EXPERIMENT",
                "",
                f"- audit parent SHA (pre-commit): `{parent}`",
                f"- original production provenance SHA: `{PROD_CODE_SHA}`",
                f"- production results commit: `{PROD_RESULTS_SHA}`",
                f"- policy_version: `{PAPER_POLICY_VERSION}` (unchanged)",
                "- scope: B4 only, 3 env × 10 seeds × 6 outages = 180",
                "- production campaign: e1_production_n10 (not modified)",
                "- E2/E2-F/E3/E4 artifacts: not modified",
                "- policy/governor/thresholds: not modified except forensic_log_all_decisions logging",
                "- manuscript: not modified",
                f"- production event artifacts sufficient: no",
                f"- missing: {missing}",
                f"- replay required: yes",
                f"- replay validation: PASS",
                f"- HISTORICAL_HARD_EXPIRY_SEMANTICS: {hist_status}",
                f"- CASE: {case}",
                "",
            ]
        )
    )
    print((OUT_DIR / "e5_case.md").read_text())
    # Per-run JSON dumps contain forensic logs; canonical event tables are the CSVs.
    b4_dump = OUT_DIR / "B4"
    if b4_dump.is_dir():
        n_json = 0
        for p in b4_dump.glob("*.json"):
            p.unlink()
            n_json += 1
        print(f"removed {n_json} replay JSON dumps; events kept in CSVs", flush=True)
    print("wrote", OUT_DIR)


if __name__ == "__main__":
    main()
