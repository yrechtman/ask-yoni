#!/bin/bash
# Unattended sync on DigitalOcean droplet. Pulls latest code, runs RSS sync.
# Exits 0 regardless of step failures — log loudly, do not retry.
# Uses flock to prevent concurrent runs.
set -uo pipefail

PROJECT_DIR="/root/ask-yoni-local"
cd "$PROJECT_DIR" || exit 1

LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/sync-$(date -u +%Y-%m).log"

exec >>"$LOG" 2>&1

# Single-instance lock. Skip silently if a previous run is still going.
exec 200>/var/lock/ask-yoni-sync.lock
flock -n 200 || { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) skip: prior run still holds lock"; exit 0; }

# Pull latest code (fast-forward only). Don't get clever on conflicts.
git pull --ff-only origin main 2>&1 || { echo "git pull failed, using existing checkout"; }

set -a; source .env 2>/dev/null || true; set +a

echo ""
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) sync starting (pid $$) ==="

.venv/bin/python -u scripts/rss_sync.py && echo "rss: ok" || echo "rss: FAILED (exit $?)"

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) sync done ==="
exit 0
