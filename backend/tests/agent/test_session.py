"""Tests for agent session management and wizard flow (AGENT-03)."""
import pytest


class TestSessionManagement:
    """Session message history stored in Redis."""

    @pytest.mark.anyio
    async def test_save_and_load_session(self, mock_redis):
        """Messages saved to session can be loaded back."""
        pytest.skip("STUB — implementation in Plan 02-03")

    @pytest.mark.anyio
    async def test_session_isolation(self, mock_redis):
        """Different session IDs return different message histories."""
        pytest.skip("STUB — implementation in Plan 02-03")


class TestWizardCompletion:
    """AGENT-03: Wizard collects parameters and produces JobSpec."""

    @pytest.mark.anyio
    async def test_wizard_completion(self, mock_redis):
        """Complete wizard flow produces a valid JobSpec."""
        pytest.skip("STUB — implementation in Plan 02-03")
