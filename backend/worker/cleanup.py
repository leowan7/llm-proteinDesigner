"""Orphan pod cleanup — safety net for pods that were not terminated by the webhook.

If the webhook fails to fire (network issue, container crash before POST, etc.),
the pod keeps running and billing continues. This task runs periodically to find
and terminate any pods that have been running longer than expected.

Usage:
    Run as a standalone script via cron or as an arq cron job:
        python -m worker.cleanup

    Or integrate into the arq worker via cron_jobs in WorkerSettings.
"""

import asyncio
import datetime
import logging

from config import settings
from db.connection import get_db_pool
from gpu.runpod import RunPodProvider
from jobs.notifications import send_failure_email
from worker.tasks import publish_status

logger = logging.getLogger(__name__)

# Pods running longer than this (seconds) are considered orphaned.
# Set to 2 hours — generous buffer above the longest pipeline (BindCraft at 1.5hr).
MAX_POD_LIFETIME_SECONDS = 7200

# Jobs with no heartbeat for this duration (seconds) are considered stale.
STALE_HEARTBEAT_SECONDS = 600


async def cleanup_orphan_pods() -> int:
    """Find and terminate RunPod pods that exceed the maximum lifetime.

    Compares active pods against the jobs table. A pod is orphaned if:
    - It has been running longer than MAX_POD_LIFETIME_SECONDS, OR
    - Its job is already in a terminal state (complete/failed/cancelled)

    Returns:
        Number of pods terminated.
    """
    if not settings.runpod_api_key:
        logger.info("RunPod API key not configured, skipping cleanup")
        return 0

    provider = RunPodProvider(api_key=settings.runpod_api_key)
    terminated = 0

    try:
        pods = await provider.list_pods()
    except Exception as exc:
        logger.error("Failed to list RunPod pods: %s", exc)
        return 0

    if not pods:
        return 0

    pool = await get_db_pool()

    for pod in pods:
        pod_id = pod.get("id", "")
        pod_name = pod.get("name", "")

        # Only manage pods created by Kendrew (name starts with "kendrew-").
        if not pod_name.startswith("kendrew-"):
            continue

        # Check if the job associated with this pod is already terminal.
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, status, started_at FROM public.jobs WHERE runpod_job_id = $1",
                pod_id,
            )

        should_terminate = False
        reason = ""

        if row and row["status"] in ("complete", "failed", "cancelled"):
            should_terminate = True
            reason = f"job {row['id']} already in terminal state: {row['status']}"

        elif row and row["started_at"]:
            import datetime
            elapsed = datetime.datetime.now(datetime.timezone.utc) - row["started_at"]
            if elapsed.total_seconds() > MAX_POD_LIFETIME_SECONDS:
                should_terminate = True
                reason = f"job {row['id']} running for {elapsed.total_seconds():.0f}s (max {MAX_POD_LIFETIME_SECONDS}s)"

        elif not row:
            # Pod exists but no matching job — orphaned.
            should_terminate = True
            reason = "no matching job in database"

        if should_terminate:
            logger.warning("Terminating orphan pod %s (%s): %s", pod_id, pod_name, reason)
            try:
                await provider.terminate_pod(pod_id)
                terminated += 1
            except Exception as exc:
                logger.error("Failed to terminate pod %s: %s", pod_id, exc)

    logger.info("Orphan cleanup complete: %d pods terminated", terminated)
    return terminated


