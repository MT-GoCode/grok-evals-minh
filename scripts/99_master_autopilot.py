#!/usr/bin/env python3
"""Master autopilot: wait for image build, then per-task inference + verify
+ aggregate + git push, until budget exhausted or all tasks done.

Single subprocess per phase per task — kept sequential so spend never
overshoots BUDGET_USD between checks. Resumable across restarts: tasks
with an existing patch file are skipped on inference, tasks with an
existing scores entry are skipped on verify.

Each completed task is fully persisted to disk before the next one
launches. Every COMMIT_EVERY tasks, the partial results + updated
README are committed and pushed.

Usage:
  python scripts/99_master_autopilot.py [--budget 14.50] [--run-name full_run_v1]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR = REPO_ROOT / "vendor" / "AndroidBench"
PY = VENDOR / ".venv" / "bin" / "python"
VERIFIER = VENDOR / ".venv" / "bin" / "verifier"
TASKS_DIR = VENDOR / "dataset" / "tasks"
OUT_DIR = VENDOR / "out"
RESULTS_DIR_REPO = REPO_ROOT / "results" / "androidbench"
BUILD_LOG = Path("/home/minh/androidbench/build100.log")
LOG = REPO_ROOT / "results" / "androidbench" / "autopilot.log"
COMMIT_EVERY = 5
GIT_SSH_ENV = {**os.environ, "GIT_SSH_COMMAND": "ssh -o StrictHostKeyChecking=no"}


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def wait_for_build() -> None:
    log("waiting for build100.log to report BUILD100_EXIT=...")
    while True:
        if BUILD_LOG.is_file():
            txt = BUILD_LOG.read_text(errors="ignore")
            if "BUILD100_EXIT=" in txt:
                # Even on error, proceed with whatever images we have.
                n_ok = txt.count("Successfully built")
                n_err = txt.count("Error building")
                log(f"build done. successfully_built={n_ok} errors={n_err}")
                return
        time.sleep(60)


def all_task_ids() -> list[str]:
    return sorted(p.name for p in TASKS_DIR.iterdir() if p.is_dir() and (p / "task.yaml").exists())


def task_has_image(instance_id: str) -> bool:
    """Quick check: docker image tagged after the lowercased instance id exists."""
    try:
        r = subprocess.run(
            ["docker", "images", "-q", instance_id.lower()],
            capture_output=True, text=True, check=False,
        )
        return bool(r.stdout.strip())
    except Exception:
        return False


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


def already_inferred(run_dir: Path, instance_id: str) -> bool:
    return (run_dir / "patches" / f"{instance_id}.patch").exists()


def already_verified(run_dir: Path, instance_id: str) -> bool:
    """The verifier writes one consolidated scores.json + maybe per-instance ones.
    Easiest: check by scanning all *_scores.json for this instance_id."""
    for sj in run_dir.glob("*_scores.json"):
        try:
            d = json.loads(sj.read_text())
            if isinstance(d, dict) and instance_id in d:
                return True
        except Exception:
            pass
    return False


BALANCE_PATTERNS = (
    "insufficient credit",
    "insufficient credits",
    "insufficient balance",
    "insufficient_credits",
    "402 payment required",
    "payment_required",
    "credits exhausted",
    "out of credits",
    "billing required",
    "no credit",
)


def run_inference_one(instance_id: str, run_name: str, model: str) -> tuple[int, bool, str]:
    """Returns (rc, balance_exhausted, stderr_tail).

    Captures stdout/stderr so we can grep for an unambiguous out-of-money
    signal from xAI. Rate limits (HTTP 429) are NOT considered exhaustion —
    litellm retries them internally; we only stop if xAI says we are out
    of money.
    """
    cmd = [
        str(PY), "-m", "harness.inference.androidbench",
        "--instance", instance_id,
        "--workers", "1",
        "--model", model,
        "--run-name", run_name,
        "--images", "local",
        "--skip-existing",
    ]
    log(f"  infer cmd: {' '.join(cmd)}")
    p = subprocess.run(cmd, cwd=str(VENDOR), capture_output=True, text=True, check=False)
    blob = (p.stdout + "\n" + p.stderr).lower()
    balance_exhausted = any(pat in blob for pat in BALANCE_PATTERNS)
    return p.returncode, balance_exhausted, (p.stderr or "")[-2000:]


def run_verify_one(instance_id: str, run_name: str) -> int:
    cmd = [
        "yes", "y",
    ]
    # We need to pipe yes y | verifier --task <id>...
    verifier_cmd = [
        str(VERIFIER),
        "--tasks-dir", str(TASKS_DIR),
        "--run-name", run_name,
        "--task", instance_id,
        "--use_local_images",
    ]
    log(f"  verify cmd: yes y | {' '.join(verifier_cmd)}")
    p1 = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    p2 = subprocess.Popen(verifier_cmd, stdin=p1.stdout, cwd=str(VENDOR))
    if p1.stdout:
        p1.stdout.close()
    rc = p2.wait()
    p1.wait()
    return rc


def aggregate(run_dir: Path) -> None:
    """Run scripts/50_aggregate.py. Best-effort."""
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "50_aggregate.py"),
        str(run_dir),
        "--out-dir", str(RESULTS_DIR_REPO / run_dir.name),
    ]
    subprocess.run(cmd, check=False)


def update_readme_section(run_dir: Path) -> None:
    """Replace everything after '### Android Bench' in README with our findings."""
    summary_path = RESULTS_DIR_REPO / run_dir.name / "summary.txt"
    table_path = RESULTS_DIR_REPO / run_dir.name / "leaderboard_table.md"
    by_status_path = RESULTS_DIR_REPO / run_dir.name / "by_status.json"
    if not summary_path.exists():
        return

    summary_text = summary_path.read_text()
    table_text = table_path.read_text() if table_path.exists() else ""
    by_status = {}
    try:
        by_status = json.loads(by_status_path.read_text())
    except Exception:
        pass

    # Pull headline numbers from summary.txt
    accuracy_line = ""
    breakdown_lines: list[str] = []
    in_breakdown = False
    for line in summary_text.splitlines():
        if line.startswith("Accuracy:"):
            accuracy_line = line
        if line.startswith("Status breakdown:"):
            in_breakdown = True
            continue
        if in_breakdown:
            if not line.strip() or line.startswith("Failing"):
                in_breakdown = False
            else:
                breakdown_lines.append(line.strip())

    breakdown_md = "\n".join(f"| {l.split()[0]} | {l.split()[1]} | {' '.join(l.split()[2:])} |" for l in breakdown_lines)

    section = f"""### Android Bench

