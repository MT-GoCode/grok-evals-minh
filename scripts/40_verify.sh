#!/usr/bin/env bash
# Verify patches: apply each, run that task's tests, write *_scores.json.
# Usage: bash scripts/40_verify.sh [RUN_NAME] [PARALLEL]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/vendor/AndroidBench"
RUN_NAME="${1:-full_run_v1}"
PARALLEL="${2:-4}"
cd "$VENDOR_DIR"

# `yes y |` auto-accepts the overwrite prompt the verifier uses when scores already exist.
sg kvm -c "sg docker -c \"yes y | .venv/bin/verifier --tasks-dir dataset/tasks --run-name $RUN_NAME --use_local_images --max-parallel-containers $PARALLEL --skip-existing\""
