"""RunPod webhook handler for GPU Pod job completion.

The container running in a RunPod Pod POSTs results to this endpoint when the
pipeline finishes (success or failure). The handler:
1. Validates the request (signature check in prod, open in dev).
2. Resolves the internal job by job_id from the payload.
3. Calculates GPU seconds from started_at timestamp.
4. Updates DB status and results.
5. Records billing via Stripe Billing Meters API.
6. Terminates the RunPod pod to stop billing.
7. Sends email notification via Resend.
8. Publishes SSE status event via Redis pub/sub.

Payload structure (POSTed by run_pipeline.py in the container):
    id       str   Kendrew job UUID
    pod_id   str   RunPod pod ID (for termination)
    status   str   "COMPLETED" or "FAILED"
    output   dict  Pipeline results (candidates, counts, etc.)
    error    dict  Error info if status is "FAILED"
"""

import asyncio
import datetime
import hashlib
import hmac
import json
import logging

import sentry_sdk
from billing.stripe_client import record_gpu_usage
from config import settings
from db.connection import get_db_pool
from fastapi import APIRouter, HTTPException, Request
from gpu import get_provider
from jobs.notifications import send_completion_email, send_failure_email
from storage.client import list_job_design_keys
from worker.tasks import publish_status, update_job_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Map RunPod terminal statuses to internal JobStatus values.
_RUNPOD_STATUS_MAP: dict[str, str] = {
    "COMPLETED": "complete",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
    "TIMED_OUT": "failed",
}


def validate_webhook_signature(
    body: bytes,
    signature: str | None,
    current_secret: str,
    prev_secret: str | None = None,
) -> str:
    """Validate HMAC-SHA256 signature against the current secret, then _PREV.

    Dual-secret rotation per Phase 11 D-10: during a rotation grace window,
    the backend accepts signatures made with either the current or previous
    secret. When the _PREV secret matches, a WARNING is logged so the rotation
    runbook operator knows traffic is still flowing against the old secret.

    Args:
        body: Raw request body bytes.
        signature: Value of the signature header (X-RunPod-Signature or X-Modal-Signature).
        current_secret: settings.webhook_hmac_secret.
        prev_secret: settings.webhook_hmac_secret_prev (may be None/empty).

    Returns:
        "current" if the current secret matched, "prev" if the previous matched,
        "dev-skip" if both secrets are empty (local dev — validation skipped).

    Raises:
        HTTPException(401): Missing or invalid signature.
    """
    if not current_secret and not prev_secret:
        return "dev-skip"

    if not signature:
        raise HTTPException(status_code=401, detail="Missing signature")

    for label, secret in (("current", current_secret), ("prev", prev_secret)):
        if not secret:
            continue
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature):
            if label == "prev":
                logger.warning(
                    "Webhook signed with PREV secret — rotation window active"
                )
            return label

    raise HTTPException(status_code=401, detail="Invalid signature")


# Backwards-compat shim: old callers still invoke validate_runpod_signature.
# Remove after grep confirms no usages remain.
def validate_runpod_signature(body: bytes, signature: str | None) -> None:
    """Deprecated — use validate_webhook_signature directly."""
    validate_webhook_signature(
        body,
        signature,
        settings.webhook_hmac_secret,
        settings.webhook_hmac_secret_prev,
    )


