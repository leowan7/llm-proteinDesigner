#!/usr/bin/env bash
# SC 6 manual validator: dispatch a tiny BindCraft smoke job to Modal staging
# and confirm it completes. Exit 0 on success, non-zero on failure.
#
# Usage:
#   scripts/validate_prod_gpu.sh [staging|main]   # default: staging
#
# Requires: modal CLI authenticated via MODAL_TOKEN_ID + MODAL_TOKEN_SECRET.

set -euo pipefail

ENV="${1:-staging}"
APP_FILE="infrastructure/modal/bindcraft_app.py"

if [ ! -f "$APP_FILE" ]; then
  echo "ERROR: $APP_FILE not found" >&2
  exit 2
fi

if ! command -v modal >/dev/null 2>&1; then
  echo "ERROR: modal CLI not installed (pip install modal==1.4.2)" >&2
  exit 2
fi

echo "Dispatching smoke BindCraft run to Modal env=$ENV ..."
modal run --env "$ENV" "$APP_FILE::run_tool" --payload '{"tier":"smoke","num_designs":1}' || {
  echo "FAIL: modal run exited non-zero" >&2
  exit 1
}
echo "PASS: Modal smoke run completed in env=$ENV"
