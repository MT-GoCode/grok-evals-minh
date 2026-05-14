#!/usr/bin/env bash
# Build base + repo-base + all 100 task docker images locally, in two passes:
#
#   Pass 1: vendor's generate_docker_images.py — builds the base
#           android-bench-env image and the ~32 repo-base images that pre-clone
#           each upstream Android repo and prime the gradle cache. Uses the
#           default docker bridge network for these (works fine).
#
#   Pass 2: rebuild_with_dns.sh — builds the 100 task layers using
#           `docker build --network=host`. Without --network=host, the gradle
#           wrapper inside the build container often can't resolve
#           services.gradle.org on common cloud-VM docker setups, and the
#           ./gradlew assembleDebug step in each task Dockerfile fails.
#
# Both passes are idempotent and resumable (skip images that already exist).
# 32 GB RAM -> 4 workers in pass 1, 3 workers in pass 2; tune via env.
#
# Usage: bash scripts/20_build_images.sh [WORKERS_PASS1] [WORKERS_PASS2]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/vendor/AndroidBench"
WORKERS_PASS1="${1:-4}"
WORKERS_PASS2="${2:-3}"
cd "$VENDOR_DIR"

echo ">>> Pass 1: vendor's generate_docker_images.py (--max_workers $WORKERS_PASS1)"
sg kvm -c "sg docker -c \". $HOME/.env && export GITHUB_TOKEN=\${GITHUB_PAT:-\${GITHUB_TOKEN:-}} && export PATH=\\\"$HOME/.local/bin:\$PATH\\\" && .venv/bin/python -m utils.docker.generate_docker_images --build --max_workers $WORKERS_PASS1\"" || echo "(pass 1 returned non-zero — common; pass 2 will retry the failing task images with --network=host)"

echo ">>> Pass 2: rebuild_with_dns.sh (--network=host, parallel=$WORKERS_PASS2)"
WORKERS="$WORKERS_PASS2" bash "$REPO_ROOT/scripts/rebuild_with_dns.sh"

echo ">>> Done. Run scripts/30_inference.sh next."
