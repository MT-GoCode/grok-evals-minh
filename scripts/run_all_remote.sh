#!/usr/bin/env bash
# End-to-end orchestration of the remote-machine Grok 4.3 / AndroidBench run.
# Idempotent: each step is resumable.
# Usage: bash scripts/run_all_remote.sh [RUN_NAME] [WORKERS]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
RUN_NAME="${1:-full_run_v1}"
WORKERS="${2:-4}"

bash "$HERE/00_setup.sh"
bash "$HERE/10_setup_base.sh"
bash "$HERE/20_build_images.sh" "$WORKERS"
bash "$HERE/30_inference.sh"   "$RUN_NAME" "$WORKERS"
bash "$HERE/40_verify.sh"      "$RUN_NAME" "$WORKERS"

python3 "$HERE/50_aggregate.py" "$REPO_ROOT/vendor/AndroidBench/out/$RUN_NAME"

echo
echo ">>> All done. See:"
echo "  $REPO_ROOT/results/androidbench/$RUN_NAME/summary.txt"
echo "  $REPO_ROOT/results/androidbench/$RUN_NAME/leaderboard_table.md"
echo "  $REPO_ROOT/vendor/AndroidBench/out/$RUN_NAME/  (raw scores, patches, trajectories)"
