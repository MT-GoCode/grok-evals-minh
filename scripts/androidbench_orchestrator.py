"""AndroidBench continuous orchestrator.

Chains batches of 10 tasks through {build, infer, verify}, parses scores into a
master JSONL after each batch, deletes the per-task Docker images between
batches to keep disk usage flat, and stops when any of:
  * All 100 task instances done
  * Total wall-clock since start exceeds DEADLINE_HOURS
  * A sentinel file appears at scripts/.stop_androidbench

Outputs:
  results/androidbench/continuous_<ts>.jsonl       — one row per task, appended
  results/androidbench/continuous_<ts>.summary.json — rolling aggregate
  results/androidbench/orchestrator.log            — append-only progress log

Usage:
  sg docker -c '.venv/bin/python scripts/androidbench_orchestrator.py'

(Has to be invoked under `sg docker` so subprocesses inherit the docker group.)
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import time
import yaml
from datetime import datetime, timedelta
from pathlib import Path

DEADLINE_HOURS = 12
BATCH_SIZE = 10
RNG_SEED = 42
TARGET_N = 30  # stop after this many total task results in the master JSONL

REPO_ROOT = Path("/home/minh/code/grok-evals")
BENCH = REPO_ROOT / "vendor" / "AndroidBench"
BENCH_PY = BENCH / ".venv" / "bin" / "python"
BENCH_VERIFIER = BENCH / ".venv" / "bin" / "verifier"
TASKS_DIR = BENCH / "dataset" / "tasks"
OUT_DIR = BENCH / "out"
RESULTS_DIR = REPO_ROOT / "results" / "androidbench"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

START = datetime.now()
STAMP = START.strftime("%Y%m%d-%H%M%S")
MASTER_JSONL = RESULTS_DIR / f"continuous_{STAMP}.jsonl"
MASTER_SUMMARY = RESULTS_DIR / f"continuous_{STAMP}.summary.json"
LOG_PATH = RESULTS_DIR / "orchestrator.log"
STOP_SENTINEL = REPO_ROOT / "scripts" / ".stop_androidbench"

DEADLINE = START + timedelta(hours=DEADLINE_HOURS)


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def all_task_ids() -> list[str]:
    return sorted(
        p.name for p in TASKS_DIR.iterdir()
        if p.is_dir() and (p / "task.yaml").exists()
    )


def done_task_ids_from_master() -> set[str]:
    if not MASTER_JSONL.exists():
        return set()
    out: set[str] = set()
    with MASTER_JSONL.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.add(json.loads(line)["task_id"])
            except Exception:
                pass
    return out


def done_task_ids_from_prior_runs() -> dict[str, dict]:
    """Look at prior per-task vendor runs (e.g. the grok10_n10_seed0 batch)
    and reuse their scores so we don't re-run them."""
    out: dict[str, dict] = {}
    for run_dir in OUT_DIR.iterdir():
        if not run_dir.is_dir():
            continue
        for sj in run_dir.glob("*_scores.json"):
            try:
                d = json.loads(sj.read_text())
            except Exception:
                continue
            for tid, rec in d.items():
                # Only count "real" outcomes — skip placeholder AGENT_NO_PATCH
                # rows that were created by the verifier for un-run instances.
                status = rec.get("status", "")
                if status == "AGENT_NO_PATCH" and rec.get("steps") in (0, "0", None):
                    continue
                if tid not in out:
                    out[tid] = {"run_dir": str(run_dir), "rec": rec}
    return out


