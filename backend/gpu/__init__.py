"""GPU compute provider selection.

Exposes ``get_provider()`` — the factory the worker + webhook layers use to
obtain the active GPU compute provider. Today's default is Modal; RunPod is
retained behind ``GPU_PROVIDER=runpod_emergency`` as a break-glass rollback.

Usage:
    from gpu import get_provider
    provider = get_provider()
    pod_id = await provider.submit_job(submission)

Per the migration plan, no other module should instantiate provider classes
directly — always go through this factory so swapping providers is a single
config change.
"""

from config import settings

from gpu.provider import GPUJobStatus, GPUJobSubmission, GPUProvider

__all__ = ["GPUProvider", "GPUJobSubmission", "GPUJobStatus", "get_provider"]


def get_provider() -> GPUProvider:
    """Return the active GPU provider instance based on ``settings.gpu_provider``.

    Values:
        - ``"modal"`` (default): ``ModalProvider``. Spawns functions via Modal SDK.
        - ``"runpod_emergency"``: ``RunPodProvider`` — legacy fallback kept for
          rollback drills only. A plain ``"runpod"`` value is intentionally
          rejected to prevent accidental RunPod use.

    Returns:
        A ``GPUProvider`` instance configured from ``settings``.

    Raises:
        ValueError: If ``settings.gpu_provider`` is not a recognized value.
    """
    choice = (settings.gpu_provider or "modal").lower()

    if choice == "modal":
        # Lazy import to avoid importing modal SDK when the emergency path is
        # active (e.g. during a rollback where modal may not be installed).
        from gpu.modal import ModalProvider

        return ModalProvider(
            token_id=settings.modal_token_id,
            token_secret=settings.modal_token_secret,
            workspace=settings.modal_workspace,
            environment=settings.modal_environment,
        )

    if choice == "runpod_emergency":
        from gpu.runpod import RunPodProvider

        return RunPodProvider(api_key=settings.runpod_api_key)

    raise ValueError(
        f"Unknown GPU_PROVIDER={choice!r}. "
        "Expected 'modal' (default) or 'runpod_emergency' (rollback only)."
    )


def endpoint_for_tool(tool: str) -> str:
    """Resolve a tool name to the provider-specific endpoint identifier.

    For Modal: returns ``"<app_name>/run_tool"`` — a Modal ``app/function`` ref.
    For RunPod-emergency: returns the Docker image name configured on settings.

    Args:
        tool: Tool name (one of the 5 registered tools — bindcraft, boltzgen,
            rfdiffusion, rfantibody, pxdesign).

    Returns:
        Endpoint identifier string suitable for ``GPUJobSubmission.endpoint_id``.

    Raises:
        ValueError: If the tool is not configured for the active provider.
    """
    choice = (settings.gpu_provider or "modal").lower()

    if choice == "modal":
        modal_app = _MODAL_APPS_BY_TOOL.get(tool)
        if not modal_app:
            raise ValueError(
                f"No Modal app configured for tool={tool!r}. "
                "Add it to infrastructure/modal/ and register in "
                "gpu/__init__.py:_MODAL_APPS_BY_TOOL."
            )
        return f"{modal_app}/run_tool"

    if choice == "runpod_emergency":
        image = _runpod_image_for_tool(tool)
        if not image:
            raise ValueError(
                f"No RunPod image configured for tool={tool!r} "
                "(the tool may never have had a RunPod path)."
            )
        return image

    raise ValueError(f"Unknown GPU_PROVIDER={choice!r}")


_MODAL_APPS_BY_TOOL: dict[str, str] = {
    "bindcraft": "ranomics-bindcraft-prod",
    "boltzgen": "ranomics-boltzgen-prod",
    "rfdiffusion": "ranomics-rfdiffusion-prod",
    "rfantibody": "ranomics-rfantibody-prod",
    "pxdesign": "ranomics-pxdesign-prod",
}


def _runpod_image_for_tool(tool: str) -> str:
    """Legacy RunPod image lookup. Only used when GPU_PROVIDER=runpod_emergency."""
    mapping = {
        "rfdiffusion": settings.runpod_image_rfdiffusion,
        "rfantibody": settings.runpod_image_rfantibody,
        "bindcraft": settings.runpod_image_bindcraft,
        "boltzgen": settings.runpod_image_boltzgen,
        "pxdesign": settings.runpod_image_pxdesign,
    }
    return mapping.get(tool, "")
