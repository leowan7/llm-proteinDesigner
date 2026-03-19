-- Phase 3: Billing and results schema
-- Adds Stripe customer tracking, job execution columns, and ranked design candidates table.

-- Stripe customer ID on users
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;

-- Job execution tracking columns
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS runpod_job_id TEXT;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS gpu_seconds INTEGER;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS gpu_cost_usd NUMERIC(10,4);
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS stage TEXT;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS error_category TEXT;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS results JSONB;

-- Design candidates (ranked outputs from a completed job)
CREATE TABLE IF NOT EXISTS public.job_candidates (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id      UUID NOT NULL REFERENCES public.jobs(id) ON DELETE CASCADE,
    rank        INTEGER NOT NULL,
    pdb_key     TEXT NOT NULL,
    scores      JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_job_candidates_job_id ON public.job_candidates(job_id);

ALTER TABLE public.job_candidates ENABLE ROW LEVEL SECURITY;

CREATE POLICY candidates_own ON public.job_candidates
    FOR ALL USING (
        auth.uid() = (SELECT user_id FROM public.jobs WHERE id = job_id)
    );
