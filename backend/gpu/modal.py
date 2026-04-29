"""Modal GPU provider implementation.

Implements the :class:`GPUProvider` ABC using the Modal Python SDK (v1.x).

Design:

* ``submit_job`` resolves the deployed function with
  ``modal.Function.from_name(app, fn)`` and spawns it via ``.spawn.aio()``
  (the async-native variant — ``run_job`` is already inside an asyncio worker).
* ``provider_job_id`` is the Modal ``FunctionCall.object_id`` string.
* ``endpoint_id`` is the Modal ``"<app_name>/<function_name>"`` reference
  resolved by :func:`gpu.endpoint_for_tool`. ``"<app_name>"`` (no slash) is
  also accepted and defaults the function name to ``"run_tool"``.
* The Modal function body (in ``infrastructure/modal/<tool>_app.py``) takes
  a single ``dict`` argument. Inside the container it translates to env vars
  and shells out to the same ``/opt/run_pipeline.py`` script the RunPod path
  uses. See ``infrastructure/modal/base_image.py::build_run_env``.
* Result flow: the container POSTs candidates + scores to
  ``webhook_url`` (``/webhooks/runpod``). The return value of the Modal
  function body is a short status dict we only use for logging.

Modal 1.x API notes:

* ``Function.lookup`` was renamed ``Function.from_name`` in the 1.x line and
  removed entirely from >=1.0. Always use ``from_name``.
* Each Modal ``Function``/``FunctionCall`` method has a sync form (plain call)
  and an async form (``.aio()`` suffix). We use the async form inside worker
  tasks so the event loop is not blocked by the Modal gRPC round-trip.
* ``environment_name=None`` is valid and means "use the workspace default".
  Passing an empty string used to be tolerated and is no longer — we
  normalize empties to ``None`` below.

The ``modal`` package is lazy-imported inside each method so the backend can
boot with ``GPU_PROVIDER=runpod_emergency`` even on a host where modal is
not installed.
"""

from __future__ import annotations

import logging
import os

from gpu.provider import GPUJobStatus, GPUJobSubmission, GPUProvider

logger = logging.getLogger(__name__)


# Map Modal-side status strings to the same vocabulary the webhook handler
# and UI already understand (originally RunPod's). Kept permissive so any
# future Modal rename doesn't silently fail — unknown values fall through to
# ``IN_PROGRESS`` and are handled by the webhook path.
_MODAL_STATUS_MAP: dict[str, str] = {
    "unknown": "IN_QUEUE",
    "queued": "IN_QUEUE",
    "pending": "IN_QUEUE",
    "starting": "IN_PROGRESS",
    "running": "IN_PROGRESS",
    "in_progress": "IN_PROGRESS",
    "executing": "IN_PROGRESS",
    "success": "COMPLETED",
    "completed": "COMPLETED",
    "failed": "FAILED",
    "timeout": "FAILED",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
}


def _call_object_id(call) -> str:
    """Return the stable string ID of a Modal FunctionCall.

    In 1.x the canonical attribute is ``object_id``. Some internal types
    expose ``function_call_id``. Fall back across both so a rename won't
    silently put the wrong value in the DB.
    """
    for attr in ("object_id", "function_call_id"):
        value = getattr(call, attr, None)
        if value:
            return str(value)
    # Last resort: repr — better than silently losing the ID.
    return repr(call)


