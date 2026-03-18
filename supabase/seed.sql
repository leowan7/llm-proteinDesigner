-- Pre-verified test user for local development
-- Credentials: test@example.com / Password123!
-- This user bypasses email verification for convenience

CREATE EXTENSION IF NOT EXISTS pgcrypto;

INSERT INTO auth.users (
    id,
    instance_id,
    email,
    encrypted_password,
    email_confirmed_at,
    created_at,
    updated_at,
    aud,
    role
) VALUES (
    '00000000-0000-0000-0000-000000000001',
    '00000000-0000-0000-0000-000000000000',
    'test@example.com',
    crypt('Password123!', gen_salt('bf')),
    NOW(),
    NOW(),
    NOW(),
    'authenticated',
    'authenticated'
) ON CONFLICT (id) DO NOTHING;

-- Mirror in public.users (required by FK on jobs table)
INSERT INTO public.users (id, email)
VALUES ('00000000-0000-0000-0000-000000000001', 'test@example.com')
ON CONFLICT (id) DO NOTHING;
