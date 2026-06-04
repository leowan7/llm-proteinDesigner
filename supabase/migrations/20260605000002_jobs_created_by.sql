-- Phase 12 Plan 12-03: track who launched each job (org has many possible launchers).
-- Backfill from existing user_id so existing rows have a valid value.

ALTER TABLE public.jobs ADD COLUMN created_by_user_id UUID REFERENCES public.users(id);

UPDATE public.jobs SET created_by_user_id = user_id WHERE created_by_user_id IS NULL;

ALTER TABLE public.jobs ALTER COLUMN created_by_user_id SET NOT NULL;

CREATE INDEX idx_jobs_created_by ON public.jobs(created_by_user_id);

COMMENT ON COLUMN public.jobs.created_by_user_id IS
  'Phase 12: user who launched this job. organization_id is the billing scope; this column is the audit trail / UI "launched by" field.';
