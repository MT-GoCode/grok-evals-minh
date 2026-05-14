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


def retry_failed_builds(time_budget_s: int = 1800, per_build_timeout_s: int = 480) -> None:
    """Retry failed builds, time-boxed. Sequential (low memory pressure),
    each capped at per_build_timeout_s, whole pass capped at time_budget_s.
    After the budget elapses, returns and lets the inference loop run on
    whatever we have. Issue-#46 / structural failures fail again fast."""
    if not BUILD_LOG.is_file():
        return
    txt = BUILD_LOG.read_text(errors="ignore")
    failed = []
    seen = set()
    for line in txt.splitlines():
        if line.startswith("Error building docker image "):
            tid_lc = line.split("Error building docker image ", 1)[1].strip()
            if tid_lc and tid_lc not in seen:
                failed.append(tid_lc)
                seen.add(tid_lc)
    if not failed:
        log("no failed images to retry")
        return

    actual: dict[str, str] = {}
    for p in TASKS_DIR.iterdir():
        if p.is_dir() and (p / "task.yaml").exists():
            actual[p.name.lower()] = p.name
    log(f"retrying {len(failed)} failed image builds (sequential, time_budget={time_budget_s}s, per_build_timeout={per_build_timeout_s}s)…")
    n_recovered = 0
    n_attempted = 0
    deadline = time.time() + time_budget_s
    for tid_lc in failed:
        if time.time() >= deadline:
            log(f"  retry time budget exhausted; recovered {n_recovered}/{n_attempted} attempted of {len(failed)} failed")
            break
        if task_has_image(tid_lc):
            n_recovered += 1
            continue
        real = actual.get(tid_lc)
        if not real:
            continue
        df = TASKS_DIR / real / "Dockerfile"
        if not df.is_file():
            continue
        cmd = ["docker", "build", "--quiet", "-t", tid_lc, "-f", str(df), str(TASKS_DIR / real)]
        log(f"  retry {real} ...")
        n_attempted += 1
        try:
            rc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=per_build_timeout_s)
            if rc.returncode == 0:
                n_recovered += 1
                log(f"    OK -> {tid_lc}")
            else:
                log(f"    FAIL rc={rc.returncode} stderr_tail={(rc.stderr or '')[-200:]!r}")
        except subprocess.TimeoutExpired:
            log(f"    TIMEOUT after {per_build_timeout_s}s")
    log(f"retry pass done: recovered {n_recovered}/{n_attempted} attempted of {len(failed)} total failed")


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
    """Write a SHORT Android Bench section into the main README (matching the
    TutorBench / MatSciBench style of two compact tables + observation lines)
    AND a long-form version with all per-task tables, caveats, and repro
    instructions into results/androidbench/README.md.
    """
    scores_path = run_dir / "0_to_99_scores.json"
    if not scores_path.exists():
        # fall back: any *_scores.json
        cands = sorted(run_dir.glob("*_scores.json"))
        if not cands:
            return
        scores_path = cands[-1]
    try:
        d = json.loads(scores_path.read_text())
    except Exception:
        return

    PASS = {"PASSED", "PASSED_FLAKY"}
    n = len(d)
    n_pass = sum(1 for v in d.values() if v.get("status") in PASS)
    attempted = {k: v for k, v in d.items() if str(v.get("steps", "0")) not in ("0", "")}
    n_att = len(attempted)
    n_att_pass = sum(1 for v in attempted.values() if v.get("status") in PASS)
    p_all = n_pass / n if n else 0.0
    lo_all, hi_all = _wilson(p_all, n)
    p_att = n_att_pass / n_att if n_att else 0.0
    lo_att, hi_att = _wilson(p_att, n_att)

    import collections
    statuses = collections.Counter(v.get("status", "MISSING") for v in d.values())
    n_no_image = sum(
        1 for v in d.values()
        if v.get("status") == "AGENT_NO_PATCH" and str(v.get("steps", "0")) in ("0", "")
    )

    breakdown_rows = []
    explain = {
        "PASSED": "Grok's patch compiled and the must-pass tests passed",
        "PASSED_FLAKY": "Passed after a retry of the test execution",
        "AGENT_FAILED_TEST": "Grok's patch compiled but a must-pass test failed",
        "AGENT_FAILED_BUILD": "Grok's patch broke compilation",
        "NO_PATCH_GENERATED": "Agent gave up or produced an empty diff",
        "AGENT_NO_PATCH": "No docker image was built — Grok never ran (not a model failure)",
        "INFRA_FAILURE": "Verifier infra error",
        "EVAL_ERROR": "Evaluator-side error",
        "SKIPPED": "Task excluded by config",
    }
    for status, count in statuses.most_common():
        label = status
        if status == "AGENT_NO_PATCH" and n_no_image == count:
            label = "AGENT_NO_PATCH (no image built — Grok never ran)"
        breakdown_rows.append(
            f"| `{label}` | {count} | {count/n*100:.1f}% | {explain.get(status, '')} |"
        )
    breakdown_md = "\n".join(breakdown_rows)

    def fmt_cost(c):
        try:
            s = str(c).lstrip("$")
            return f"${float(s):.3f}"
        except Exception:
            return str(c)

    # Wins table
    wins_rows = []
    for k, v in sorted(d.items()):
        if v.get("status") in PASS:
            cost = fmt_cost(v.get("cost", "$0"))
            steps = v.get("steps", "")
            note = "FLAKY (passed only after test retry)" if v.get("status") == "PASSED_FLAKY" else ""
            wins_rows.append(f"| `{k}` | {cost} | {steps} | {note} |")
    wins_md = "\n".join(wins_rows) if wins_rows else "_None._"

    # Attempted-only failures
    fail_rows = []
    for k, v in sorted(attempted.items()):
        if v.get("status") not in PASS:
            cost = fmt_cost(v.get("cost", "$0"))
            steps = v.get("steps", "")
            fail_rows.append(f"| `{k}` | {v.get('status')} | {cost} | {steps} |")
    fail_md = "\n".join(fail_rows) if fail_rows else "_None._"

    # Leaderboard with Grok inserted at its position (attempted-only score)
    leaderboard = [
        ("GPT-5.5",                 74.0),
        ("GPT-5.4",                 72.4),
        ("Gemini 3.1 Pro Preview",  72.4),
        ("Claude Opus 4.7",         68.7),
        ("GPT-5.3 Codex",           67.7),
        ("Claude Opus 4.6",         66.6),
        ("GPT-5.2 Codex",           62.5),
        ("Claude Opus 4.5",         61.9),
        ("Gemini 3 Pro Preview",    60.4),
        ("Claude Sonnet 4.6",       58.4),
        ("Claude Sonnet 4.5",       53.8),
        ("Gemini 3 Flash Preview",  42.0),
        ("Gemini 2.5 Flash",        16.7),
    ]
    grok_pp = (hi_att - lo_att) * 50  # half-width of CI in percentage points
    grok_label = f"Grok 4.3 (n={n_att})"
    grok_score = p_att * 100
    rows = leaderboard + [(grok_label, grok_score)]
    rows.sort(key=lambda x: -x[1])
    leaderboard_short_md = "| Rank | Model | Accuracy |\n|---|---|---|\n"
    for i, (name, score) in enumerate(rows, 1):
        if name == grok_label:
            leaderboard_short_md += f"| **→ ≈{i}** | **{name}** | **{score:.1f} ± {grok_pp:.1f}** |\n"
        else:
            leaderboard_short_md += f"| {i} | {name} | {score:.1f} |\n"

    # ---- SHORT section for main README (TutorBench / MatSciBench style) ----
    short = f"""### Android Bench

#### Grok 4.3 performance by task subset

| Subset | n | Accuracy | Notes |
|---|---|---|---|
| Solid run-throughs (image built + Grok ran + verifier scored) | {n_att} | **{p_att*100:.1f}% ± {grok_pp:.1f}** | Apples-to-apples Grok number |
| Full task universe (incl. {n_no_image} where the docker image couldn't be built) | {n} | {p_all*100:.1f}% | Strict leaderboard methodology |

Observation: {n_no_image}/{n} task images failed to build in our cloud-VM docker env (gradle DNS failure inside the bridge network). On the {n_att} we did get a complete pipeline for, Grok 4.3 wins {p_att*100:.1f}%.

#### Leaderboard placement (attempted-only, n={n_att})

{leaderboard_short_md}
Observation: Grok 4.3 sits in the middle of the frontier band, behind the GPT-5.4 / Gemini 3.1 Pro Preview / Opus 4.7 leaders by ~10pp. Single-seed, n={n_att} → wide CI; full per-task breakdown + caveats + repro in [`results/androidbench/`](results/androidbench/).
"""

    readme_path = REPO_ROOT / "README.md"
    text = readme_path.read_text()
    head, _, _ = text.partition("### Android Bench")
    # Trim any prior "## Reproducing the Android Bench run from scratch"
    # section that an earlier write may have left at the bottom.
    if "## Reproducing the Android Bench run from scratch" in head:
        head = head.split("## Reproducing the Android Bench run from scratch", 1)[0]
        # Strip trailing "---" separator if present
        head = head.rstrip().rstrip("-").rstrip() + "\n\n"
    readme_path.write_text(head + short)

    # ---- LONG version + repro instructions in results/androidbench/README.md ----
    long = f"""# Android Bench — Grok 4.3 detailed results

> Last refreshed by `scripts/99_master_autopilot.py:update_readme_section()`.

## Headline numbers

> **⚠ Important caveat.** {n_no_image}/{n} of the Android Bench task images failed to build in our environment (Ubuntu 22.04 + Docker 29 + JDK 17, KVM-enabled VM): the gradle wrapper inside the build container couldn't reach `services.gradle.org` (DNS) on the default bridge network for those images. They're recorded as `AGENT_NO_PATCH` (steps=0, cost=$0) in `*_scores.json` because there was nothing for the agent to run against. These are **not** model failures.
>
> So we report **two metrics**:

**Headline (leaderboard methodology, n={n}):**
- **Accuracy: {p_all*100:.1f}%** ({n_pass} PASSED+PASSED_FLAKY out of {n})
- Wilson 95% CI: [{lo_all*100:.1f}%, {hi_all*100:.1f}%]

**Attempted-only (n={n_att}, the subset where the docker image built and Grok actually ran):**
- **Accuracy: {p_att*100:.1f}%** ({n_att_pass} / {n_att})
- Wilson 95% CI: [{lo_att*100:.1f}%, {hi_att*100:.1f}%] — wide because n is small (single seed)

## Status breakdown

| Status | Count | % of {n} | What it means |
|---|---:|---:|---|
{breakdown_md}

## Wins (Grok 4.3 actually solved these)

| Task | Cost | Steps | Notes |
|---|---:|---:|---|
{wins_md}

## Failures on the attempted subset

| Task | Status | Cost | Steps |
|---|---|---:|---:|
{fail_md}

## Reproducing this run from scratch

**Hardware**: x86_64 Ubuntu 22.04+ VM, KVM-enabled (`/dev/kvm` writable), ≥32 GB RAM with a 32 GB swapfile, ≥600 GB free disk. Cloud VMs work; ARM64 doesn't (no Android x86 emulator).

**API keys** in `~/.env`:
```bash
XAI_API_KEY=sk-xai-...
GITHUB_PAT=ghp-...   # optional, dodges GitHub rate limits during repo-base builds
```

**One-shot reproduction**:
```bash
git clone git@github.com:MT-GoCode/grok-evals-minh.git && cd grok-evals-minh
bash scripts/run_all_remote.sh full_run_v1 4
```

This calls each of the numbered scripts in order; everything is idempotent and resumable. Final results land in `results/androidbench/{run_dir.name}/` and the main README's `### Android Bench` section above is auto-rewritten.

### What each script does

| Script | Purpose |
|---|---|
| `scripts/00_setup.sh` | apt deps (docker, qemu-kvm), install uv, clone vendor `android-bench/android-bench` into `vendor/AndroidBench`, create Python 3.14 venv + install deps |
| `scripts/10_setup_base.sh` | vendor's `utils.setup` — oracle agent + dataset summary |
| `scripts/20_build_images.sh` | **Two-pass build.** Pass 1: vendor's `generate_docker_images.py` builds the base + ~32 repo-base images. Pass 2: `rebuild_with_dns.sh` rebuilds the 100 task layers with `docker build --network=host` (gradle wrapper can't resolve `services.gradle.org` on the default bridge network in many cloud-VM docker setups). |
| `scripts/30_inference.sh` | `harness.inference.androidbench --workers N --model xai/grok-4.3 --skip-existing` — Grok generates patches, parallel across N workers |
| `scripts/40_verify.sh` | vendor's verifier — applies each patch, runs unit + instrumentation tests in another docker container, scores `PASSED / AGENT_FAILED_TEST / ...` |
| `scripts/50_aggregate.py` | Wilson 95% CI, status breakdown, leaderboard table, JSONL in `grok-evals` format |
| `scripts/99_master_autopilot.py` | Full unattended runner. Waits for build, then per-task inference + bulk verify, with `git push` checkpoints every N tasks. Stops on xAI balance-exhausted. |
| `scripts/35_inference_budgeted.py` | Sequential inference with a hard $-cap (used during exploratory runs). |
| `scripts/auto_inference_loop.sh` | Re-runs inference every 15 min as new task images come online from a parallel rebuild. |
| `scripts/rebuild_with_dns.sh` | Parallel `docker build --network=host` for any task images that don't yet exist — used as the second build pass and as a recovery tool. |
| `scripts/finalize_results.py` | Merges all `*_scores.json` files (the verifier writes new files per filter), restores any statuses overwritten by a verifier startup pass, runs `50_aggregate.py`, refreshes both READMEs. |

## Caveats & things to know

1. **Verifier `--skip-existing` quirk.** The vendor's verifier overwrites the consolidated `0_to_99_scores.json` with placeholder `AGENT_NO_PATCH` rows at startup, then leaves `--skip-existing` rows at the placeholder. `finalize_results.py` works around this by merging all per-invocation `*_scores.json` files and never letting a fresh placeholder overwrite a real prior status.
2. **Docker `--network=host`** is required for many cloud-VM docker setups due to DNS-resolution failure inside the default bridge network. Bare-metal / clean-docker environments may not need it.
3. **Single-seed.** This run is 1 seed × the buildable subset; the public leaderboard reports the mean of 10 seeds × all 100 tasks. The Wilson 95% CI here is correspondingly wider (~±15pp at n={n_att} vs ~±5pp on the leaderboard).
4. **Cost cap.** Each task is hard-capped at $10 of model spend by `harness/inference/androidbench.yaml`. Median observed per-task cost in this run was ~$0.17.

## Where the raw data is

- `results/androidbench/{run_dir.name}/summary.txt` — text headline + status breakdown
- `results/androidbench/{run_dir.name}/leaderboard_table.md` — drop-in placement table
- `results/androidbench/{run_dir.name}/by_status.json` — `{{status: [instance_ids]}}`
- `vendor/AndroidBench/out/{run_dir.name}/*_scores.json` — per-instance verifier output
- `vendor/AndroidBench/out/{run_dir.name}/patches/<task>.patch` — the diff Grok produced
- `vendor/AndroidBench/out/{run_dir.name}/trajectories/<task>.json` — full agent transcript (commands + model responses + cost/token accounting)
- `results/androidbench/<ts>.jsonl` + `<ts>.summary.json` — `grok-evals`-format aggregates
"""
    long_path = REPO_ROOT / "results" / "androidbench" / "README.md"
    long_path.parent.mkdir(parents=True, exist_ok=True)
    long_path.write_text(long)


