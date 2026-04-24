"""Application configuration loaded from environment variables."""

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All application settings. Read from .env.local via docker-compose env_file."""

    # Supabase
    supabase_url: str = "http://127.0.0.1:54321"
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""

    # Legal / Compliance (Phase 10)
    tos_current_version: str = "2026-04-23"
    privacy_current_version: str = "2026-04-23"

    # Database (Supabase local Postgres)
    database_url: str = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"

    # S3 / MinIO / R2
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket_name: str = "protein-designer"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # CSRF
    csrf_secret: str = "local-dev-csrf-secret-change-in-prod"
    cookie_secure: bool = False

    # CORS
    cors_origins: list[str] = ["http://localhost:5173"]

    # App
    debug: bool = True
    testing: bool = False

    # Sentry
    sentry_dsn: str = ""

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_default: str = "60/minute"

    # Anthropic (Claude agent)
    anthropic_api_key: str = ""

    # External API base URLs
    rcsb_base_url: str = "https://files.rcsb.org"
    uniprot_base_url: str = "https://rest.uniprot.org"

    # Agent
    agent_model: str = "claude-sonnet-4-6"
    agent_max_tokens: int = 2048
    agent_session_ttl_seconds: int = 3600  # Redis session TTL: 1 hour

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_meter_event_name: str = "gpu_seconds"

    # GPU provider selection. Values:
    #   "modal" (default) — ModalProvider; the current production path.
    #   "runpod_emergency" — RunPodProvider; break-glass rollback only.
    # A bare "runpod" is intentionally rejected by gpu/__init__.py to prevent
    # accidental RunPod use. See .claude/plans/i-have-been-building-typed-whistle.md.
    gpu_provider: str = "modal"

    # Modal
    modal_token_id: str = ""
    modal_token_secret: str = ""
    modal_workspace: str = ""        # e.g. "leowan7" (Modal workspace slug)
    modal_environment: str = "main"  # Modal environment within the workspace

    # RunPod (quarantined — retained for emergency rollback only)
    runpod_api_key: str = ""

    # Dual-secret webhook rotation (Phase 11 D-10, amended 2026-04-24).
    # Single shared secret covers both Modal and RunPod webhook HMAC — see
    # .planning/phases/11-deployment/11-CONTEXT.md §D-10.
    # Backend tries webhook_hmac_secret first, falls back to webhook_hmac_secret_prev
    # during rotation grace windows. Rotation runbook in docs/deploy.md (Plan 11-05).
    webhook_hmac_secret: str = ""
    webhook_hmac_secret_prev: str = ""

    # DEPRECATED — retained for one release cycle as a backwards-compat alias.
    # Reads are resolved to webhook_hmac_secret below via model_post_init.
    # Remove after next phase once Railway Variables are fully migrated to WEBHOOK_HMAC_SECRET.
    runpod_webhook_secret: str = ""

    # RunPod Pod configuration (replaces per-tool serverless endpoint IDs)
    runpod_gpu_type_ids: list[str] = ["NVIDIA RTX A6000"]  # Priority-ordered fallbacks
    runpod_container_disk_gb: int = 20
    runpod_network_volume_id: str = ""  # Optional — weights baked into images by default
    runpod_container_registry_auth_id: str = ""  # GHCR auth credential ID on RunPod

    # Docker images per tool (pulled by RunPod pods)
    runpod_image_rfdiffusion: str = "ghcr.io/leowan7/kendrew-rfdiffusion:v11"
    runpod_image_rfantibody: str = ""
    runpod_image_bindcraft: str = "ghcr.io/leowan7/kendrew-bindcraft:v7"
    runpod_image_boltzgen: str = "ghcr.io/leowan7/kendrew-boltzgen:v3"
    runpod_image_pxdesign: str = ""

    # Resend (for job notifications)
    resend_api_key: str = ""
    resend_from_email: str = "Kendrew.AI <jobs@kendrew.ai>"

    # App base URL (used in email links and Stripe return URLs)
    app_base_url: str = "http://localhost:8000"

    # Upload URL expiry for on-demand container uploads (seconds)
    upload_url_expiry_seconds: int = 3600

    # GPU spend alerting
    gpu_daily_spend_alert_usd: float = 50.0

    # SSE connection limits
    max_sse_connections_per_user: int = 3

    # Sentry frontend (separate DSN for browser project)
    sentry_dsn_frontend: str = ""

    # GPU pricing (dollars per second — A6000 at $0.33/hr = $0.0000917/sec)
    gpu_price_per_second: float = 0.0000917
    gpu_markup_percent: float = 30.0

    def model_post_init(self, __context) -> None:
        """Resolve deprecated runpod_webhook_secret to webhook_hmac_secret if the new one is empty.

        Phase 11 D-10 rename: Railway Variables may still be set as
        RUNPOD_WEBHOOK_SECRET during the rotation grace window. If the operator
        set only the old env var, fall back to it so webhook validation keeps
        working. Remove this hook after next phase.
        """
        if not self.webhook_hmac_secret and self.runpod_webhook_secret:
            self.webhook_hmac_secret = self.runpod_webhook_secret

    class Config:
        env_file = ("../.env.local", ".env.local")
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
