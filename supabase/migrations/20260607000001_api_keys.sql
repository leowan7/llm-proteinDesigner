-- Phase 13 Wave 0: api_keys table
-- Creates the API key storage table per RESEARCH §2.2 and CONTEXT.md D-01..D-04.
-- Column bcrypt_hash stores HMAC-SHA256 hex (64 chars) despite its name; see
-- COMMENT below. Algorithm decision documented in RESEARCH §2.10.

CREATE TABLE public.api_keys (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    created_by_user_id      UUID NOT NULL REFERENCES public.users(id) ON DELETE SET NULL,
    name                    TEXT NOT NULL,
    prefix                  TEXT NOT NULL,        -- first 12 chars of plaintext: "bw_live_XXXX"
    bcrypt_hash             TEXT NOT NULL,        -- actually HMAC-SHA256 hex; see COMMENT below
    role_at_creation        public.org_role NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at            TIMESTAMPTZ,
    revoked_at              TIMESTAMPTZ,
    CONSTRAINT name_not_blank CHECK (length(btrim(name)) > 0)
);

-- Fast prefix lookup for authentication (only active keys)
CREATE INDEX idx_api_keys_org ON public.api_keys(organization_id) WHERE revoked_at IS NULL;

-- Fast prefix lookup for authentication (only active keys)
CREATE INDEX idx_api_keys_prefix ON public.api_keys(prefix) WHERE revoked_at IS NULL;

COMMENT ON COLUMN public.api_keys.bcrypt_hash IS
    'HMAC-SHA256 (hex) of the plaintext key + server-side pepper. Column name retained from D-03 spec for compatibility; algorithm changed per Phase 13 RESEARCH item 2.10 (high-entropy tokens do not need a slow hash).';
