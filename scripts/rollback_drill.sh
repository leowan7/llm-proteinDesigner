#!/usr/bin/env bash
# SC 9 manual drill: rollback Railway + Vercel within 5 minutes.
# Documents the steps and records wall-clock; does not auto-execute destructive commands.
#
# Usage:
#   scripts/rollback_drill.sh --dry-run   # print steps only (safe to run anytime)
#   scripts/rollback_drill.sh             # interactive: prompts before each rollback
#
# Expected runtime to /health green: < 5 min per SC 9.

set -euo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

START=$(date +%s)

echo "=== Kendrew rollback drill (dry-run=$DRY_RUN) ==="
echo "Step 1: Identify current prod deploy SHAs"
echo "  railway deployments --service kendrew-backend-prod --limit 2"
echo "  vercel ls --scope=<team> kendrew --limit 2"
echo
echo "Step 2: Roll back Railway backend"
if [ "$DRY_RUN" = "1" ]; then
  echo "  [dry-run] railway rollback --service kendrew-backend-prod"
else
  read -r -p "Execute Railway rollback? (y/N) " ans
  [ "$ans" = "y" ] && railway rollback --service kendrew-backend-prod
fi
echo
echo "Step 3: Roll back Vercel frontend"
if [ "$DRY_RUN" = "1" ]; then
  echo "  [dry-run] vercel rollback https://bindwave.com"
else
  read -r -p "Execute Vercel rollback? (y/N) " ans
  [ "$ans" = "y" ] && vercel rollback https://bindwave.com
fi
echo
echo "Step 4: Verify /health green"
if [ "$DRY_RUN" = "0" ]; then
  for i in 1 2 3 4 5; do
    code=$(curl -s -o /dev/null -w "%{http_code}" https://app.bindwave.com/health || echo 000)
    if [ "$code" = "200" ]; then
      echo "  /health OK after ${i} attempts"
      break
    fi
    sleep 10
  done
fi

END=$(date +%s)
ELAPSED=$((END - START))
echo
echo "=== Rollback drill elapsed: ${ELAPSED}s (target < 300s) ==="
