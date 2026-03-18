"""S3-compatible storage client for MinIO (local) and Cloudflare R2 (production)."""

import boto3
from botocore.config import Config

from config import settings


def get_s3_client():
    """
    Returns a boto3 S3 client configured for MinIO (local) or Cloudflare R2 (prod).

    Same code runs in both environments -- only env vars change.
    Per-user key structure: users/{user_id}/jobs/{job_id}/inputs/{filename}
    """
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )
