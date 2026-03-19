"""Tests for Claude agent tool definitions and handlers (AGENT-01, AGENT-02)."""
import pytest


class TestIntentClassification:
    """AGENT-01: Agent classifies user's design intent."""

    def test_classify_intent_returns_valid_type(self):
        """classify_intent tool returns design_type in {binder_design, de_novo_backbone, motif_scaffolding}."""
        pytest.skip("STUB — implementation in Plan 02-03")

    def test_classify_intent_includes_tool_recommendation(self):
        """classify_intent tool returns recommended_tool in {rfdiffusion, bindcraft, boltzgen}."""
        pytest.skip("STUB — implementation in Plan 02-03")


class TestToolRecommendation:
    """AGENT-02: Agent recommends tool with rationale; user confirms."""

    def test_tool_recommendation_has_rationale(self):
        """Tool recommendation includes non-empty rationale string."""
        pytest.skip("STUB — implementation in Plan 02-03")

    def test_tool_definitions_have_required_fields(self):
        """All TOOL_DEFINITIONS entries have name, description, and input_schema."""
        pytest.skip("STUB — implementation in Plan 02-03")