def write_filter(task_ids: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(task_ids, f)


def status_to_score(status: str) -> float:
    return 1.0 if status in {"PASSED", "PASSED_FLAKY"} else 0.0


def parse_run_scores(run_name: str, task_ids: list[str]) -> list[dict]:
    run_dir = OUT_DIR / run_name
    data: dict[str, dict] = {}
    for sj in sorted(run_dir.glob("*_scores.json")):
        try:
            data.update(json.loads(sj.read_text()))
        except Exception:
            pass
    rows = []
    for tid in task_ids:
        rec = data.get(tid, {})
        status = rec.get("status", "")
        completion = ""
        patch = run_dir / "patches" / f"{tid}.patch"
        if patch.exists():
            try:
                completion = patch.read_text()
            except Exception:
                completion = ""
        rows.append({
            "task_id": tid,
            "score": status_to_score(status),
            "completion": completion,
            "raw": {
                "run_name": run_name,
                "status": status,
                "diagnostics": rec.get("diagnostics", ""),
                "steps": rec.get("steps", 0),
                "cost_usd": rec.get("cost", "$0.0"),
                "used_tokens": rec.get("used_tokens"),
                "latency_seconds": (rec.get("latency_details") or {}).get(
                    "total_latency_seconds"
                ),
            },
        })
    return rows


def append_rows(rows: list[dict]) -> None:
    with MASTER_JSONL.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    update_summary()


def update_summary() -> None:
    rows = []
    with MASTER_JSONL.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    n = len(rows)
    n_pass = sum(1 for r in rows if r.get("score") == 1.0)
    n_no_patch = sum(1 for r in rows if r["raw"].get("status") == "AGENT_NO_PATCH")
    n_failed_test = sum(1 for r in rows if r["raw"].get("status") == "AGENT_FAILED_TEST")
    summary = {
        "model": "grok-4.3",
        "started_at": STAMP,
        "deadline_hours": DEADLINE_HOURS,
        "n_tasks_done": n,
        "n_passed": n_pass,
        "accuracy": (n_pass / n) if n else None,
        "n_agent_no_patch": n_no_patch,
        "n_agent_failed_test": n_failed_test,
        "elapsed_minutes": round((datetime.now() - START).total_seconds() / 60, 1),
    }
    MASTER_SUMMARY.write_text(json.dumps(summary, indent=2))


def run_batch(task_ids: list[str], run_name: str) -> None:
    """Build → infer → verify a batch sequentially. Subprocesses inherit env."""
    filter_path = BENCH / "data" / f"{run_name}_filter.yaml"
    write_filter(task_ids, filter_path)

    log(f"  STEP 1/3 build images ({len(task_ids)} tasks, workers=1)")
    subprocess.run(
        [str(BENCH_PY), "-m", "utils.docker.generate_docker_images",
         "--tasks-filter", str(filter_path), "--build", "--max_workers", "1"],
        cwd=str(BENCH), check=True,
    )

    log(f"  STEP 2/3 inference (workers=2, model=xai/grok-4.3)")
    subprocess.run(
        [str(BENCH_PY), "-m", "harness.inference.androidbench",
         "--tasks-filter", str(filter_path),
         "--workers", "2",
         "--model", "xai/grok-4.3",
         "--run-name", run_name,
         "--images", "local"],
        cwd=str(BENCH), check=True,
    )

    log(f"  STEP 3/3 verifier")
    subprocess.run(
        [str(BENCH_VERIFIER),
         "--tasks-dir", str(TASKS_DIR),
         "--run-name", run_name,
         "--use_local_images"],
        cwd=str(BENCH), check=True,
    )


def cleanup_images(task_ids: list[str]) -> None:
    """Delete per-task Docker images to keep disk flat. Base images kept."""
    for tid in task_ids:
        img = tid.lower()
        try:
            subprocess.run(
                ["docker", "rmi", "-f", img],
                capture_output=True, text=True, check=False,
            )
        except Exception:
            pass
    # also prune dangling
    subprocess.run(["docker", "image", "prune", "-f"], capture_output=True, check=False)


def main() -> int:
    log(f"=== orchestrator START. master_jsonl={MASTER_JSONL.name} deadline={DEADLINE:%H:%M}")

    all_ids = all_task_ids()
    log(f"Total tasks in dataset: {len(all_ids)}")

    # Seed master with prior batch results (so we don't re-run them)
    prior = done_task_ids_from_prior_runs()
    log(f"Inheriting {len(prior)} prior task outcomes from out/*")
    if prior and not MASTER_JSONL.exists():
        rows = []
        for tid, info in prior.items():
            rec = info["rec"]
            status = rec.get("status", "")
            run_dir = Path(info["run_dir"])
            patch = run_dir / "patches" / f"{tid}.patch"
            completion = patch.read_text() if patch.exists() else ""
            rows.append({
                "task_id": tid,
                "score": status_to_score(status),
                "completion": completion,
                "raw": {
                    "run_name": run_dir.name,
                    "status": status,
                    "diagnostics": rec.get("diagnostics", ""),
                    "steps": rec.get("steps", 0),
                    "cost_usd": rec.get("cost", "$0.0"),
                    "used_tokens": rec.get("used_tokens"),
                    "latency_seconds": (rec.get("latency_details") or {}).get(
                        "total_latency_seconds"
                    ),
                },
            })
        append_rows(rows)

    done = done_task_ids_from_master()
    log(f"Already done (in master): {len(done)}")

    rng = random.Random(RNG_SEED)
    remaining = [t for t in all_ids if t not in done]
    rng.shuffle(remaining)

    while remaining:
        if STOP_SENTINEL.exists():
            log("STOP sentinel detected. Halting.")
            break
        if datetime.now() >= DEADLINE:
            log(f"DEADLINE reached ({DEADLINE_HOURS}h). Halting.")
            break
        n_done_now = len(done_task_ids_from_master())
        if n_done_now >= TARGET_N:
            log(f"TARGET_N={TARGET_N} reached (have {n_done_now}). Halting.")
            break

        batch = remaining[:BATCH_SIZE]
        remaining = remaining[BATCH_SIZE:]
        run_name = f"cont_{datetime.now():%Y%m%d_%H%M%S}"
        log(f"--- Batch: {len(batch)} tasks → run_name={run_name}")
        log(f"    ids: {', '.join(batch[:3])}{'...' if len(batch)>3 else ''}")

        try:
            run_batch(batch, run_name)
        except subprocess.CalledProcessError as e:
            log(f"  ! batch subprocess failed: rc={e.returncode}. Recording partials.")

        rows = parse_run_scores(run_name, batch)
        append_rows(rows)
        n_pass = sum(1 for r in rows if r["score"] == 1.0)
        log(f"  ✓ batch done. pass={n_pass}/{len(rows)} (cumulative_n={len(done_task_ids_from_master())})")

        # cleanup task images to keep disk flat
        cleanup_images(batch)

    log("=== orchestrator STOP")
    update_summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
