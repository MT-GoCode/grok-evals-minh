#!/usr/bin/env bash
# Re-runs inference every 15 min as new task images come online from the
# rebuild script. Picks up newly-buildable tasks via --skip-existing.
# Exits when:
#   * rebuild script is done AND no new patches in 2 consecutive iterations
#   * EXIT_AFTER_LOOPS env override (default 30 = 7.5 hours max)
set -uo pipefail

VENDOR="${VENDOR:-/home/minh/grok-evals-minh/vendor/AndroidBench}"
WORKERS="${WORKERS:-4}"
MAX_LOOPS="${EXIT_AFTER_LOOPS:-30}"
LOG=/home/minh/androidbench/auto_inference.log
: > "$LOG"
cd "$VENDOR"

prev_patches=$(ls out/full_run_v1/patches/*.patch 2>/dev/null | wc -l)
no_progress=0
for i in $(seq 1 "$MAX_LOOPS"); do
  ts=$(date '+%H:%M:%S')

  # Regenerate buildable.yaml from current docker images
  python3 - <<PY 2>>"$LOG"
import os, subprocess, yaml
out = subprocess.check_output(['sg','docker','-c','docker images --format {{.Repository}}']).decode()
tags = {l.strip() for l in out.splitlines() if l.strip()}
ids = []
for d in sorted(os.listdir('dataset/tasks')):
    full = 'dataset/tasks/' + d
    if os.path.isdir(full) and os.path.exists(full + '/task.yaml') and d.lower() in tags:
        ids.append(d)
yaml.safe_dump(ids, open('dataset/tasks/buildable.yaml', 'w'))
print(f'iter {$i}: {len(ids)} buildable tasks')
PY

  echo "[$ts] iter $i: launching inference --workers $WORKERS --skip-existing" >> "$LOG"
  sg kvm -c "sg docker -c \". $HOME/.env && export PATH=\\\"\$HOME/.local/bin:\$PATH\\\" && .venv/bin/python -m harness.inference.androidbench --workers $WORKERS --model xai/grok-4.3 --tasks-filter dataset/tasks/buildable.yaml --run-name full_run_v1 --images local --skip-existing\"" >> "$LOG" 2>&1

  cur_patches=$(ls out/full_run_v1/patches/*.patch 2>/dev/null | wc -l)
  delta=$((cur_patches - prev_patches))
  echo "[$(date '+%H:%M:%S')] iter $i done: patches now=$cur_patches (+$delta)" >> "$LOG"
  prev_patches=$cur_patches

  rebuild_running=0
  if pgrep -f rebuild_with_dns.sh >/dev/null; then rebuild_running=1; fi

  if [ "$delta" -eq 0 ]; then
    no_progress=$((no_progress+1))
  else
    no_progress=0
  fi

  if [ "$rebuild_running" -eq 0 ] && [ "$no_progress" -ge 2 ]; then
    echo "[$(date '+%H:%M:%S')] rebuild done + 2 consecutive loops without new patches; exiting" >> "$LOG"
    break
  fi

  echo "[$(date '+%H:%M:%S')] sleeping 15min before next iter (rebuild_running=$rebuild_running no_progress=$no_progress)" >> "$LOG"
  sleep 900
done

echo "AUTO_INFERENCE_DONE total_patches=$(ls out/full_run_v1/patches/*.patch 2>/dev/null | wc -l)" >> "$LOG"
