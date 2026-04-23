-- Phase 10 Plan 5: Data retention tracking
--
-- retention_warning_sent_at : stamped when the T-7-day warning email was sent (idempotent)
-- retention_deleted_at      : stamped when the object-storage + DB cleanup completed
--
-- Jobs created before policy_effective_from are exempt — we do not retroactively
-- delete pre-policy data without user notification. Users can explicitly opt-in
-- via a future "delete now" action (out of scope for Plan 10-05).

ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS retention_warning_sent_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS retention_deleted_at TIMESTAMPTZ;

COMMENT ON COLUMN public.jobs.retention_warning_sent_at IS
    'Timestamp of the 7-day-before-expiry warning email. NULL = not yet sent.';
COMMENT ON COLUMN public.jobs.retention_deleted_at IS
    'Timestamp when retention cron hard-deleted object storage for this job. Row is retained so users see the expired state.';

-- Add 'expired' to jobs.status check constraint (alongside existing statuses).
-- The constraint is named `jobs_status_check` — confirmed in migration 20260320000001_add_draft_status.sql.
ALTER TABLE public.jobs DROP CONSTRAINT IF EXISTS jobs_status_check;
ALTER TABLE public.jobs ADD CONSTRAINT jobs_status_check
    CHECK (status IN ('draft','pending','queued','running','complete','failed','cancelled','expired'));

-- Policy effective date — single-row config table
CREATE TABLE IF NOT EXISTS public.retention_policy (
    id                    INT PRIMARY KEY DEFAULT 1,
    policy_effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT retention_policy_singleton CHECK (id = 1)
);

-- Seed the single row (idempotent).
INSERT INTO public.retention_policy (id, policy_effective_from)
VALUES (1, now())
ON CONFLICT (id) DO NOTHING;

-- No RLS on retention_policy — it's read-only reference data accessed by the worker.
-- (Service role reads directly; not exposed to end-user via PostgREST.)
