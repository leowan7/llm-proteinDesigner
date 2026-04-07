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

    # RunPod
    runpod_api_key: str = ""
    runpod_webhook_secret: str = ""

    # RunPod Pod configuration (replaces per-tool serverless endpoint IDs)
    runpod_gpu_type_ids: list[str] = ["NVIDIA RTX A6000"]  # Priority-ordered fallbacks
    runpod_container_disk_gb: int = 20
    runpod_network_volume_id: str = ""  # Optional — weights baked into images by default
    runpod_container_registry_auth_id: str = ""  # GHCR auth credential ID on RunPod

    # Docker images per tool (pulled by RunPod pods)
    runpod_image_rfdiffusion: str = "ghcr.io/leowan7/kendrew-rfdiffusion:v6"
    runpod_image_rfantibody: str = ""
    runpod_image_bindcraft: str = ""
    runpod_image_boltzgen: str = ""
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

    class Config:
        env_file = ".env.local"
        env_file_encoding = "utf-8"


settings = Settings()
