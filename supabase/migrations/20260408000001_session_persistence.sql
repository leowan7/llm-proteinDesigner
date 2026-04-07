-- Phase 6: Persistent session storage
-- Adds sessions, session_messages tables with RLS, links jobs to sessions,
-- and adds user profile columns (display_name, notification_preferences).

CREATE TABLE public.sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    title           TEXT,
    agent_history   JSONB DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata        JSONB DEFAULT '{}'
);

CREATE TABLE public.session_messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID NOT NULL REFERENCES public.sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL DEFAULT '',
    cards       JSONB,
    sort_order  INTEGER NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sessions_user_updated ON public.sessions(user_id, updated_at DESC);
CREATE INDEX idx_session_messages_session ON public.session_messages(session_id, sort_order);

-- Link jobs to sessions (nullable; jobs created outside a session have no link)
ALTER TABLE public.jobs ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES public.sessions(id);

-- User profile columns for settings page
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS display_name TEXT;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS notification_preferences JSONB DEFAULT '{"job_complete": true, "job_failure": true}';

-- RLS: users see only their own sessions and messages
ALTER TABLE public.sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.session_messages ENABLE ROW LEVEL SECURITY;

CREATE POLICY sessions_own ON public.sessions
    FOR ALL USING (auth.uid() = user_id);

CREATE POLICY session_messages_own ON public.session_messages
    FOR ALL USING (
        session_id IN (SELECT id FROM public.sessions WHERE user_id = auth.uid())
    );
