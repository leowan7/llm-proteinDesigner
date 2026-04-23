-- Phase 10: Legal & Compliance — ToS acceptance + data retention
--
-- Columns added:
--   tos_accepted_at     TIMESTAMPTZ — timestamp of most recent ToS acceptance (NULL = never accepted)
--   tos_version         TEXT        — version string the user accepted (e.g. "2026-04-23");
--                                     compared against backend TOS_CURRENT_VERSION at login
--   data_retention_days INT         — per-user override of the 90-day retention default
--                                     read by Plan 10-05 retention cron; constrained 30-365

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS tos_accepted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS tos_version TEXT,
    ADD COLUMN IF NOT EXISTS data_retention_days INT NOT NULL DEFAULT 90
        CHECK (data_retention_days BETWEEN 30 AND 365);

COMMENT ON COLUMN public.users.tos_accepted_at IS
    'Timestamp of most recent ToS acceptance; NULL means the user signed up before Phase 10 and must re-accept on next login.';
COMMENT ON COLUMN public.users.tos_version IS
    'Version string accepted (e.g. "2026-04-23"); drift vs backend TOS_CURRENT_VERSION triggers re-acceptance modal.';
COMMENT ON COLUMN public.users.data_retention_days IS
    'Per-user retention window in days (30-365). Defaults to 90. Consumed by the retention cron (Plan 10-05).';
