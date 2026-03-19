-- Phase 2: Add JobSpec storage and PDB path to jobs table
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS job_spec JSONB;
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS pdb_path TEXT;

-- Index for querying jobs by tool type
CREATE INDEX IF NOT EXISTS idx_jobs_tool ON public.jobs(tool);

COMMENT ON COLUMN public.jobs.job_spec IS 'Complete JobSpec JSON: tool, target, parameters, validation results, cost estimate';
COMMENT ON COLUMN public.jobs.pdb_path IS 'MinIO path to normalized PDB/CIF file: users/{uid}/jobs/{jid}/inputs/target.cif';
