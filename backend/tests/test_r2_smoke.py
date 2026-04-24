"""S3/R2 roundtrip smoke test (SC 5).

Uploads a 1KB object to the configured bucket via boto3, generates a presigned
GET URL, downloads via HTTP, asserts the bytes match, and cleans up. Gated on
``S3_SMOKE_ENABLED`` because the test requires real S3-compatible credentials
(R2 in prod, MinIO locally). CI runners without the flag skip.

Reference: .planning/phases/11-deployment/11-VALIDATION.md row 5 (R2 happy path).
"""

import os
import uuid

import pytest

os.environ.setdefault("TESTING", "true")


@pytest.mark.skipif(
    not os.environ.get("S3_SMOKE_ENABLED"),
    reason="Enable with S3_SMOKE_ENABLED=1 -- requires MinIO or R2 creds",
)
def test_r2_roundtrip():
    """Upload, presign-GET, download, and delete a 1KB object.

    Fails fast with an informative AssertionError if the downloaded bytes do
    not match the uploaded payload (the whole point of the smoke test).
    """
    import boto3
    import urllib.request

    from config import settings

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="auto",
    )

    payload = os.urandom(1024)  # 1KB random bytes
    key = f"smoke/{uuid.uuid4()}.txt"
    bucket = settings.s3_bucket_name

    try:
        client.put_object(Bucket=bucket, Key=key, Body=payload)

        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=60,
        )

        with urllib.request.urlopen(url, timeout=10) as resp:
            downloaded = resp.read()

        assert downloaded == payload, (
            f"Round-trip mismatch: uploaded {len(payload)} bytes, "
            f"downloaded {len(downloaded)} bytes"
        )
    finally:
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            # Cleanup-best-effort: don't mask the real assertion failure.
            pass
