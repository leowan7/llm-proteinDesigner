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


def generate_presigned_put_url(key: str, expires_in: int = 3600) -> str:
    """Presigned PUT URL for direct container upload to R2/MinIO.

    Args:
        key: S3 object key (path within the bucket).
        expires_in: URL expiry in seconds. Default 1 hour.

    Returns:
        Presigned URL string that allows a PUT request without credentials.
    """
    client = get_s3_client()
    return client.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket_name, "Key": key},
        ExpiresIn=expires_in,
    )


def generate_presigned_get_url(key: str, expires_in: int = 3600) -> str:
    """Presigned GET URL for download from R2/MinIO.

    Args:
        key: S3 object key (path within the bucket).
        expires_in: URL expiry in seconds. Default 1 hour.

    Returns:
        Presigned URL string that allows a GET request without credentials.
    """
    client = get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket_name, "Key": key},
        ExpiresIn=expires_in,
    )
