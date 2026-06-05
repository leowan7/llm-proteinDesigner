-- Phase 13 Wave 0: api_key_idempotency table
-- Three-state idempotency lifecycle table per RESEARCH §2.9 and CONTEXT.md D-05.
-- Stores (api_key_id, idempotency_key) → (body_hash, status, response) for 24h.
-- Swept by an arq-scheduled reaper cron using idempotency_ttl_hours (default 25h).

CREATE TABLE public.api_key_idempotency (
    api_key_id          UUID NOT NULL REFERENCES public.api_keys(id) ON DELETE CASCADE,
    idempotency_key     TEXT NOT NULL,
    request_body_hash   TEXT NOT NULL,            -- sha256 of canonicalized JSON body
    status              TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'completed'
    response_status     INT,                              -- NULL until completed
    response_body       JSONB,                            -- NULL until completed
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,                      -- set on transition to 'completed'
    PRIMARY KEY (api_key_id, idempotency_key),
    CONSTRAINT status_valid CHECK (status IN ('pending', 'completed'))
);

-- Index to support the reaper cron (DELETE WHERE created_at < now() - INTERVAL 'N hours')
CREATE INDEX idx_api_key_idem_created ON public.api_key_idempotency(created_at);
