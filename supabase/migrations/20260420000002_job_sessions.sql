-- Phase 6 of the Modal migration: chunked full-design job sessions.
--
-- Full-design binder campaigns can run 24-96 hours. Modal's @app.function
-- call timeout is capped at 24 hours, so campaigns >23hr MUST run as
-- multiple sessions with resume state stored in a modal.Volume between them.
--
-- This table records each session (one Modal FunctionCall per session) so the
-- orchestrator can auto-spawn the next session when the current one emits
-- ``chunk_status="paused_for_resume"`` in its webhook payload, AND so the
-- progress page can render a session timeline ("Session 3 of ~4").
--
-- See: .claude/plans/i-have-been-building-typed-whistle.md (Phase 6).

CREATE TABLE IF NOT EXISTS public.job_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL REFERENCES public.jobs(id) ON DELETE CASCADE,
    session_index int NOT NULL CHECK (session_index >= 0),

    -- The Modal FunctionCall.object_id for this session (or RunPod pod ID
    -- if the job was submitted while GPU_PROVIDER=runpod_emergency).
    provider_job_id text NOT NULL,

    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,

    -- Webhook reports one of: 'running' | 'paused_for_resume' | 'complete' | 'failed'
    -- 'paused_for_resume' triggers auto-spawn of session_index + 1 by the orchestrator.
    chunk_status text
        CHECK (chunk_status IN ('running', 'paused_for_resume', 'complete', 'failed')),

    designs_completed int DEFAULT 0 CHECK (designs_completed >= 0),
    error text,

    UNIQUE (job_id, session_index)
);

CREATE INDEX IF NOT EXISTS idx_job_sessions_job_id ON public.job_sessions (job_id);
CREATE INDEX IF NOT EXISTS idx_job_sessions_status ON public.job_sessions (chunk_status)
    WHERE chunk_status IN ('running', 'paused_for_resume');

COMMENT ON TABLE public.job_sessions IS
    'One row per Modal @app.function call within a chunked full-design job. '
    'Pilot jobs typically have 0 rows (they use the legacy single-call path) '
    'but the session_orchestrator can insert one row for pilots too for uniformity.';

COMMENT ON COLUMN public.job_sessions.chunk_status IS
    'paused_for_resume means the container hit SESSION_DEADLINE_UNIX, saved '
    'state to /state, and exited 0. The orchestrator spawns session_index+1.';
