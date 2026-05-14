#!/usr/bin/env bash
# Rebuild every task docker image that doesn't currently exist, using
# --network=host so the gradle wrapper inside the container can resolve
# services.gradle.org (the bridge-network DNS was failing in our env).
#
# Parallel via xargs -P. Adjust WORKERS via env if RAM is tight.
#
# Usage: bash scripts/rebuild_with_dns.sh
set -uo pipefail

VENDOR="${VENDOR:-/home/minh/grok-evals-minh/vendor/AndroidBench}"
WORKERS="${WORKERS:-3}"
LOG=/home/minh/androidbench/rebuild_dns.log
: > "$LOG"

cd "$VENDOR"

# Build worklist of (task, tag) pairs that don't have a docker image yet.
WORK=/tmp/rebuild_dns.work
: > "$WORK"
for taskdir in dataset/tasks/*/; do
  task=$(basename "$taskdir")
  [ -f "$taskdir/Dockerfile" ] || continue
  tag=$(echo "$task" | tr '[:upper:]' '[:lower:]')
  if ! sg docker -c "docker image inspect $tag >/dev/null 2>&1"; then
    echo "$task $tag" >> "$WORK"
  fi
done
total=$(wc -l < "$WORK")
echo "[$(date '+%H:%M:%S')] queued $total task images for rebuild (workers=$WORKERS)" | tee -a "$LOG"

build_one() {
  local task="$1" tag="$2"
  local ts=$(date '+%H:%M:%S')
  if sg docker -c "docker build --network=host -q -t $tag -f dataset/tasks/$task/Dockerfile dataset/tasks/$task" >> "$LOG" 2>&1; then
    echo "[$(date '+%H:%M:%S')] OK   $task" | tee -a "$LOG"
  else
    echo "[$(date '+%H:%M:%S')] FAIL $task" | tee -a "$LOG"
  fi
}
export -f build_one

xargs -a "$WORK" -L 1 -P "$WORKERS" -I '{}' bash -c 'build_one $1 $2' _ {} 2>&1

n_built=$(grep -c "^\[.*\] OK   " "$LOG")
n_fail=$(grep -c "^\[.*\] FAIL " "$LOG")
echo "REBUILD_SUMMARY total=$total built=$n_built failed=$n_fail" | tee -a "$LOG"
