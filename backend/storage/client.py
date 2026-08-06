"""S3-compatible storage client for MinIO (local) and Cloudflare R2 (production)."""

import logging
import os

import boto3
from botocore.config import Config
from config import settings

logger = logging.getLogger(__name__)


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


def _looks_like_local_path(path: str) -> bool:
    """Heuristic: absolute filesystem paths start with ``/`` on POSIX (backend
    container) or a drive letter on Windows. S3 keys never start with ``/``.
    """
    if not path:
        return False
    if path.startswith("/"):
        return True
    if len(path) >= 3 and path[1:3] == ":\\":  # e.g. "C:\\..."
        return True
    return False


def ensure_pdb_in_s3(target_pdb_path: str, user_id: str, job_id: str) -> str:
    """Idempotently ensure the target PDB lives in the bucket, return its key.

    The agent's ``resolve_structure`` tool writes fetched PDBs to a local path
    (``/tmp/structures/<ID>.pdb``) inside the backend container. Downstream
    workers need an S3 key so the GPU container can fetch via presigned URL.

    Contract:
      * If ``target_pdb_path`` already looks like an S3 key (no leading slash),
        it is returned unchanged — caller should keep presigning against it.
      * If it's a local path and the file exists, it's uploaded under
        ``users/{user_id}/jobs/{job_id}/inputs/target.pdb`` and that key is
        returned. The original local file is left in place.
      * If it's a local path that doesn't exist, raises FileNotFoundError so
        the worker fails fast with a clear error instead of silently 404-ing
        on the container side.
    """
    if not _looks_like_local_path(target_pdb_path):
        return target_pdb_path

    if not os.path.isfile(target_pdb_path):
        raise FileNotFoundError(
            f"Target PDB not found at local path: {target_pdb_path}. "
            "The agent's resolve_structure step did not persist a file, or "
            "the backend container was restarted and /tmp was wiped."
        )

    filename = os.path.basename(target_pdb_path) or "target.pdb"
    ext = os.path.splitext(filename)[1].lower() or ".pdb"
    key = f"users/{user_id}/jobs/{job_id}/inputs/target{ext}"

    client = get_s3_client()
    logger.info(
        "ensure_pdb_in_s3: uploading %s -> s3://%s/%s",
        target_pdb_path, settings.s3_bucket_name, key,
    )
    with open(target_pdb_path, "rb") as fh:
        client.put_object(
            Bucket=settings.s3_bucket_name,
            Key=key,
            Body=fh.read(),
            ContentType="chemical/x-pdb",
        )
    return key


def list_and_delete_user_objects(user_id: str) -> int:
    """Enumerate and delete all objects under ``users/{user_id}/``. Return count deleted.

    Used by the GDPR Article 17 hard-delete flow (Plan 10-04) to purge every
    per-user object — job inputs, outputs, and export ZIPs — before the
    Supabase auth user is deleted.

    Uses ``list_objects_v2`` via the paginator (which yields pages of <=1000
    contents) and ``delete_objects`` per page (S3's max-keys-per-delete limit
    is also 1000, so the paginator's chunking lines up exactly). Safe to
    re-run: on an already-empty prefix, ``Contents`` is empty and no delete
    call is issued, returning 0 without error.

    Raises:
        RuntimeError: If any object returned an error in the ``Errors``
            section of the ``delete_objects`` response. Caller (executor
            cron) aborts the hard-delete so the user row stays pending and
            a subsequent cron run retries.
    """
    client = get_s3_client()
    prefix = f"users/{user_id}/"
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.s3_bucket_name, Prefix=prefix):
        contents = page.get("Contents") or []
        if not contents:
            continue
        keys = [{"Key": obj["Key"]} for obj in contents]
        resp = client.delete_objects(
            Bucket=settings.s3_bucket_name,
            Delete={"Objects": keys, "Quiet": True},
        )
        deleted += len(keys)
        errors = resp.get("Errors") or []
        if errors:
            logger.error(
                "list_and_delete_user_objects: delete_objects errors for user=%s: %s",
                user_id, errors,
            )
            raise RuntimeError(
                f"delete_objects reported {len(errors)} errors for user {user_id}; see log"
            )
    logger.info("list_and_delete_user_objects: user=%s deleted=%d", user_id, deleted)
    return deleted


def delete_job_objects(user_id: str, job_id: str) -> int:
    """Delete all R2 objects under ``users/{user_id}/jobs/{job_id}/``.

    Used by the Plan 10-05 retention cron to purge per-job object storage
    (input PDBs, output results) once a job crosses its retention deadline.
    Scoped to a single job prefix so the cron cannot fan out beyond the row
    it's processing.

    Uses the same ``list_objects_v2`` paginator + batched ``delete_objects``
    pattern as :func:`list_and_delete_user_objects`. Safe on an empty/missing
    prefix — returns 0.

    Args:
        user_id: Owner's user UUID string.
        job_id:  Target job UUID string.

    Returns:
        Count of S3 objects deleted.

    Raises:
        RuntimeError: If any object key returned an error in the
            ``Errors`` section of the ``delete_objects`` response. The cron
            per-row try/except catches this and leaves ``retention_deleted_at``
            NULL so the next cron run retries.
    """
    client = get_s3_client()
    prefix = f"users/{user_id}/jobs/{job_id}/"
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.s3_bucket_name, Prefix=prefix):
        contents = page.get("Contents") or []
        if not contents:
            continue
        keys = [{"Key": obj["Key"]} for obj in contents]
        resp = client.delete_objects(
            Bucket=settings.s3_bucket_name,
            Delete={"Objects": keys, "Quiet": True},
        )
        deleted += len(keys)
        errors = resp.get("Errors") or []
        if errors:
            logger.error(
                "delete_job_objects errors job=%s user=%s: %s",
                job_id, user_id, errors,
            )
            raise RuntimeError(
                f"delete_objects reported {len(errors)} errors for job {job_id}; see log"
            )
    logger.info(
        "delete_job_objects: user=%s job=%s deleted=%d",
        user_id, job_id, deleted,
    )
    return deleted