@router.post("/runpod")
async def runpod_webhook(request: Request):
    """Handle pipeline completion webhooks from RunPod Pod containers.

    The container POSTs results here when the pipeline finishes. This handler
    processes results, records billing, terminates the pod, and notifies the user.

    Returns:
        {"received": True} on all valid requests.
    """
    body = await request.body()
    signature = request.headers.get("X-RunPod-Signature") or request.headers.get("X-Modal-Signature")
    validate_webhook_signature(
        body,
        signature,
        settings.webhook_hmac_secret,
        settings.webhook_hmac_secret_prev,
    )

    payload = json.loads(body)
    job_id: str = payload.get("id", "")
    pod_id: str = payload.get("pod_id", "")
    runpod_status: str = payload.get("status", "")

    # Phase 6 addition: ``chunk_status`` signals how a session ended.
    # 'paused_for_resume' = container checkpointed before Modal's 23hr timeout
    #                        and exited 0; orchestrator must spawn the next session.
    # 'complete' / 'failed' = terminal — proceed with the existing completion path.
    # Absent = legacy webhook (one-shot job); treated as terminal.
    chunk_status: str | None = payload.get("chunk_status")

    # Early dispatch: if the container reports paused_for_resume, enqueue the
    # next session and return WITHOUT running the terminate/billing/email path.
    if chunk_status == "paused_for_resume" and job_id:
        from worker.session_orchestrator import handle_chunk_status

        designs_so_far = int(payload.get("output", {}).get("designs_completed_so_far", 0))
        await handle_chunk_status(
            job_id=job_id,
            chunk_status="paused_for_resume",
            designs_completed=designs_so_far,
        )
        # SSE event so the progress page shows session-boundary state.
        await publish_status(job_id, "running", "Session paused — spawning next session")
        return {"received": True, "action": "resume_enqueued"}

    internal_status = _RUNPOD_STATUS_MAP.get(runpod_status)
    if not internal_status:
        # Non-terminal status or unrecognised value — acknowledge and ignore.
        return {"received": True}

    # Replay protection: reject payloads older than 5 minutes
    payload_timestamp = payload.get("timestamp")
    if payload_timestamp:
        try:
            sent_at = datetime.datetime.fromisoformat(payload_timestamp)
            age = datetime.datetime.now(datetime.UTC) - sent_at
            if age.total_seconds() > 300:  # 5 minutes
                logger.warning("Rejected stale webhook: job_id=%s age=%.0fs", job_id, age.total_seconds())
                return {"received": True}
        except (ValueError, TypeError):
            pass  # If timestamp is malformed, skip replay check (don't break existing webhooks)

    pool = await get_db_pool()

    # Resolve the internal job by job UUID (pod webhook sends job_id directly).
    # Pull `tool` from DB so the completion email can name it correctly --
    # the container's webhook payload doesn't include job_spec, so reading
    # tool from payload.output.job_spec.tool always returned "Unknown"
    # (discovered 2026-06-03 right after SC 6 close-out).
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, user_id, started_at, runpod_job_id, tool FROM public.jobs WHERE id = $1",
            job_id,
        )

    if not row:
        # Try fallback: look up by pod ID stored in runpod_job_id column.
        if pod_id:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT id, user_id, started_at, runpod_job_id, tool FROM public.jobs WHERE runpod_job_id = $1",
                    pod_id,
                )
        if not row:
            logger.warning("Webhook received for unknown job_id=%s pod_id=%s", job_id, pod_id)
            return {"received": True}

    job_id = str(row["id"])
    user_id = row["user_id"]
    stored_pod_id = row["runpod_job_id"] or pod_id

    # Double-processing guard: skip if job is already in a terminal state
    async with pool.acquire() as conn:
        current_row = await conn.fetchrow(
            "SELECT status FROM public.jobs WHERE id = $1", job_id
        )
    if current_row and current_row["status"] in ("complete", "failed", "cancelled"):
        logger.info("Webhook skipped: job %s already in terminal state %s", job_id, current_row["status"])
        return {"received": True}

    # Calculate GPU seconds consumed since the job started running.
    gpu_seconds = 0
    if row["started_at"]:
        elapsed = datetime.datetime.now(datetime.UTC) - row["started_at"]
        gpu_seconds = int(elapsed.total_seconds())

    gpu_cost_usd = round(
        gpu_seconds * settings.gpu_price_per_second * (1 + settings.gpu_markup_percent / 100),
        4,
    )

    # Build results payload for completed jobs.
    results_json: str | None = None
    output: dict = {}
    payload_candidates: list[dict] = []
    if internal_status == "complete":
        output = payload.get("output", {})
        candidate_count: int = output.get("candidate_count", 0)
        zero_output = candidate_count == 0
        results_json = json.dumps({
            "candidate_count": candidate_count,
            "next_steps": output.get("next_steps", ""),
            "zero_output": zero_output,
        })
        # `or []`, not a default: a payload with an explicit null here used
        # to reach the candidate loop below and raise TypeError inside the
        # transaction, which rolls the claim back and returns 500, and the
        # redelivery that follows fails the same way, so a finished run
        # would never go terminal. a479ac38 guarded the loop instead
        # (`if candidates:`); the transaction moved the loop under the
        # claim and dropped it
        # (tests/webhooks/test_router.py::test_a_null_candidates_payload_is_not_iterated).
        # No container sends null today -- all five send a list -- but this
        # is an HTTP boundary, not a container contract.
        payload_candidates = output.get("candidates") or []

    error_category: str | None = None
    if internal_status == "failed":
        error_info = payload.get("error", {})
        error_category = error_info.get("category", "Pipeline error")

    # Update DB status. This write is also the claim on the job. The guard at
    # :207 is only a pre-read, and the reaper can take the row in the window
    # between that read and this write (worker/cleanup.py:276-285 claims it
    # the same way, from the other side), so everything below that must
    # happen once per job is gated on having won the transition.
    #
    # The claim and the two writes that record what the run produced -- the
    # job_candidates rows and the results/cost column -- run in one
    # transaction, for two reasons. A handler that loses the claim writes
    # neither, so it cannot leave rows on a row another route has already
    # made terminal: get_job reads job_candidates only on a `complete` row
    # (jobs/router.py:837) and its partial branch lists Storage instead
    # (jobs/router.py:852), so those rows would reach no one
    # (tests/webhooks/test_router.py::test_a_lost_claim_on_a_complete_payload_writes_no_candidate_rows).
    # And a failure part-way through the candidate loop now rolls the claim
    # back with it, leaving the row non-terminal for a redelivery or the
    # reaper to settle; without the transaction the row would stay terminal
    # with some of its candidates, a NULL `results`, no mail and no charge,
    # and the redelivery would be dropped by the pre-read guard at :207
    # (tests/webhooks/test_router.py::test_a_failed_candidate_insert_rolls_the_claim_back).
    async with pool.acquire() as conn:
        async with conn.transaction():
            claimed = await update_job_status(
                job_id,
                internal_status,
                stage=internal_status.capitalize(),
                gpu_seconds=gpu_seconds,
                error_category=error_category,
                require_non_terminal=True,
                conn=conn,
            )
            if claimed:
                for c in payload_candidates:
                    await conn.execute(
                        """INSERT INTO public.job_candidates (job_id, rank, pdb_key, scores)
                           VALUES ($1, $2, $3, $4::jsonb)""",
                        row["id"],
                        c["rank"],
                        c["pdb_key"],
                        json.dumps(c.get("scores", {})),
                    )
                if results_json:
                    await conn.execute(
                        "UPDATE public.jobs SET results = $1::jsonb, gpu_cost_usd = $2 WHERE id = $3",
                        results_json,
                        gpu_cost_usd,
                        row["id"],
                    )
                else:
                    await conn.execute(
                        "UPDATE public.jobs SET gpu_cost_usd = $1 WHERE id = $2",
                        gpu_cost_usd,
                        row["id"],
                    )

    # ---------- Terminate the GPU job to stop billing ----------
    # Ahead of the claim check, so it runs whether or not this handler won.
    # The pod burns money either way, and at a479ac38 every webhook that got
    # past the pre-read guard terminated it, because there was no claim to
    # lose. The winner is not a safe place to leave this: a cancel skips the
    # provider call when the tool resolves to no endpoint
    # (jobs/service.py:84). The reaper does cancel every stale pod it sweeps,
    # claimed or not (worker/cleanup.py:309), but it only sweeps after
    # STALE_HEARTBEAT_SECONDS
    # (tests/webhooks/test_router.py::test_a_lost_claim_still_terminates_the_pod).
    # On RunPod this terminates the pod. On Modal the function has already
    # self-terminated before this webhook fires, so terminate_pod is a no-op.
    if stored_pod_id:
        try:
            provider = get_provider()
            await provider.terminate_pod(stored_pod_id)
            logger.info("Terminated GPU job %s for job %s", stored_pod_id, job_id)
        except Exception as exc:
            # Log but don't fail the webhook — orphan cleanup will catch it.
            # The webhook still returns 200 so the GPU container exits cleanly;
            # Sentry capture is the only way Leo sees this without tailing
            # Railway logs (FastApiIntegration auto-capture would not fire
            # because we explicitly swallow here).
            logger.exception("Failed to terminate GPU job %s", stored_pod_id)
            sentry_sdk.capture_exception(exc)

    if not claimed:
        # Another route made the row terminal first, so it owns the status, the
        # mail and the charge. The pod is already terminated above.
        logger.info(
            "Webhook skipped: job %s went terminal between this handler's "
            "guard and its status update",
            job_id,
        )
        return {"received": True}

    # Publish SSE status update.
    await publish_status(job_id, internal_status, internal_status.capitalize())

    # How many designs this run banked before it died. Counted once, here,
    # because two separate things turn on it: the failure mail points the
    # customer at them, and a failed run is billed exactly when there is at
    # least one. The same count feeds the `partial` flag get_job serves
    # (jobs/router.py:917) and the reaper's own gate (worker/cleanup.py:368);
    # all three call one function, storage/client.py::list_job_design_keys.
    # The bulk download reads the same prefix with its own paginator
    # (jobs/router.py:611) because it archives every object it finds, CSVs
    # included, but it counts nothing and decides no money.
    saved_designs = 0
    if internal_status == "failed":
        try:
            # Sync boto3 off the event loop, the house pattern at
            # jobs/notifications.py:57.
            saved_designs = len(
                await asyncio.to_thread(list_job_design_keys, str(user_id), job_id)
            )
        except Exception as exc:
            # An unreadable bucket must not become a charge, and must not cost
            # the customer their mail either. The count stays 0, which leaves
            # the run unbilled -- what every failure did before this change --
            # and sends the plain failure notice. A successful listing that
            # finds nothing lands on the same 0 and the same outcome, so the
            # two cases are deliberately not distinguished.
            logger.exception(
                "Could not read saved designs for failed job %s; leaving it "
                "unbilled and sending the plain failure mail",
                job_id,
            )
            sentry_sdk.capture_exception(exc)

    # Record billing for a run the org owes GPU time for.
    #
    # Finished and cancelled runs qualify. So does a failed run that banked at
    # least one design, for every tool -- Leo's call 2026-10-13: this PR makes
    # those designs reachable (jobs/router.py:852), and they are not handed
    # over for free. Such a run is settled on GPU time used, the same as a
    # finished run, and is not prorated. A run that saved nothing stays fully
    # unbilled. The condition is `saved_designs > 0`, the same value the mail
    # below reports and the same function get_job's `partial` flag calls
    # (storage/client.py::list_job_design_keys), so no tool is billed on a
    # different count than the one deciding what is served. The shared
    # function is all that holds them equal; nothing checks it. `cancelled`
    # is billed with no design condition at all, as it was before.
    #
    # Exactly one of this route and the reaper bills any given job, because
    # exactly one of them wins the status transition. This handler reaches the
    # charge only when its own UPDATE claimed a non-terminal row
    # (:274), and worker/cleanup.py reaches it only when its
    # UPDATE claimed a row still 'running' (worker/cleanup.py:284)
    # -- two guards that cannot both succeed, since whichever writes first
    # makes the row terminal. The loser takes the early return above.
    # record_gpu_usage keys its meter event on the job id as a second line of
    # defence (billing/stripe_client.py:219), but it is a backstop: Stripe
    # deduplicates that key for 24 hours per its own docstring there, so the
    # guarantee is the transition, not the key.
    #
    # Phase 12: webhook handler runs WITHOUT a user JWT, so we cannot call
    # is_member_of(...) or rely on RLS. The service-role pool bypasses RLS and
    # we resolve the billing customer from the job's org:
    #   jobs.id -> jobs.organization_id -> org_stripe_customer()
    # which reads organizations.stripe_customer_id and falls back to the
    # deprecated public.users.stripe_customer_id for a personal org whose
    # customer was created by an old replica mid-deploy. Without that fallback
    # the GPU time of an existing payer would silently go unmetered, because
    # a NULL resolution skips billing rather than raising.
    should_bill = internal_status in ("complete", "cancelled") or (
        internal_status == "failed" and saved_designs > 0
    )

    if should_bill and gpu_seconds > 0:
        async with pool.acquire() as conn:
            stripe_customer_id = await conn.fetchval(
                """SELECT public.org_stripe_customer(j.organization_id)
                   FROM public.jobs j
                   WHERE j.id = $1""",
                job_id,
            )
        if stripe_customer_id:
            try:
                record_gpu_usage(stripe_customer_id, job_id, gpu_seconds)
            except Exception as exc:
                # Metering must not cost the customer their failure email: the
                # row is already terminal, so a retry returns at the
                # double-processing guard above and the mail would never go
                # out. Pinned by
                # test_a_metering_failure_still_emails_the_customer.
                logger.exception(
                    "Could not meter job %s; it stays unbilled", job_id
                )
                sentry_sdk.capture_exception(exc)

    # Send email notification.
    async with pool.acquire() as conn:
        user_row = await conn.fetchrow(
            "SELECT email FROM auth.users WHERE id = $1", user_id
        )

    if user_row:
        if internal_status == "complete":
            await send_completion_email(
                to_email=user_row["email"],
                job_id=job_id,
                tool=row["tool"] or "Unknown",
                num_designs=output.get("candidate_count", 0),
                runtime_min=gpu_seconds // 60,
                runtime_seconds=gpu_seconds,
            )
        elif internal_status == "failed":
            # saved_designs was counted before the billing block above, which
            # gates the charge on the same value this mail reports.
            await send_failure_email(
                to_email=user_row["email"],
                job_id=job_id,
                error_category=error_category or "Unknown error",
                saved_designs=saved_designs,
            )

    return {"received": True}


