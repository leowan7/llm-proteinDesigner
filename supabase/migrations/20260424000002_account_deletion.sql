-- Phase 10 Plan 4: GDPR deletion + export tracking
--
-- Columns added:
--   deletion_requested_at     TIMESTAMPTZ — timestamp of Article 17 deletion request
--                                           (NULL = no pending deletion); executor cron
--                                           hard-deletes ~30 days after this timestamp.
--   last_export_requested_at  TIMESTAMPTZ — most recent Article 20 export request
--   last_export_url           TEXT        — presigned R2 URL to the most recent export ZIP
--   last_export_expires_at    TIMESTAMPTZ — when last_export_url stops working (24hr post-request)

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_export_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_export_url TEXT,
    ADD COLUMN IF NOT EXISTS last_export_expires_at TIMESTAMPTZ;

COMMENT ON COLUMN public.users.deletion_requested_at IS
    'Timestamp of GDPR Article 17 deletion request; hard-delete executes ~30 days later via daily cron (worker/deletion_cron.py). NULLable so users can cancel during grace.';
COMMENT ON COLUMN public.users.last_export_requested_at IS
    'Timestamp of the most recent data export request (GDPR Article 20).';
COMMENT ON COLUMN public.users.last_export_url IS
    'Presigned R2 URL for the most recent export ZIP (expires at last_export_expires_at).';
COMMENT ON COLUMN public.users.last_export_expires_at IS
    'TTL for last_export_url; after this moment the GET /user/data-export status flips to "expired".';
