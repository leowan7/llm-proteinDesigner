#!/usr/bin/env bash
# Re-applies all six Bindwave Auth email templates and subjects via the
# Supabase Management API in one PATCH call.
#
# Usage:
#   export SUPABASE_ACCESS_TOKEN=sbp_xxx        # from https://supabase.com/dashboard/account/tokens
#   bash docs/auth-email-templates/apply-templates.sh
#
# Idempotent: re-running with the same files is a no-op for content.

set -euo pipefail

PROJECT_REF="omrhpkmgiqvuwpadhbsl"
TEMPLATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${SUPABASE_ACCESS_TOKEN:-}" ]]; then
  echo "error: SUPABASE_ACCESS_TOKEN env var is required" >&2
  echo "       generate one at https://supabase.com/dashboard/account/tokens" >&2
  exit 1
fi

read_file() {
  python -c "import json,sys; print(json.dumps(open(sys.argv[1], encoding='utf-8').read()))" "$1"
}

CONFIRM=$(read_file "$TEMPLATE_DIR/confirm-signup.html")
INVITE=$(read_file "$TEMPLATE_DIR/invite.html")
MAGIC=$(read_file "$TEMPLATE_DIR/magic-link.html")
CHANGE=$(read_file "$TEMPLATE_DIR/change-email.html")
RESET=$(read_file "$TEMPLATE_DIR/reset-password.html")
REAUTH=$(read_file "$TEMPLATE_DIR/reauthentication.html")

PAYLOAD=$(cat <<EOF
{
  "mailer_subjects_confirmation": "Confirm your Bindwave email",
  "mailer_subjects_invite":       "You're invited to Bindwave",
  "mailer_subjects_magic_link":   "Your Bindwave sign-in link",
  "mailer_subjects_email_change": "Confirm your new Bindwave email",
  "mailer_subjects_recovery":     "Reset your Bindwave password",
  "mailer_subjects_reauthentication": "Confirm it's you on Bindwave",
  "mailer_templates_confirmation_content":     $CONFIRM,
  "mailer_templates_invite_content":           $INVITE,
  "mailer_templates_magic_link_content":       $MAGIC,
  "mailer_templates_email_change_content":     $CHANGE,
  "mailer_templates_recovery_content":         $RESET,
  "mailer_templates_reauthentication_content": $REAUTH
}
EOF
)

echo "PATCH /v1/projects/${PROJECT_REF}/config/auth"
curl -sS -X PATCH \
  "https://api.supabase.com/v1/projects/${PROJECT_REF}/config/auth" \
  -H "Authorization: Bearer ${SUPABASE_ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" | python -m json.tool

echo
echo "Applied. Verify in dashboard:"
echo "  https://supabase.com/dashboard/project/${PROJECT_REF}/auth/templates"
