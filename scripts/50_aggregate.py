#!/usr/bin/env python3
"""Aggregate AndroidBench scores for a single run into:

  results/androidbench/full_run_v1/summary.txt
  results/androidbench/full_run_v1/leaderboard_table.md
  results/androidbench/full_run_v1/by_status.json
  results/androidbench/<ts>.jsonl                       (matches scripts/androidbench_orchestrator.py format)
  results/androidbench/<ts>.summary.json                (ditto)

Reads per-instance ``*_scores.json`` files emitted by ``vendor/AndroidBench/.venv/bin/verifier``.

Usage:
  python scripts/50_aggregate.py vendor/AndroidBench/out/full_run_v1
  python scripts/50_aggregate.py vendor/AndroidBench/out/full_run_v1 --out-dir results/androidbench/full_run_v1
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import time
from collections import Counter
from pathlib import Path

PASS_STATUSES = {"PASSED", "PASSED_FLAKY"}  # PASSED_FLAKY kept for safety; current vendor only emits PASSED

# Public AndroidBench v1 leaderboard snapshot (https://developer.android.com/bench, 2026-05-05).
# Each public score is mean of 10 seeds × 100 tasks; CI is across-seed.
LEADERBOARD = [
    ("GPT-5.5",                  74.0),
    ("GPT-5.4",                  72.4),
    ("Gemini 3.1 Pro Preview",   72.4),
    ("Claude Opus 4.7",          68.7),
    ("GPT-5.3 Codex",            67.7),
    ("Claude Opus 4.6",          66.6),
    ("GPT-5.2 Codex",            62.5),
    ("Claude Opus 4.5",          61.9),
    ("Gemini 3 Pro Preview",     60.4),
    ("Claude Sonnet 4.6",        58.4),
    ("Claude Sonnet 4.5",        53.8),
    ("Gemini 3 Flash Preview",   42.0),
    ("Gemini 2.5 Flash",         16.7),
]


def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / d
    return c - h, c + h


def load_scores(run_dir: Path) -> dict:
    scores: dict = {}
    for path in sorted(glob.glob(str(run_dir / "*_scores.json"))):
        try:
            payload = json.loads(Path(path).read_text())
        except Exception as e:
            print(f"WARN: skipping {path}: {e}")
            continue
        # Verifier writes either {instance_id: result} or just result.
        if isinstance(payload, dict) and "status" in payload and "instance_id" not in payload:
            scores[Path(path).stem.replace("_scores", "")] = payload
        else:
            scores.update(payload)
    return scores


def load_trajectory_costs(run_dir: Path) -> dict:
    """Pull instance_cost from each trajectory JSON (best-effort)."""
    costs: dict = {}
    for tp in (run_dir / "trajectories").glob("*.json") if (run_dir / "trajectories").is_dir() else []:
        try:
            t = json.loads(tp.read_text())
            ms = (t.get("info", {}).get("extra_info", {}) or {}).get("model_stats") \
                 or t.get("info", {}).get("model_stats") or {}
            costs[tp.stem] = ms.get("instance_cost")
        except Exception:
            pass
    return costs


def render(run_dir: Path, out_dir: Path, model_label: str, repo_results_root: Path) -> None:
    scores = load_scores(run_dir)
    costs = load_trajectory_costs(run_dir)
    n = len(scores)
    if n == 0:
        raise SystemExit(f"No *_scores.json under {run_dir}")

    statuses = Counter(r.get("status", "MISSING") for r in scores.values())
    n_pass = sum(1 for r in scores.values() if r.get("status") in PASS_STATUSES)
    p = n_pass / n
    lo, hi = wilson(p, n)
    cost_vals = sorted([c for c in costs.values() if isinstance(c, (int, float))])
    median_cost = cost_vals[len(cost_vals) // 2] if cost_vals else None
    total_cost = sum(cost_vals) if cost_vals else None

    out_dir.mkdir(parents=True, exist_ok=True)
    repo_results_root.mkdir(parents=True, exist_ok=True)

    summary = out_dir / "summary.txt"
    with summary.open("w") as f:
        f.write(f"{model_label} on AndroidBench v1 (n={n})\n")
        f.write(f"Accuracy: {p*100:.2f}%   Wilson 95% CI: [{lo*100:.2f}, {hi*100:.2f}]   (±{(hi-lo)*50:.1f}pp)\n")
        if median_cost is not None:
            f.write(f"Median cost / task: ${median_cost:.4f}    Total: ${total_cost:.2f}\n")
        f.write("\nStatus breakdown:\n")
        for status, count in statuses.most_common():
            f.write(f"  {status:25s} {count:3d}  ({count/n*100:.1f}%)\n")
        f.write("\nFailing instance ids (status != PASSED*):\n")
        for inst, r in sorted(scores.items()):
            if r.get("status") not in PASS_STATUSES:
                f.write(f"  {inst:60s} {r.get('status')}\n")

    table = out_dir / "leaderboard_table.md"
    rows = LEADERBOARD + [(model_label + " (this run, 1 seed)", p * 100)]
    rows.sort(key=lambda x: -x[1])
    with table.open("w") as f:
        f.write(f"# AndroidBench v1 leaderboard — with {model_label}\n\n")
        f.write(f"Public scores: mean accuracy over 10 runs of 100 tasks each (https://developer.android.com/bench, snapshot 2026-05-05). This run: 1 seed, n={n}, Wilson 95% CI shown.\n\n")
        f.write("| Rank | Model | Accuracy |\n|---:|---|---:|\n")
        for i, (name, score) in enumerate(rows, 1):
            mark = " ←" if name.endswith("(this run, 1 seed)") else ""
            f.write(f"| {i} | {name}{mark} | {score:.1f}% |\n")
        f.write(f"\n**Grok 4.3:** {p*100:.1f}% (Wilson 95% CI {lo*100:.1f}–{hi*100:.1f})\n")

    by_status_path = out_dir / "by_status.json"
    by_status_path.write_text(json.dumps(
        {s: sorted(i for i, r in scores.items() if r.get("status") == s) for s in statuses},
        indent=2,
    ))

    # Emit grok-evals-style JSONL + summary that mirrors scripts/androidbench_orchestrator.py
    ts = time.strftime("%Y%m%d-%H%M%S")
    jsonl_path = repo_results_root / f"{ts}.jsonl"
    sum_path = repo_results_root / f"{ts}.summary.json"
    rows_out: list[dict] = []
    with jsonl_path.open("w") as f:
        for inst, rec in sorted(scores.items()):
            patch_path = run_dir / "patches" / f"{inst}.patch"
            completion = patch_path.read_text() if patch_path.exists() else ""
            row = {
                "task_id": inst,
                "score": 1.0 if rec.get("status") in PASS_STATUSES else 0.0,
                "completion": completion,
                "raw": {
                    "run_name": run_dir.name,
                    "status": rec.get("status", ""),
                    "diagnostics": rec.get("diagnostics", ""),
                    "steps": rec.get("steps", 0),
                    "cost_usd": rec.get("cost", "$0.0"),
                    "instance_cost_usd": costs.get(inst),
                },
            }
            rows_out.append(row)
            f.write(json.dumps(row) + "\n")
    sum_payload = {
        "model": model_label,
        "eval": "androidbench",
        "n_tasks_done": n,
        "n_passed": n_pass,
        "accuracy": p,
        "wilson_95_ci": [lo, hi],
        "median_instance_cost_usd": median_cost,
        "total_instance_cost_usd": total_cost,
        "status_counts": dict(statuses),
    }
    sum_path.write_text(json.dumps(sum_payload, indent=2))

    print(summary.read_text())
    print()
    print(f"Wrote: {summary}")
    print(f"Wrote: {table}")
    print(f"Wrote: {by_status_path}")
    print(f"Wrote: {jsonl_path}")
    print(f"Wrote: {sum_path}")


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path, help="e.g. vendor/AndroidBench/out/full_run_v1")
    ap.add_argument("-o", "--out-dir", type=Path, default=None, help="defaults to results/androidbench/<run-dir-name>/")
    ap.add_argument("--model-label", default="Grok 4.3")
    ap.add_argument("--repo-results-root", type=Path, default=repo_root / "results" / "androidbench")
    args = ap.parse_args()
    run_dir = args.run_dir.expanduser().resolve()
    out_dir = (args.out_dir or (args.repo_results_root / run_dir.name)).expanduser()
    render(run_dir, out_dir, args.model_label, args.repo_results_root)


if __name__ == "__main__":
    main()
