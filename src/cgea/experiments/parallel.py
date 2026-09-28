"""CPU process-pool for independent SimPy replays. Does not move SimPy to CUDA.

GPU traces must be prebuilt in the parent. Workers only load parquet and replay.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path
from typing import Any, Callable, Iterable

from omegaconf import OmegaConf

from cgea.experiments.runner import run_single
from cgea.metrics import RunMetrics

DEFAULT_WORKERS = 16  # initial fallback only; production default is set after 1/4/8/16/24 bench
BENCH_WORKER_COUNTS = (1, 4, 8, 16, 24)
# parallel.py lives at src/cgea/experiments/; repo root is parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]


def logical_cpu_count() -> int:
    """Return logical CPUs (hardware threads).

    `os.cpu_count()` is not physical core count. On SMT/EPYC this can be 2× cores.
    Worker caps use this value to avoid oversubscribing *schedulable* processors,
    not as a physical-core oracle.
    """
    return int(os.cpu_count() or 1)


def resolve_simpy_workers(cpu_count: int | None = None, requested: int | None = None) -> int:
    """Choose worker count; never exceed logical CPUs. Env and bench file win.

    Production default is `results/simpy_parallel_default.json`, written only after
    the 1/4/8/16/24 worker benchmark. Until that file exists, fall back to 16
    (capped by logical CPU count) or `CGEA_SIMPY_WORKERS`.
    """
    ncpu = int(cpu_count if cpu_count is not None else logical_cpu_count())
    env = os.environ.get("CGEA_SIMPY_WORKERS")
    if env is not None and env.strip() != "":
        return max(1, min(int(env), ncpu))
    if requested is not None:
        return max(1, min(int(requested), ncpu))
    default_path = _REPO_ROOT / "results" / "simpy_parallel_default.json"
    if default_path.is_file():
        payload = json.loads(default_path.read_text())
        return max(1, min(int(payload["workers"]), ncpu))
    return max(1, min(DEFAULT_WORKERS, ncpu))


def cfg_to_container(cfg: Any) -> dict[str, Any]:
    return OmegaConf.to_container(cfg, resolve=True)  # type: ignore[return-value]


def _init_simpy_worker() -> None:
    # Replay-only: do not attach to the H100. Keep BLAS single-threaded per process.
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")


def execute_simpy_job(job: dict[str, Any]) -> dict[str, Any]:
    """Top-level worker entry (must be picklable under spawn)."""
    t0 = time.perf_counter()
    cfg = OmegaConf.create(job["cfg"])
    cfg.forbid_trace_generation = True
    cfg.force_regenerate_trace = False
    metrics = run_single(cfg, str(job["baseline"]))
    expected = job.get("expected_trace_id")
    if expected and metrics.provenance.channel_trace_id != expected:
        raise RuntimeError(
            f"Trace mismatch: expected {expected}, got {metrics.provenance.channel_trace_id}"
        )
    return {
        "seed": int(job["seed"]),
        "outage": job.get("outage"),
        "baseline": str(job["baseline"]),
        "expected_trace_id": expected,
        "elapsed_s": time.perf_counter() - t0,
        "pid": os.getpid(),
        "metrics": metrics.model_dump(mode="json"),
    }


def metrics_from_worker(payload: dict[str, Any]) -> RunMetrics:
    return RunMetrics.model_validate(payload["metrics"])


def run_simpy_jobs(
    jobs: list[dict[str, Any]],
    *,
    max_workers: int,
    on_complete: Callable[[int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Run independent (seed, outage, baseline) jobs. Order of `jobs` is preserved."""
    if not jobs:
        return []
    workers = max(1, int(max_workers))
    if workers == 1:
        out: list[dict[str, Any]] = []
        for i, job in enumerate(jobs):
            payload = execute_simpy_job(job)
            if on_complete:
                on_complete(i, payload)
            out.append(payload)
        return out

    ctx = get_context("spawn")
    results: list[dict[str, Any] | None] = [None] * len(jobs)
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=_init_simpy_worker,
    ) as pool:
        future_to_index = {pool.submit(execute_simpy_job, job): i for i, job in enumerate(jobs)}
        for fut in as_completed(future_to_index):
            i = future_to_index[fut]
            payload = fut.result()
            results[i] = payload
            if on_complete:
                on_complete(i, payload)
    missing = [i for i, r in enumerate(results) if r is None]
    if missing:
        raise RuntimeError(f"SimPy pool returned no result for job indices {missing}")
    return [r for r in results if r is not None]


class CpuSampler:
    """Background /proc/stat sampler. Mean busy fraction over the window."""

    def __init__(self, interval_s: float = 0.25) -> None:
        self.interval_s = interval_s
        self.samples: list[float] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _idle_and_total() -> tuple[int, int]:
        with open("/proc/stat", encoding="utf-8") as f:
            parts = f.readline().split()
        nums = [int(x) for x in parts[1:]]
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        return idle, sum(nums)

    def start(self) -> None:
        self.samples.clear()
        self._stop.clear()
        prev = self._idle_and_total()

        def loop() -> None:
            nonlocal prev
            while not self._stop.wait(self.interval_s):
                cur = self._idle_and_total()
                didle = cur[0] - prev[0]
                dtot = cur[1] - prev[1]
                prev = cur
                if dtot > 0:
                    self.samples.append(max(0.0, min(1.0, 1.0 - didle / dtot)))

        self._thread = threading.Thread(target=loop, name="cpu-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> float:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if not self.samples:
            return float("nan")
        return float(sum(self.samples) / len(self.samples))


def pick_stable_workers(rows: Iterable[dict[str, Any]], cpu_count: int) -> int:
    """Pick workers after the 1/4/8/16/24 bench. `cpu_count` is logical CPUs."""
    eligible = [r for r in rows if int(r["workers"]) <= cpu_count]
    if not eligible:
        return min(DEFAULT_WORKERS, cpu_count)
    best_wall = min(float(r["wall_s"]) for r in eligible)
    close = [r for r in eligible if float(r["wall_s"]) <= best_wall * 1.05]
    close.sort(key=lambda r: (-float(r["efficiency"]), int(r["workers"])))
    return int(close[0]["workers"])
