-- Phase 2 of the Modal migration: pilot vs full-design job tiers + budget accounting.
--
-- Two first-class job tiers:
--   - 'pilot': small validation run (10-100 designs, <1hr, <$5). The default for
--              new users; also the GSD Phase 4 validation mechanism.
--   - 'full_design': real campaign (24-96 hr budget, multi-session chunked).
--                    Gated: users must have one successful pilot per tool before
--                    a full_design submission is accepted for that tool.
--
-- Budget columns support Phase 6 full-design chunking: total_budget_hours
-- caps the campaign; hours_consumed accumulates across sessions;
-- session_count increments as chunked sessions spawn.
--
-- See: .claude/plans/i-have-been-building-typed-whistle.md (Phase 2 + Phase 6).

ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS job_tier text NOT NULL DEFAULT 'pilot'
        CHECK (job_tier IN ('pilot', 'full_design')),
    ADD COLUMN IF NOT EXISTS total_budget_hours int NOT NULL DEFAULT 4
        CHECK (total_budget_hours > 0 AND total_budget_hours <= 96),
    ADD COLUMN IF NOT EXISTS hours_consumed numeric(6,2) NOT NULL DEFAULT 0
        CHECK (hours_consumed >= 0),
    ADD COLUMN IF NOT EXISTS session_count int NOT NULL DEFAULT 0
        CHECK (session_count >= 0);

-- Index supports the Phase 2 gating query — "has this user ever completed a
-- successful pilot of this tool?" — which runs on every full_design submit.
-- Partial index so it stays small (only complete rows matter).
CREATE INDEX IF NOT EXISTS idx_jobs_user_tier_tool_status
    ON public.jobs (user_id, job_tier, status)
    WHERE status = 'complete';

COMMENT ON COLUMN public.jobs.job_tier IS
    'Job tier: pilot (validation run) or full_design (real campaign). See Modal migration plan.';
COMMENT ON COLUMN public.jobs.total_budget_hours IS
    'Hard cap on total GPU hours for this job (1-96). Default 4 for pilot, 24 for full_design.';
COMMENT ON COLUMN public.jobs.hours_consumed IS
    'GPU hours consumed across all sessions of this job (accumulates during chunked runs).';
COMMENT ON COLUMN public.jobs.session_count IS
    'Number of Modal sessions spawned for this job (1 for pilot, 1..N for full_design).';
