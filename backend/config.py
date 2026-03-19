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

    # Anthropic (Claude agent)
    anthropic_api_key: str = ""

    # External API base URLs
    rcsb_base_url: str = "https://files.rcsb.org"
    uniprot_base_url: str = "https://rest.uniprot.org"

    # Agent
    agent_model: str = "claude-sonnet-4-5"
    agent_max_tokens: int = 2048
    agent_session_ttl_seconds: int = 3600  # Redis session TTL: 1 hour

    class Config:
        env_file = ".env.local"
        env_file_encoding = "utf-8"


settings = Settings()
