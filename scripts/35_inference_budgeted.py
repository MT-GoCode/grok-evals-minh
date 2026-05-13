#!/usr/bin/env python3
"""Budget-capped sequential inference driver for AndroidBench.

Runs vendor's inference harness one task at a time and stops the moment
cumulative xAI spend (read from each task's trajectory) crosses BUDGET_USD
(default $10.50). Any task that already has a patch in the run dir is
skipped on resume.

Usage:
  scripts/35_inference_budgeted.py [--run-name NAME] [--budget USD] [--start-from ID]

Why sequential and not --workers N?
  --workers parallelises tasks; we'd risk launching a task that pushes us
  past the budget before we can react. Sequential gives us a clean
  cancellation point between tasks.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR = REPO_ROOT / "vendor" / "AndroidBench"
PY = VENDOR / ".venv" / "bin" / "python"
TASKS_DIR = VENDOR / "dataset" / "tasks"
OUT_DIR = VENDOR / "out"
MODEL = os.environ.get("MODEL", "xai/grok-4.3")


def all_task_ids() -> list[str]:
    return sorted(p.name for p in TASKS_DIR.iterdir() if p.is_dir() and (p / "task.yaml").exists())


def trajectory_cost(run_dir: Path, instance_id: str) -> float | None:
    tp = run_dir / "trajectories" / f"{instance_id}.json"
    if not tp.exists():
        return None
    try:
        t = json.loads(tp.read_text())
    except Exception:
        return None
    info = t.get("info", {}) or {}
    ms = (info.get("extra_info", {}) or {}).get("model_stats") or info.get("model_stats") or {}
    c = ms.get("instance_cost")
    return float(c) if isinstance(c, (int, float)) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", default="full_run_v1")
    ap.add_argument("--budget", type=float, default=10.50, help="USD; stop before launching a task if total >= this")
    ap.add_argument("--start-from", default=None, help="Optional task id to resume from (alphabetic order otherwise)")
    ap.add_argument("--max-tasks", type=int, default=None, help="Optional safety cap on number of tasks attempted")
    args = ap.parse_args()

    run_dir = OUT_DIR / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    task_ids = all_task_ids()
    if args.start_from and args.start_from in task_ids:
        task_ids = task_ids[task_ids.index(args.start_from):]

    # Skip any task that already has a patch (resume support)
    patches_dir = run_dir / "patches"
    already_done = {p.stem for p in patches_dir.glob("*.patch")} if patches_dir.is_dir() else set()

    total = 0.0
    # Pre-sum cost from any prior trajectories in this run dir
    for inst in already_done:
        c = trajectory_cost(run_dir, inst)
        if c is not None:
            total += c

    print(f"[start] run_name={args.run_name} budget=${args.budget:.2f} resume_done={len(already_done)} resume_spend=${total:.4f}")
    started = 0
    for inst in task_ids:
        if inst in already_done:
            continue
        if total >= args.budget:
            print(f"[stop] cumulative spend ${total:.4f} >= budget ${args.budget:.2f}")
            break
        if args.max_tasks is not None and started >= args.max_tasks:
            print(f"[stop] reached --max-tasks={args.max_tasks}")
            break

        remaining = args.budget - total
        print(f"\n[task {started+1}] {inst}   running_total=${total:.4f}   remaining=${remaining:.4f}")
        cmd = [
            str(PY), "-m", "harness.inference.androidbench",
            "--instance", inst,
            "--workers", "1",
            "--model", MODEL,
            "--run-name", args.run_name,
            "--images", "local",
            "--skip-existing",
        ]
        t0 = time.perf_counter()
        rc = subprocess.run(cmd, cwd=str(VENDOR)).returncode
        elapsed = time.perf_counter() - t0
        cost = trajectory_cost(run_dir, inst)
        if cost is not None:
            total += cost
            print(f"[task done] rc={rc} elapsed={elapsed:.1f}s cost=${cost:.4f} total=${total:.4f}")
        else:
            print(f"[task done] rc={rc} elapsed={elapsed:.1f}s cost=UNKNOWN (no trajectory) total=${total:.4f}")
        started += 1

    print(f"\n[summary] tasks_started={started} cumulative_spend=${total:.4f} run_dir={run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