@router.post("/heartbeat")
async def heartbeat_webhook(request: Request):
    """Receive container heartbeat with stage and progress.

    Called every 60 seconds by the pipeline container. Updates job stage
    and last_heartbeat_at in DB, publishes SSE event for live progress.

    Expected payload:
        job_id: str          Kendrew job UUID
        stage: str           Current pipeline stage (e.g., "Running RFdiffusion")
        designs_completed: int  Number of designs finished so far
        designs_total: int      Total designs requested
    """
    body = await request.body()
    signature = request.headers.get("X-RunPod-Signature") or request.headers.get("X-Modal-Signature")
    validate_webhook_signature(
        body,
        signature,
        settings.webhook_hmac_secret,
        settings.webhook_hmac_secret_prev,
    )

    payload = json.loads(body)
    job_id = payload.get("job_id", "")
    stage = payload.get("stage", "")
    designs_completed = payload.get("designs_completed", 0)
    designs_total = payload.get("designs_total", 0)

    if not job_id:
        return {"received": True}

    # Build progress string: "Running RFdiffusion - 45/100 designs"
    progress_stage = stage
    if designs_total > 0:
        progress_stage = f"{stage} - {designs_completed}/{designs_total} designs"

    pool = await get_db_pool()

    # Verify job exists and is running (ignore heartbeats for terminal jobs)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM public.jobs WHERE id = $1", job_id
        )
    if not row or row["status"] != "running":
        return {"received": True}

    # Update heartbeat timestamp and stage
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE public.jobs
               SET last_heartbeat_at = NOW(), stage = $1, updated_at = NOW()
               WHERE id = $2""",
            progress_stage,
            job_id,
        )

    # Publish SSE event for live frontend progress
    await publish_status(job_id, "running", progress_stage)

    return {"received": True}
