-- Admin flag on users (per D-01)
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;

-- Audit log table (per D-26)
-- No RLS on audit_log — only accessible via admin router through postgres superuser connection.
CREATE TABLE IF NOT EXISTS public.audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_user_id   UUID NOT NULL REFERENCES public.users(id),
    action          TEXT NOT NULL,
    target_id       TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON public.audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_admin_user ON public.audit_log(admin_user_id);

-- Explicitly deny PostgREST roles and restrict to backend service role only.
-- The anon/authenticated roles must never read or write audit data directly.
REVOKE ALL ON public.audit_log FROM anon, authenticated;
GRANT SELECT, INSERT ON public.audit_log TO service_role;

-- Performance index for revenue queries (no existing index on jobs.created_at)
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON public.jobs(created_at DESC);

-- Bootstrap admin (per D-05) — idempotent, safe if email doesn't exist yet
-- NOTE: Run this manually after migration: UPDATE public.users SET is_admin = TRUE WHERE email = 'leo@ranomics.com';
