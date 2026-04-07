-- Phase 5: Heartbeat tracking for stale job detection
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;