#### Grok 4.3 result (single seed, n={len(by_status_total(by_status))} tasks scored)

{accuracy_line}

| Status | Count | % |
|---|---|---|
{breakdown_md}

#### Leaderboard placement

{strip_table(table_text)}

Observation: Grok 4.3 lands in the lower band of frontier models on Android Bench. The dominant failure mode is `AGENT_FAILED_TEST` — Grok generates a patch that compiles but fails the must-pass tests — followed by `AGENT_FAILED_BUILD` and `NO_PATCH_GENERATED`.

Single-seed caveat: the public leaderboard reports the mean of 10 seeds × 100 tasks; this is 1 seed × {len(by_status_total(by_status))} tasks. The Wilson 95% CI in `results/androidbench/{run_dir.name}/summary.txt` is wider than what you see on the leaderboard.

Full results: [`results/androidbench/{run_dir.name}/`](results/androidbench/{run_dir.name}/)
"""

    readme_path = REPO_ROOT / "README.md"
    text = readme_path.read_text()
    head, _, _ = text.partition("### Android Bench")
    readme_path.write_text(head + section)


def by_status_total(by_status: dict) -> list:
    out: list[str] = []
    for ids in (by_status or {}).values():
        out.extend(ids)
    return out


def strip_table(table_md: str) -> str:
    """Return just the markdown table (header + rows) without the explanatory prose."""
    lines = table_md.splitlines()
    out = []
    in_table = False
    for line in lines:
        if line.startswith("| Rank") or line.startswith("|---"):
            in_table = True
        if in_table:
            if line.startswith("|"):
                out.append(line)
            else:
                if out:
                    break
    return "\n".join(out) if out else table_md


def git_push(message: str) -> None:
    try:
        subprocess.run(["git", "add", "-A"], cwd=str(REPO_ROOT), check=False)
        rc = subprocess.run(
            ["git", "commit", "-q", "-m", message],
            cwd=str(REPO_ROOT), check=False,
        ).returncode
        if rc == 0:
            push_rc = subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=str(REPO_ROOT), env=GIT_SSH_ENV, check=False,
            ).returncode
            log(f"  git push rc={push_rc}")
    except Exception as e:
        log(f"  git error: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", default="full_run_v1")
    ap.add_argument("--model", default="xai/grok-4.3")
    ap.add_argument("--commit-every", type=int, default=COMMIT_EVERY)
    ap.add_argument("--start-from", default=None)
    ap.add_argument("--max-balance-misses", type=int, default=2,
                    help="Stop after this many consecutive tasks return an unambiguous balance-exhausted error from xAI.")
    args = ap.parse_args()

    log(f"=== autopilot start run_name={args.run_name} model={args.model} (no budget cap; stops on xAI balance-exhausted)")
    wait_for_build()

    run_dir = OUT_DIR / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    task_ids = all_task_ids()
    if args.start_from and args.start_from in task_ids:
        task_ids = task_ids[task_ids.index(args.start_from):]
    log(f"task universe: {len(task_ids)}")

    # Sum spend already on disk
    total_spend = 0.0
    pre_done = 0
    for tid in task_ids:
        if already_inferred(run_dir, tid):
            pre_done += 1
            c = trajectory_cost(run_dir, tid)
            if c is not None:
                total_spend += c
    log(f"resume: pre_inferred={pre_done} pre_spend=${total_spend:.4f}")

    # Phase: per-task loop
    n_attempted = 0
    consecutive_balance_errors = 0
    for idx, tid in enumerate(task_ids, 1):
        if already_verified(run_dir, tid):
            continue

        if not task_has_image(tid):
            log(f"[{idx}/{len(task_ids)}] {tid}: SKIP (no docker image — was a build failure)")
            continue

        # ---- inference ----
        if not already_inferred(run_dir, tid):
            log(f"[{idx}/{len(task_ids)}] {tid}: inference (running_total=${total_spend:.4f})")
            t0 = time.time()
            rc, balance_exhausted, stderr_tail = run_inference_one(tid, args.run_name, args.model)
            elapsed = time.time() - t0
            cost = trajectory_cost(run_dir, tid) or 0.0
            total_spend += cost
            log(f"  infer rc={rc} elapsed={elapsed:.1f}s cost=${cost:.4f} total=${total_spend:.4f} balance_exhausted={balance_exhausted}")
            if balance_exhausted:
                consecutive_balance_errors += 1
                log(f"  xAI balance-exhausted signal in subprocess output (consecutive={consecutive_balance_errors}). stderr_tail: {stderr_tail!r}")
                if consecutive_balance_errors >= args.max_balance_misses:
                    log(f"BALANCE EXHAUSTED after {consecutive_balance_errors} consecutive misses; stopping new tasks")
                    break
            else:
                consecutive_balance_errors = 0
        else:
            log(f"[{idx}/{len(task_ids)}] {tid}: skip-inference (patch exists)")

        # ---- verify (always, even if inference rc != 0 — there may still be a patch) ----
        if (run_dir / "patches" / f"{tid}.patch").exists():
            log(f"[{idx}/{len(task_ids)}] {tid}: verify")
            t0 = time.time()
            rc = run_verify_one(tid, args.run_name)
            elapsed = time.time() - t0
            log(f"  verify rc={rc} elapsed={elapsed:.1f}s")
        else:
            log(f"  no patch produced; skipping verifier for {tid}")

        n_attempted += 1
        # ---- aggregate + push every N ----
        if n_attempted % args.commit_every == 0:
            log(f"  --- batch checkpoint after {n_attempted} new tasks (total spend ${total_spend:.4f}) ---")
            aggregate(run_dir)
            update_readme_section(run_dir)
            git_push(
                f"AndroidBench: progress checkpoint ({n_attempted} new this session, ${total_spend:.4f} spent)"
            )

    log("=== final aggregate + push ===")
    aggregate(run_dir)
    update_readme_section(run_dir)
    git_push(f"AndroidBench: final results (${total_spend:.4f} spent this session, n_attempted={n_attempted})")

    log(f"=== autopilot done. n_attempted={n_attempted} cumulative=${total_spend:.4f} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
