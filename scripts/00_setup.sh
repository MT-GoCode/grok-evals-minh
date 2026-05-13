#!/usr/bin/env bash
# Install system prereqs and clone AndroidBench. Idempotent. Designed for the
# remote machine (Ubuntu 22.04, KVM, ~32 GB RAM, ~hundreds GB disk). Mirrors
# the layout src/grok_evals/evals/androidbench.py expects:
#   <repo>/vendor/AndroidBench/.venv/bin/python
# Usage: bash scripts/00_setup.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR_DIR="$REPO_ROOT/vendor/AndroidBench"

echo ">>> apt prereqs (sudo)"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  docker.io docker-buildx qemu-kvm git ca-certificates curl

echo ">>> docker + kvm group membership"
sudo usermod -aG docker "$USER"
sudo usermod -aG kvm "$USER"
sudo systemctl enable --now docker

echo ">>> uv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

echo ">>> clone AndroidBench into vendor/"
mkdir -p "$REPO_ROOT/vendor"
if [ ! -e "$VENDOR_DIR" ]; then
  git clone --depth 1 https://github.com/android-bench/android-bench "$VENDOR_DIR"
fi

echo ">>> python 3.14 venv + deps"
cd "$VENDOR_DIR"
uv venv -p 3.14 .venv
uv pip install -e . --python .venv/bin/python

echo ">>> Done. NEW SHELLS pick up docker+kvm groups via 'sg kvm -c \"sg docker -c ...\"'."
echo ">>> Put XAI_API_KEY=... in \$HOME/.env (and optionally GITHUB_TOKEN=... for image-build rate limits)."
