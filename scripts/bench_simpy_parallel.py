"""CPU process-pool scaling for SimPy replay (not PHY).

Prebuilds/loads GPU traces once, then times the same independent job set at
workers = 1, 4, 8, 16, 24. Writes results/simpy_parallel_bench.json and the
chosen default worker count.

Does not retune CGEA. Uses a distinct experiment.name so paper campaign JSON
is not overwritten.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_e1_pilot_v2 import (  # noqa: E402
    BASELINES,
    OUTAGES,
    SEEDS,
    iter_campaign_jobs,
    load_base_cfg,
    prebuild_traces,
)
from cgea.experiments.parallel import (  # noqa: E402
    BENCH_WORKER_COUNTS,
    CpuSampler,
    pick_stable_workers,
    run_simpy_jobs,
)


def _subset_jobs(jobs: list[dict], max_jobs: int) -> list[dict]:
    if max_jobs <= 0 or max_jobs >= len(jobs):
        return jobs
    # Spread across seeds/baselines; keep one outage (300s) for representative per-run time.
    picked = [j for j in jobs if j["outage"] == "300"]
    if len(picked) >= max_jobs:
        return picked[:max_jobs]
    return jobs[:max_jobs]


def main() -> None:
    max_jobs = int(os.environ.get("CGEA_BENCH_MAX_JOBS", "21"))
    cfg = load_base_cfg()
    cfg.experiment.name = "e1_simpy_parallel_bench"
    out_dir = Path(cfg.paths.results) / "simpy_parallel_bench"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[bench] prebuild/load GPU traces (parent only)", flush=True)
    t_trace = time.perf_counter()
    trace_ids = prebuild_traces(cfg)
    trace_s = time.perf_counter() - t_trace
    print(f"[bench] traces={trace_ids} load_or_gen_s={trace_s:.2f}", flush=True)

    all_jobs = iter_campaign_jobs(cfg, trace_ids)
    jobs = _subset_jobs(all_jobs, max_jobs)
    print(f"[bench] {len(jobs)} SimPy jobs (full campaign would be {len(all_jobs)})", flush=True)

    ncpu = os.cpu_count() or 1
    rows = []
    baseline_wall = None
    for workers in BENCH_WORKER_COUNTS:
        w = min(int(workers), int(ncpu))
        sampler = CpuSampler(interval_s=0.2)
        sampler.start()
        t0 = time.perf_counter()
        payloads = run_simpy_jobs(jobs, max_workers=w)
        wall = time.perf_counter() - t0
        cpu = sampler.stop()
        per_run = [float(p["elapsed_s"]) for p in payloads]
        mean_per_run = sum(per_run) / len(per_run)
        if workers == 1:
            baseline_wall = wall
        speedup = (baseline_wall / wall) if baseline_wall and wall > 0 else float("nan")
        efficiency = speedup / w if w else float("nan")
        rec = {
            "workers": w,
            "n_jobs": len(jobs),
            "wall_s": wall,
            "mean_per_run_s": mean_per_run,
            "min_per_run_s": min(per_run),
            "max_per_run_s": max(per_run),
            "cpu_busy_fraction": cpu,
            "speedup_vs_1": speedup,
            "efficiency": efficiency,
            "ncpu": ncpu,
            "pids": sorted({int(p["pid"]) for p in payloads}),
            "campaign_wall_estimate_s": wall * (len(all_jobs) / len(jobs)),
        }
        rows.append(rec)
        print(
            f"[bench] workers={w} wall={wall:.1f}s per_run={mean_per_run:.2f}s "
            f"cpu={cpu:.2f} speedup={speedup:.2f}x eff={efficiency:.2f} "
            f"campaign_est={rec['campaign_wall_estimate_s']:.0f}s",
            flush=True,
        )

    chosen = pick_stable_workers(rows, ncpu)
    payload = {
        "trace_ids": {str(k): v for k, v in trace_ids.items()},
        "n_jobs_benchmarked": len(jobs),
        "n_jobs_full_campaign": len(all_jobs),
        "outages": list(OUTAGES),
        "baselines": list(BASELINES),
        "seeds": list(SEEDS),
        "rows": rows,
        "chosen_workers": chosen,
        "note": (
            "PHY is not in this loop. Traces are loaded from parquet. "
            "campaign_wall_estimate_s scales this job subset to 126 full-campaign jobs."
        ),
    }
    (out_dir / "simpy_parallel_bench.json").write_text(json.dumps(payload, indent=2) + "\n")
    default_path = Path(cfg.paths.results) / "simpy_parallel_default.json"
    default_path.write_text(json.dumps({"workers": chosen, "from": str(out_dir / "simpy_parallel_bench.json")}, indent=2) + "\n")
    print(f"[bench] chosen_workers={chosen} wrote {out_dir / 'simpy_parallel_bench.json'}", flush=True)


if __name__ == "__main__":
    main()
