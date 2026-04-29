-- Add a user-editable name column to jobs for easy identification.
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS name TEXT;
