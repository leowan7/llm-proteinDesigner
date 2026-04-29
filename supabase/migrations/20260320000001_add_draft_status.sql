-- Add 'draft' to jobs status CHECK constraint for wizard-created jobs
ALTER TABLE public.jobs DROP CONSTRAINT IF EXISTS jobs_status_check;
ALTER TABLE public.jobs ADD CONSTRAINT jobs_status_check
    CHECK (status IN ('draft', 'pending', 'queued', 'running', 'complete', 'failed', 'cancelled'));
