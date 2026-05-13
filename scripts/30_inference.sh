#!/usr/bin/env bash
# Run grok-4.3 inference on all 100 tasks. Resumable via --skip-existing.
# Usage: bash scripts/30_inference.sh [RUN_NAME] [WORKERS]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/vendor/AndroidBench"
RUN_NAME="${1:-full_run_v1}"
WORKERS="${2:-4}"
MODEL="${MODEL:-xai/grok-4.3}"
cd "$VENDOR_DIR"

sg kvm -c "sg docker -c \". $HOME/.env && export PATH=\\\"$HOME/.local/bin:\$PATH\\\" && .venv/bin/python -m harness.inference.androidbench --workers $WORKERS --model $MODEL --run-name $RUN_NAME --images local --skip-existing\""