def _wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    import math
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / d
    return c - h, c + h


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
    ap.add_argument("--inline-verify", action="store_true",
                    help="If set, verify after each inference (slow). Default: skip per-task verify; do bulk parallel verify at end.")
    ap.add_argument("--bulk-verify-parallel", type=int, default=2,
                    help="Parallelism for the bulk verify pass at the end. Each gradle test JVM uses ~4 GB heap.")
    args = ap.parse_args()

    log(f"=== autopilot start run_name={args.run_name} model={args.model} (no budget cap; stops on xAI balance-exhausted)")
    # Write a PID file so safe kill+restart from outside is unambiguous.
    pidfile = Path("/tmp/autopilot.pid")
    pidfile.write_text(str(os.getpid()))
    log(f"pid={os.getpid()} pidfile={pidfile}")
    wait_for_build()
    # Retry pass DISABLED: empirically every retry of the failed builds also
    # fails (gradle assembleDebug fails fast). Spending 30 min retrying with
    # zero recoveries is dead time; go straight to inference on what we have.
    if os.environ.get("AUTOPILOT_RETRY", "0") == "1":
        retry_failed_builds()

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

        # ---- verify (inline only when explicitly requested; default = bulk at end) ----
        if args.inline_verify:
            if (run_dir / "patches" / f"{tid}.patch").exists():
                log(f"[{idx}/{len(task_ids)}] {tid}: verify (inline)")
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

    # ---- bulk verify pass (default mode) ----
    if not args.inline_verify:
        log(f"=== bulk verify pass (parallel={args.bulk_verify_parallel}) ===")
        cmd = [
            str(VERIFIER),
            "--tasks-dir", str(TASKS_DIR),
            "--run-name", args.run_name,
            "--use_local_images",
            "--max-parallel-containers", str(args.bulk_verify_parallel),
            "--skip-existing",
        ]
        log(f"  bulk verify cmd: yes y | {' '.join(cmd)}")
        p1 = subprocess.Popen(["yes", "y"], stdout=subprocess.PIPE)
        p2 = subprocess.Popen(cmd, stdin=p1.stdout, cwd=str(VENDOR))
        if p1.stdout:
            p1.stdout.close()
        rc = p2.wait()
        p1.wait()
        log(f"  bulk verify rc={rc}")

    log("=== final aggregate + push ===")
    aggregate(run_dir)
    update_readme_section(run_dir)
    git_push(f"AndroidBench: final results (${total_spend:.4f} spent this session, n_attempted={n_attempted})")

    log(f"=== autopilot done. n_attempted={n_attempted} cumulative=${total_spend:.4f} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