async def detect_stale_jobs() -> int:
    """Find and fail jobs that have not sent a heartbeat within the threshold.

    A job is considered stale if:
    - It has status 'running' AND last_heartbeat_at is older than 10 minutes, OR
    - It has status 'running' AND started_at is older than 10 minutes AND
      last_heartbeat_at is NULL (container never sent a heartbeat).

    For each stale job the function:
    1. Marks the job as failed with an appropriate error category.
    2. Caps billed gpu_seconds at the stale threshold after last heartbeat.
    3. Terminates the RunPod pod if one is associated.
    4. Publishes an SSE failure event.
    5. Sends a failure notification email.

    Returns:
        Number of stale jobs killed.
    """
    pool = await get_db_pool()
    killed = 0

    # Query for stale running jobs
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, user_id, started_at, last_heartbeat_at, runpod_job_id
               FROM public.jobs
               WHERE status = 'running'
                 AND (
                     (last_heartbeat_at IS NOT NULL
                      AND last_heartbeat_at < NOW() - INTERVAL '10 minutes')
                     OR
                     (last_heartbeat_at IS NULL
                      AND started_at IS NOT NULL
                      AND started_at < NOW() - INTERVAL '10 minutes')
                 )"""
        )

    if not rows:
        return 0

    logger.warning("Found %d stale running job(s)", len(rows))

    provider = None
    if settings.runpod_api_key:
        provider = RunPodProvider(api_key=settings.runpod_api_key)

    for row in rows:
        job_id = str(row["id"])
        user_id = row["user_id"]
        started_at = row["started_at"]
        last_hb = row["last_heartbeat_at"]
        pod_id = row["runpod_job_id"]

        # Cap gpu_seconds: bill only up to (last_heartbeat + threshold) or
        # (started_at + threshold) if no heartbeat was ever received.
        reference_time = last_hb if last_hb else started_at
        if reference_time:
            billable_end = reference_time + datetime.timedelta(seconds=STALE_HEARTBEAT_SECONDS)
            gpu_seconds = int((billable_end - started_at).total_seconds())
        else:
            gpu_seconds = 0

        # Mark job as failed
        error_category = "Job timed out - no response from GPU"
        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE public.jobs
                   SET status = 'failed',
                       error_category = $1,
                       gpu_seconds = $2,
                       completed_at = NOW(),
                       updated_at = NOW()
                   WHERE id = $3""",
                error_category,
                gpu_seconds,
                job_id,
            )

        logger.warning(
            "Stale job %s marked failed (last_heartbeat=%s, gpu_seconds=%d)",
            job_id, last_hb, gpu_seconds,
        )

        # Terminate the RunPod pod if present
        if pod_id and provider:
            try:
                await provider.terminate_pod(pod_id)
                logger.info("Terminated pod %s for stale job %s", pod_id, job_id)
            except Exception as exc:
                logger.error("Failed to terminate pod %s for stale job %s: %s", pod_id, job_id, exc)

        # Publish SSE failure event
        await publish_status(job_id, "failed", "Job timed out")

        # Send failure email
        async with pool.acquire() as conn:
            user_row = await conn.fetchrow(
                "SELECT email FROM auth.users WHERE id = $1", user_id
            )
        if user_row:
            await send_failure_email(
                to_email=user_row["email"],
                job_id=job_id,
                error_category=error_category,
            )

        killed += 1

    logger.info("Stale job detection complete: %d job(s) killed", killed)
    return killed


async def check_daily_gpu_spend() -> None:
    """Check total GPU spend in the last 24 hours and alert if over threshold.

    Queries the jobs table for completed/cancelled/failed jobs in the last 24
    hours, sums gpu_cost_usd, and sends an alert email via Resend if over
    the configured threshold (gpu_daily_spend_alert_usd).
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT COALESCE(SUM(gpu_cost_usd), 0) as total_spend
               FROM public.jobs
               WHERE completed_at > NOW() - INTERVAL '24 hours'
               AND status IN ('complete', 'cancelled', 'failed')"""
        )

    total_spend = float(row["total_spend"]) if row else 0.0
    logger.info(
        "Daily GPU spend: $%.2f (threshold: $%.2f)",
        total_spend,
        settings.gpu_daily_spend_alert_usd,
    )

    if total_spend > settings.gpu_daily_spend_alert_usd:
        logger.warning(
            "GPU spend alert: $%.2f exceeds $%.2f threshold",
            total_spend,
            settings.gpu_daily_spend_alert_usd,
        )
        if settings.resend_api_key:
            import resend

            resend.api_key = settings.resend_api_key
            try:
                resend.Emails.send({
                    "from": settings.resend_from_email,
                    "to": ["leo@ranomics.com"],
                    "subject": f"[Kendrew] GPU spend alert: ${total_spend:.2f} in 24h",
                    "text": (
                        f"Daily GPU spend has reached ${total_spend:.2f}, "
                        f"exceeding the ${settings.gpu_daily_spend_alert_usd:.2f} threshold.\n\n"
                        f"Review active jobs and GPU usage in the Kendrew admin dashboard."
                    ),
                })
                logger.info("GPU spend alert email sent")
            except Exception as exc:
                logger.error("Failed to send GPU spend alert: %s", exc)


async def main():
    """Run orphan cleanup as a standalone script."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    count = await cleanup_orphan_pods()
    print(f"Terminated {count} orphan pod(s)")


if __name__ == "__main__":
    asyncio.run(main())
