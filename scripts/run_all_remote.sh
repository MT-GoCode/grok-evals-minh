#!/usr/bin/env bash
# End-to-end orchestration of the remote-machine Grok 4.3 / AndroidBench run.
# Idempotent — every step is resumable.
#
# Prereqs:
#   * Ubuntu 22.04+ VM, KVM-enabled, ~32 GB RAM, ~600 GB free disk.
#   * 32 GB swap recommended (gradle JVMs are RAM-hungry; without swap a
#     parallel build pass can OOM-kill the whole box). See README.
#   * $HOME/.env containing:  XAI_API_KEY=...
#     Optional:                GITHUB_PAT=...   (avoids GitHub rate limits
#                                                during repo-base image builds)
#
# Usage: bash scripts/run_all_remote.sh [RUN_NAME] [WORKERS]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
RUN_NAME="${1:-full_run_v1}"
WORKERS="${2:-4}"

bash "$HERE/00_setup.sh"               # apt deps, docker, qemu-kvm, uv, vendor clone, venv, deps
bash "$HERE/10_setup_base.sh"          # vendor's setup_env: oracle agent + dataset summary
bash "$HERE/20_build_images.sh" "$WORKERS" 3   # base + repo bases (vendor) + task layers (--network=host)
bash "$HERE/30_inference.sh"   "$RUN_NAME" "$WORKERS"   # parallel inference, --skip-existing
bash "$HERE/40_verify.sh"      "$RUN_NAME" 2            # parallel verify, --skip-existing

# Merge any incremental score files + restore originals if a prior verify pass
# overwrote them; writes results/androidbench/$RUN_NAME/{summary.txt, leaderboard_table.md, ...}
# and rewrites README's '### Android Bench' section with the latest numbers.
python3 "$HERE/finalize_results.py"

echo
echo ">>> All done. See:"
echo "  $REPO_ROOT/README.md   (Android Bench section)"
echo "  $REPO_ROOT/results/androidbench/$RUN_NAME/summary.txt"
echo "  $REPO_ROOT/results/androidbench/$RUN_NAME/leaderboard_table.md"
echo "  $REPO_ROOT/vendor/AndroidBench/out/$RUN_NAME/  (raw scores, patches, trajectories)"
