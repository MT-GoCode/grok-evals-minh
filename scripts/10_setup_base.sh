#!/usr/bin/env bash
# Oracle agent + dataset summary + prebuild checks. Run once after 00_setup.
# Usage: bash scripts/10_setup_base.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/vendor/AndroidBench"
cd "$VENDOR_DIR"

# Both kvm and docker need to be in the active subshell's effective groups.
sg kvm -c 'sg docker -c ". $HOME/.env && export PATH=\"$HOME/.local/bin:$PATH\" && .venv/bin/python -m utils.setup"'
