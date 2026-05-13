#!/usr/bin/env bash
# Build base + all 100 task docker images locally.
# 32 GB RAM -> 4 workers; 64 GB -> 8; 128 GB -> 16.
# Usage: bash scripts/20_build_images.sh [WORKERS]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/vendor/AndroidBench"
WORKERS="${1:-4}"
cd "$VENDOR_DIR"

sg kvm -c "sg docker -c \". $HOME/.env && export PATH=\\\"$HOME/.local/bin:\$PATH\\\" && .venv/bin/python -m utils.docker.generate_docker_images --build --max_workers $WORKERS\""