class ModalProvider(GPUProvider):
    """GPUProvider implementation for Modal.com (SDK v1.x).

    Unlike RunPod Pods (one pod per job, explicit lifecycle), Modal functions
    auto-terminate when their body returns, so:

    * ``cancel_job`` calls :meth:`FunctionCall.cancel.aio`.
    * ``terminate_pod`` (RunPod parity alias) is a no-op — the container
      exits itself when the tool finishes.
    * Orphan detection relies on Modal's own timeout enforcement and the
      stale-heartbeat cron in ``worker/cleanup.py``.
    """

    def __init__(
        self,
        token_id: str = "",
        token_secret: str = "",
        workspace: str = "",
        environment: str = "main",
    ) -> None:
        """Construct the provider.

        Args:
            token_id: Modal token ID. Read from ``MODAL_TOKEN_ID`` env var by
                the SDK if empty here — but passing explicitly avoids surprises
                in Docker where env may not propagate as expected.
            token_secret: Matching token secret.
            workspace: Modal workspace slug (e.g. ``"leowan7"``). Reserved for
                future per-call overrides; the SDK resolves workspace from the
                token today.
            environment: Modal environment within the workspace
                (e.g. ``"main"``, ``"staging"``). Empty or "main" = default.
        """
        self._token_id = token_id
        self._token_secret = token_secret
        self._workspace = workspace
        # Normalize empty string → None so Modal 1.x treats it as "default env".
        self._environment: str | None = environment or None

        # Surface tokens into the process env for the SDK to pick up.
        # ``setdefault`` so explicit env wins over config defaults.
        if token_id:
            os.environ.setdefault("MODAL_TOKEN_ID", token_id)
        if token_secret:
            os.environ.setdefault("MODAL_TOKEN_SECRET", token_secret)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_endpoint(self, endpoint_id: str) -> tuple[str, str]:
        """Parse ``"app_name/function_name"`` (or bare ``"app_name"``).

        Args:
            endpoint_id: Endpoint reference from :class:`GPUJobSubmission`.

        Returns:
            ``(app_name, function_name)`` tuple. Defaults ``function_name`` to
            ``"run_tool"`` to match the convention in
            ``infrastructure/modal/<tool>_app.py``.

        Raises:
            ValueError: If ``endpoint_id`` is empty or whitespace-only.
        """
        if not endpoint_id or not endpoint_id.strip():
            raise ValueError("Modal endpoint_id is empty")

        if "/" in endpoint_id:
            app_name, function_name = endpoint_id.split("/", 1)
        else:
            app_name, function_name = endpoint_id, "run_tool"

        app_name = app_name.strip()
        function_name = function_name.strip() or "run_tool"
        return app_name, function_name

    def _lookup_function(self, endpoint_id: str):
        """Resolve an endpoint reference to a Modal ``Function`` handle.

        ``Function.from_name`` is synchronous in 1.x (it returns a lazy handle
        that hydrates on first RPC). Safe to call from an async context.
        """
        import modal

        app_name, function_name = self._split_endpoint(endpoint_id)

        kwargs: dict = {}
        if self._environment:
            kwargs["environment_name"] = self._environment

        try:
            return modal.Function.from_name(app_name, function_name, **kwargs)
        except Exception as exc:  # AttributeError, NotFoundError, auth errors
            logger.exception(
                "Modal.Function.from_name failed for app=%s fn=%s env=%s",
                app_name, function_name, self._environment,
            )
            raise RuntimeError(
                f"Could not resolve Modal function {app_name}/{function_name}"
                f" (env={self._environment!r}). Is the app deployed and are "
                f"your MODAL_TOKEN_* credentials correct? Root cause: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # GPUProvider ABC implementation
    # ------------------------------------------------------------------

    async def submit_job(self, submission: GPUJobSubmission) -> str:
        """Spawn a Modal function call and return its ``object_id``.

        Args:
            submission: ``endpoint_id`` = ``"<app>/<fn>"`` ref;
                ``input_payload`` carries job_spec + presigned URL + job_token;
                ``webhook_url`` is the callback run_pipeline.py POSTs to;
                ``policy`` carries job_id + tier + session hints.

        Returns:
            Modal ``FunctionCall.object_id`` (e.g. ``"fc-..."``).
        """
        fn = self._lookup_function(submission.endpoint_id)

        policy = submission.policy or {}
        payload = {
            **submission.input_payload,
            "webhook_url": submission.webhook_url,
            "job_id": policy.get("job_id", ""),
            "tool": policy.get("tool", ""),
            "job_tier": policy.get("job_tier", "pilot"),
            "session_index": policy.get("session_index", 0),
            "session_deadline_unix": policy.get("session_deadline_unix"),
            "resume_state_path": policy.get("resume_state_path", ""),
            "total_budget_hours": policy.get("total_budget_hours"),
        }

        logger.info(
            "Modal spawn: endpoint=%s tier=%s session=%s webhook=%s",
            submission.endpoint_id,
            payload["job_tier"],
            payload["session_index"],
            submission.webhook_url,
        )

        # ``.spawn.aio()`` is the async-native variant — it queues the call
        # on Modal and returns immediately with a FunctionCall handle. Uses
        # gRPC under the hood; latency ~50–200ms.
        try:
            call = await fn.spawn.aio(payload)
        except AttributeError:
            # Older 1.x builds shipped ``spawn`` as sync-only. Fall back.
            logger.debug("fn.spawn.aio unavailable; falling back to sync spawn")
            call = fn.spawn(payload)

        object_id = _call_object_id(call)
        logger.info("Modal FunctionCall created: %s", object_id)
        return object_id

    async def get_status(self, endpoint_id: str, provider_job_id: str) -> GPUJobStatus:
        """Return the coarse status of a running Modal function call.

        ``call.get(timeout=0)`` is the cheapest "is it done?" probe: it
        returns the value if ready, raises :class:`TimeoutError` if still
        running, and raises other exception types for terminal failures.
        """
        import modal

        call = modal.FunctionCall.from_id(provider_job_id)

        try:
            # Prefer the async-native variant; fall through to sync if the
            # installed modal version doesn't expose ``.aio``.
            try:
                result = await call.get.aio(timeout=0)
            except AttributeError:
                result = call.get(timeout=0)
        except TimeoutError:
            return GPUJobStatus(
                provider_job_id=provider_job_id,
                status="IN_PROGRESS",
                output=None,
            )
        except Exception as exc:
            logger.warning("Modal call %s failed: %s", provider_job_id, exc)
            return GPUJobStatus(
                provider_job_id=provider_job_id,
                status="FAILED",
                output={"error": str(exc)},
            )

        return GPUJobStatus(
            provider_job_id=provider_job_id,
            status="COMPLETED",
            output=result if isinstance(result, dict) else {"return_value": result},
        )

    async def cancel_job(self, endpoint_id: str, provider_job_id: str) -> None:
        """Cancel a running Modal function call.

        Silent-on-already-terminated: if the call has already finished,
        Modal raises an error we log and swallow — the webhook path will
        have already marked the job done.
        """
        import modal

        logger.info("Cancelling Modal FunctionCall: %s", provider_job_id)
        try:
            call = modal.FunctionCall.from_id(provider_job_id)
            try:
                await call.cancel.aio()
            except AttributeError:
                call.cancel()
            logger.info("Modal FunctionCall cancelled: %s", provider_job_id)
        except Exception as exc:
            logger.warning(
                "Modal cancel on %s returned error (may already be terminal): %s",
                provider_job_id,
                exc,
            )

    async def terminate_pod(self, pod_id: str) -> None:
        """RunPod-parity no-op: Modal functions self-terminate on return."""
        logger.debug(
            "Modal terminate_pod is a no-op (auto-terminates on return): id=%s",
            pod_id,
        )

    async def get_results(self, endpoint_id: str, provider_job_id: str) -> dict:
        """Fetch the return value of a completed Modal function call.

        Note: this is **not** the authoritative job output. Real candidates
        and scores flow through the webhook path. This method is used by
        admin tools for after-the-fact inspection.
        """
        import modal

        call = modal.FunctionCall.from_id(provider_job_id)
        try:
            try:
                result = await call.get.aio(timeout=0)
            except AttributeError:
                result = call.get(timeout=0)
        except TimeoutError:
            return {"status": "still_running"}
        except Exception as exc:
            return {"error": str(exc)}

        return result if isinstance(result, dict) else {"return_value": result}

    async def list_pods(self) -> list[dict]:
        """Return active Modal function calls (Phase 7 placeholder).

        Modal's SDK does not expose a global active-call enumerator. Orphan
        reconciliation is per-job via :meth:`get_status` in
        ``worker/cleanup.py``. Returning an empty list here is correct for
        that code path.
        """
        return []
