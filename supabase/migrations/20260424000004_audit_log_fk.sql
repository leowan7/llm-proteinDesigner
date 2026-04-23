-- Phase 10 CR-01: relax audit_log.admin_user_id FK to ON DELETE SET NULL
--
-- Problem (per 10-REVIEW.md CR-01): the original FK in
-- 20260409000001_admin.sql declares audit_log.admin_user_id as NOT NULL
-- REFERENCES public.users(id) WITH NO cascade policy. When a GDPR hard-delete
-- fires (backend/user/deletion.py → delete_auth_user), Supabase cascades from
-- auth.users into public.users. That cascade then aborts with a foreign-key
-- violation because the deleting user's own audit_log row still references
-- their soon-to-be-deleted public.users.id. Result: auth.users succeeded but
-- public.users remains orphaned.
--
-- Fix: preserve the audit trail by allowing the reference to become NULL on
-- user deletion. This is the standard non-repudiation-preserving pattern —
-- the audit record survives as an orphan ("deleted user"), which still
-- satisfies the "who attempted what, when" requirement without blocking the
-- user's GDPR right-to-erasure.

ALTER TABLE public.audit_log
    DROP CONSTRAINT IF EXISTS audit_log_admin_user_id_fkey,
    ALTER COLUMN admin_user_id DROP NOT NULL;

ALTER TABLE public.audit_log
    ADD CONSTRAINT audit_log_admin_user_id_fkey
    FOREIGN KEY (admin_user_id) REFERENCES public.users(id)
    ON DELETE SET NULL;

COMMENT ON COLUMN public.audit_log.admin_user_id IS
    'References public.users(id). Nullable: becomes NULL when the referenced user is deleted (ON DELETE SET NULL) so the audit row survives hard-delete as an orphan for non-repudiation. Originally declared NOT NULL in 20260409000001_admin.sql; relaxed here.';
