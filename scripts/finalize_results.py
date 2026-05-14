#!/usr/bin/env python3
"""Final aggregation step.

Combines results from BOTH passes:
  * the 10 originally-verified tasks from autopilot's first bulk verify
    (statuses preserved here because the live scores.json was overwritten
     by the second verifier invocation with --skip-existing),
  * the 25 newly-verified tasks from phase 2 verify.

Writes the merged scores.json + runs the standard aggregator + updates
README's '### Android Bench' section.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = REPO_ROOT / "vendor" / "AndroidBench" / "out" / "full_run_v1"

# Statuses verified in the FIRST run of autopilot (preserved from saved
# summary.txt + by_status.json before the second verifier overwrote
# 0_to_99_scores.json). Costs/steps from saved scores.json snapshot below.
ORIGINALS = {
    "AntennaPod__AntennaPod-pr_6838":          {"status": "PASSED",             "cost": "$0.0330", "steps": "8"},
    "thunderbird__thunderbird-android-pr_7103":{"status": "PASSED",             "cost": "$0.0832", "steps": "10"},
    "thunderbird__thunderbird-android-pr_7190":{"status": "PASSED",             "cost": "$0.1570", "steps": "22"},
    "thunderbird__thunderbird-android-pr_8020":{"status": "PASSED",             "cost": "$0.1929", "steps": "18"},
    "LemmyNet__jerboa-pr_991":                 {"status": "PASSED_FLAKY",       "cost": "$2.2768", "steps": "128"},
    "MohamedRejeb__compose-rich-editor-pr_319":{"status": "AGENT_FAILED_TEST",  "cost": "$0.6110", "steps": "45"},
    "MohamedRejeb__compose-rich-editor-pr_335":{"status": "AGENT_FAILED_TEST",  "cost": "$0.1252", "steps": "17"},
    "coil-kt__coil-pr_2669":                   {"status": "AGENT_FAILED_TEST",  "cost": "$0.4410", "steps": "42"},
    "android_snippets_1":                      {"status": "AGENT_FAILED_BUILD", "cost": "$0.1185", "steps": "13"},
    "DroidKaigi__conference-app-2023-pr_896":  {"status": "INFRA_FAILURE",      "cost": "$0.0667", "steps": "13"},
}


def main() -> int:
    scores_path = RUN_DIR / "0_to_99_scores.json"
    if not scores_path.exists():
        # Find any *_scores.json
        cands = sorted(RUN_DIR.glob("*_scores.json"))
        if not cands:
            print("ERROR: no scores.json found")
            return 1
        scores_path = cands[-1]
    d = json.loads(scores_path.read_text())

    # Override originals with saved values (since the live scores.json was
    # corrupted by the second verifier's "Initial scores" overwrite).
    overrides = 0
    for tid, saved in ORIGINALS.items():
        if tid in d:
            cur = d[tid]
            # Only override if the live status is the placeholder AGENT_NO_PATCH,
            # which means it lost its real status during the overwrite.
            if cur.get("status") == "AGENT_NO_PATCH" and saved["status"] != "AGENT_NO_PATCH":
                cur["status"] = saved["status"]
                cur["cost"] = saved["cost"]
                cur["steps"] = saved["steps"]
                cur["score"] = 1.0 if saved["status"] in ("PASSED", "PASSED_FLAKY") else 0.0
                cur["status_description"] = "Restored from autopilot's first bulk verify (live scores.json was overwritten)"
                overrides += 1
    print(f"applied {overrides} status overrides from saved originals")

    scores_path.write_text(json.dumps(d, indent=2))
    print(f"wrote merged scores to {scores_path}")

    # Run the aggregator
    rc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "50_aggregate.py"), str(RUN_DIR),
         "--out-dir", str(REPO_ROOT / "results" / "androidbench" / "full_run_v1")],
        check=False,
    ).returncode
    print(f"aggregator rc={rc}")

    # Update README via autopilot's writer
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("autopilot", REPO_ROOT / "scripts" / "99_master_autopilot.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.update_readme_section(RUN_DIR)
    print("README updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
