"""Tests for ModalProvider (GPUProvider implementation on Modal.com).

Covers:
- submit_job resolves via modal.Function.from_name(...) and spawns via
  ``spawn.aio`` (async-native 1.x API), returning FunctionCall.object_id.
- get_status maps Modal FunctionCall states via ``get.aio(timeout=0)``.
- cancel_job calls FunctionCall.cancel.aio().
- terminate_pod is a safe no-op (Modal functions self-terminate).
- get_results retrieves the function's return value.

The ``modal`` package is mocked via ``sys.modules`` injection so these tests run
without the real SDK installed, matching how mini-deps-only CI will run.
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _install_fake_modal() -> tuple[MagicMock, MagicMock]:
    """Install a fake ``modal`` module in sys.modules.

    Returns:
        (mock_function_class, mock_function_call_class) so tests can configure
        the fake SDK's behaviour per-test without a real import.
    """
    fake_modal = types.ModuleType("modal")
    mock_fn_class = MagicMock(name="modal.Function")
    mock_fc_class = MagicMock(name="modal.FunctionCall")
    fake_modal.Function = mock_fn_class
    fake_modal.FunctionCall = mock_fc_class
    sys.modules["modal"] = fake_modal
    return mock_fn_class, mock_fc_class


def _make_fn_with_spawn(mock_call) -> MagicMock:
    """Build a mock Modal Function whose ``spawn.aio`` is an awaitable.

    Matches the 1.x API where each method has an ``.aio`` suffix for the
    async variant. ``spawn(payload)`` is sync, ``spawn.aio(payload)`` is a
    coroutine.
    """
    mock_fn = MagicMock()
    mock_fn.spawn = MagicMock(return_value=mock_call)
    mock_fn.spawn.aio = AsyncMock(return_value=mock_call)
    return mock_fn


def _make_call_with_get(return_value=None, side_effect=None) -> MagicMock:
    """Build a mock FunctionCall whose ``get.aio`` is awaitable."""
    mock_call = MagicMock()
    if side_effect is not None:
        mock_call.get = MagicMock(side_effect=side_effect)
        mock_call.get.aio = AsyncMock(side_effect=side_effect)
    else:
        mock_call.get = MagicMock(return_value=return_value)
        mock_call.get.aio = AsyncMock(return_value=return_value)
    return mock_call


class TestModalProvider:
    """Tests for the Modal GPUProvider implementation."""

    @pytest.mark.anyio
    async def test_submit_job_returns_object_id(self):
        """``submit_job`` must spawn on the resolved Modal Function and
        return ``FunctionCall.object_id``.
        """
        mock_fn_class, _ = _install_fake_modal()
        mock_call = MagicMock()
        mock_call.object_id = "fc-abc123"
        mock_fn = _make_fn_with_spawn(mock_call)
        mock_fn_class.from_name = MagicMock(return_value=mock_fn)

        from gpu.modal import ModalProvider
        from gpu.provider import GPUJobSubmission

        provider = ModalProvider(
            token_id="test-id",
            token_secret="test-secret",
            environment="test",
        )

        submission = GPUJobSubmission(
            endpoint_id="kendrew-bindcraft-prod/run_tool",
            input_payload={"job_spec": {"tool": "bindcraft"}, "job_token": "jt"},
            webhook_url="http://localhost:8000/webhooks/runpod",
            policy={
                "job_id": "job-xyz",
                "tool": "bindcraft",
                "job_tier": "pilot",
                "total_budget_hours": 4,
                "session_index": 0,
            },
        )
        result = await provider.submit_job(submission)

        assert result == "fc-abc123"
        mock_fn_class.from_name.assert_called_once_with(
            "kendrew-bindcraft-prod", "run_tool", environment_name="test"
        )
        mock_fn.spawn.aio.assert_called_once()
        payload = mock_fn.spawn.aio.call_args[0][0]
        assert payload["webhook_url"] == "http://localhost:8000/webhooks/runpod"
        assert payload["job_id"] == "job-xyz"
        assert payload["job_tier"] == "pilot"
        assert payload["total_budget_hours"] == 4

    @pytest.mark.anyio
    async def test_submit_job_accepts_endpoint_without_function_name(self):
        """Bare ``"app_name"`` (no slash) must default the function name to ``"run_tool"``."""
        mock_fn_class, _ = _install_fake_modal()
        mock_call = MagicMock()
        mock_call.object_id = "fc-1"
        mock_fn = _make_fn_with_spawn(mock_call)
        mock_fn_class.from_name = MagicMock(return_value=mock_fn)

        from gpu.modal import ModalProvider
        from gpu.provider import GPUJobSubmission

        provider = ModalProvider(environment="")
        await provider.submit_job(
            GPUJobSubmission(
                endpoint_id="kendrew-boltzgen-prod",
                input_payload={},
                webhook_url="http://x",
            )
        )

        # Empty environment normalizes to None -> no environment_name kwarg is passed.
        mock_fn_class.from_name.assert_called_once_with(
            "kendrew-boltzgen-prod", "run_tool"
        )

    @pytest.mark.anyio
    async def test_get_status_completed(self):
        """When the Modal FunctionCall has returned, ``get_status`` returns
        COMPLETED and the dict return value as ``output``.
        """
        _, mock_fc_class = _install_fake_modal()
        mock_call = _make_call_with_get(return_value={"candidate_count": 2})
        mock_fc_class.from_id = MagicMock(return_value=mock_call)

        from gpu.modal import ModalProvider
        from gpu.provider import GPUJobStatus

        provider = ModalProvider()
        status = await provider.get_status("", "fc-abc")

        assert isinstance(status, GPUJobStatus)
        assert status.provider_job_id == "fc-abc"
        assert status.status == "COMPLETED"
        assert status.output == {"candidate_count": 2}

    @pytest.mark.anyio
    async def test_get_status_still_running(self):
        """A TimeoutError from ``call.get.aio(timeout=0)`` means the call is still
        running; ``get_status`` returns IN_PROGRESS with ``output=None``.
        """
        _, mock_fc_class = _install_fake_modal()
        mock_call = _make_call_with_get(side_effect=TimeoutError("not ready"))
        mock_fc_class.from_id = MagicMock(return_value=mock_call)

        from gpu.modal import ModalProvider

        provider = ModalProvider()
        status = await provider.get_status("", "fc-def")

        assert status.status == "IN_PROGRESS"
        assert status.output is None

    @pytest.mark.anyio
    async def test_get_status_failed_on_exception(self):
        """A non-TimeoutError (e.g. function raised) maps to FAILED."""
        _, mock_fc_class = _install_fake_modal()
        mock_call = _make_call_with_get(side_effect=RuntimeError("boom"))
        mock_fc_class.from_id = MagicMock(return_value=mock_call)

        from gpu.modal import ModalProvider

        provider = ModalProvider()
        status = await provider.get_status("", "fc-bad")

        assert status.status == "FAILED"
        assert status.output is not None
        assert "boom" in status.output["error"]

    @pytest.mark.anyio
    async def test_cancel_job_calls_cancel(self):
        """``cancel_job`` must call ``FunctionCall.from_id(...).cancel.aio()``."""
        _, mock_fc_class = _install_fake_modal()
        mock_call = MagicMock()
        mock_call.cancel = MagicMock()
        mock_call.cancel.aio = AsyncMock()
        mock_fc_class.from_id = MagicMock(return_value=mock_call)

        from gpu.modal import ModalProvider

        provider = ModalProvider()
        await provider.cancel_job("", "fc-xyz")

        mock_fc_class.from_id.assert_called_once_with("fc-xyz")
        mock_call.cancel.aio.assert_called_once()

    @pytest.mark.anyio
    async def test_cancel_job_swallows_errors(self):
        """An error from ``.cancel.aio()`` (e.g. already-terminal) must be logged
        and swallowed, not raised — matches the RunPod path's behaviour.
        """
        _, mock_fc_class = _install_fake_modal()
        mock_call = MagicMock()
        mock_call.cancel = MagicMock()
        mock_call.cancel.aio = AsyncMock(side_effect=Exception("already done"))
        mock_fc_class.from_id = MagicMock(return_value=mock_call)

        from gpu.modal import ModalProvider

        provider = ModalProvider()
        # Must not raise.
        await provider.cancel_job("", "fc-xyz")

    @pytest.mark.anyio
    async def test_terminate_pod_is_noop(self):
        """``terminate_pod`` is a no-op on Modal (function auto-terminates on return).
        It must not raise and must not call any SDK method — the webhook
        handler invokes it unconditionally, so a no-op is the correct semantic.
        """
        _install_fake_modal()
        from gpu.modal import ModalProvider

        provider = ModalProvider()
        # Must not raise.
        await provider.terminate_pod("fc-anything")

    @pytest.mark.anyio
    async def test_get_results_returns_dict(self):
        """``get_results`` returns the function's return value when ready."""
        _, mock_fc_class = _install_fake_modal()
        mock_call = _make_call_with_get(return_value={"exit_code": 0, "candidate_count": 3})
        mock_fc_class.from_id = MagicMock(return_value=mock_call)

        from gpu.modal import ModalProvider

        provider = ModalProvider()
        result = await provider.get_results("", "fc-results")

        assert result == {"exit_code": 0, "candidate_count": 3}


