-- Enable row-level security on the three public tables created without it:
-- audit_log (20260409000001_admin.sql), job_sessions
-- (20260420000002_job_sessions.sql) and retention_policy
-- (20260424000003_retention_tracking.sql). No policies are added.
-- backend/tests/integration/test_rls_on_every_public_table.py fails if any
-- ordinary table in schema public has it off.

ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.job_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.retention_policy ENABLE ROW LEVEL SECURITY;
