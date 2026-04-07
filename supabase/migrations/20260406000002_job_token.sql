-- Phase 5: Job token for container-to-backend authentication
-- Containers use this token to request fresh presigned upload URLs on-demand,
-- eliminating pre-generated URLs that could expire during long-running jobs.
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS job_token TEXT;
