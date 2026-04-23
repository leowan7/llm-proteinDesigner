-- Phase 10 CR-02: replace persisted presigned URL with an object-key pointer
--
-- Problem (per 10-REVIEW.md CR-02): migration 20260424000002_account_deletion.sql
-- introduced public.users.last_export_url TEXT holding the full presigned R2
-- GET URL for the latest GDPR export ZIP. A presigned URL is a bearer
-- credential — anyone who can SELECT from users (DB backups, log aggregation,
-- support dashboards, a future SSRF) can download the ZIP without
-- authentication for 24 hours. GDPR export ZIPs carry the most sensitive
-- payload we hold (full profile + sessions + jobs).
--
-- Fix: persist only the object key. The backend re-mints a presigned URL on
-- each authenticated GET /user/data-export call, so only the account owner
-- (who presents a valid access_token cookie) can obtain a working URL. A
-- shorter (1 hour) user-facing TTL is applied at re-mint time in the
-- endpoint handler.

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS last_export_key TEXT;

ALTER TABLE public.users
    DROP COLUMN IF EXISTS last_export_url;

COMMENT ON COLUMN public.users.last_export_key IS
    'R2 object key for the last GDPR data-export ZIP (e.g. users/{user_id}/exports/export-{ts}.zip). Re-presigned on each authenticated GET /user/data-export call with a user-facing 1-hour TTL. Replaced last_export_url (dropped in 20260424000005) which stored a bearer-credential URL verbatim.';

-- last_export_expires_at still tracks when the export is considered stale;
-- endpoint re-mints the presigned URL with expires_in = (last_export_expires_at
-- - now()) capped at 3600 seconds.