class TestGetProviderFactory:
    """Tests for ``gpu.get_provider()`` — the factory the worker uses."""

    def test_factory_returns_modal_by_default(self):
        """``GPU_PROVIDER=modal`` (the default) must return ModalProvider."""
        _install_fake_modal()
        with patch("gpu.settings") as mock_settings:
            mock_settings.gpu_provider = "modal"
            mock_settings.modal_token_id = ""
            mock_settings.modal_token_secret = ""
            mock_settings.modal_workspace = ""
            mock_settings.modal_environment = "main"
            from gpu import get_provider
            from gpu.modal import ModalProvider

            provider = get_provider()
            assert isinstance(provider, ModalProvider)

    def test_factory_returns_runpod_on_emergency(self):
        """``GPU_PROVIDER=runpod_emergency`` must return RunPodProvider
        (break-glass rollback path)."""
        with patch("gpu.settings") as mock_settings:
            mock_settings.gpu_provider = "runpod_emergency"
            mock_settings.runpod_api_key = "test-rp-key"
            from gpu import get_provider
            from gpu.runpod import RunPodProvider

            provider = get_provider()
            assert isinstance(provider, RunPodProvider)

    def test_factory_rejects_plain_runpod(self):
        """A bare ``"runpod"`` (without ``_emergency``) must be rejected to
        prevent accidental RunPod use post-migration."""
        with patch("gpu.settings") as mock_settings:
            mock_settings.gpu_provider = "runpod"
            from gpu import get_provider

            with pytest.raises(ValueError, match="Unknown GPU_PROVIDER"):
                get_provider()

    def test_endpoint_for_tool_modal(self):
        """``endpoint_for_tool`` under Modal returns the ``app/function`` ref."""
        with patch("gpu.settings") as mock_settings:
            mock_settings.gpu_provider = "modal"
            from gpu import endpoint_for_tool

            assert endpoint_for_tool("bindcraft") == "kendrew-bindcraft-prod/run_tool"
            assert endpoint_for_tool("boltzgen") == "kendrew-boltzgen-prod/run_tool"
            assert endpoint_for_tool("rfdiffusion") == "kendrew-rfdiffusion-prod/run_tool"
            assert endpoint_for_tool("rfantibody") == "kendrew-rfantibody-prod/run_tool"
            assert endpoint_for_tool("pxdesign") == "kendrew-pxdesign-prod/run_tool"

    def test_endpoint_for_tool_rejects_unknown(self):
        with patch("gpu.settings") as mock_settings:
            mock_settings.gpu_provider = "modal"
            from gpu import endpoint_for_tool

            with pytest.raises(ValueError, match="No Modal app configured"):
                endpoint_for_tool("notatool")
