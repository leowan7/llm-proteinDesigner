"""Tests for Claude agent tool definitions and handlers (AGENT-01, AGENT-02)."""
import json

import pytest

from agent.tools import TOOL_DEFINITIONS, dispatch_tool


def _get_tool(name: str) -> dict:
    """Helper: find a tool definition by name."""
    for tool in TOOL_DEFINITIONS:
        if tool["name"] == name:
            return tool
    raise KeyError(f"Tool '{name}' not in TOOL_DEFINITIONS")


class TestIntentClassification:
    """AGENT-01: Agent classifies user's design intent."""

    def test_classify_intent_returns_valid_type(self):
        """classify_intent tool schema includes all design_type enum values."""
        tool = _get_tool("classify_intent")
        design_type_enum = tool["input_schema"]["properties"]["design_type"]["enum"]
        expected = {
            "minibinder",
            "vhh_nanobody",
            "full_antibody",
            "cyclic_peptide",
            "small_molecule_binder",
            "de_novo_backbone",
            "motif_scaffolding",
            "symmetric_assembly",
        }
        assert set(design_type_enum) == expected

    def test_classify_intent_includes_tool_recommendation(self):
        """classify_intent tool schema includes all 5 recommended_tool enum values."""
        tool = _get_tool("classify_intent")
        tool_enum = tool["input_schema"]["properties"]["recommended_tool"]["enum"]
        assert set(tool_enum) == {"rfdiffusion", "rfantibody", "bindcraft", "boltzgen", "pxdesign"}


class TestToolRecommendation:
    """AGENT-02: Agent recommends tool with rationale; user confirms."""

    def test_tool_recommendation_has_rationale(self):
        """classify_intent schema requires a rationale field."""
        tool = _get_tool("classify_intent")
        assert "rationale" in tool["input_schema"]["required"]
        # rationale is a string type
        assert tool["input_schema"]["properties"]["rationale"]["type"] == "string"

    def test_tool_definitions_have_required_fields(self):
        """All TOOL_DEFINITIONS entries have name, description, and input_schema."""
        assert len(TOOL_DEFINITIONS) == 10
        for tool in TOOL_DEFINITIONS:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool '{tool['name']}' missing 'description'"
            assert "input_schema" in tool, f"Tool '{tool['name']}' missing 'input_schema'"

    def test_all_expected_tools_present(self):
        """TOOL_DEFINITIONS contains the expected setup + analysis tool names."""
        names = {t["name"] for t in TOOL_DEFINITIONS}
        assert names == {
            "resolve_structure",
            "classify_intent",
            "collect_parameters",
            "validate_preflight",
            "extract_interface",
            "load_job_results",
            "analyze_candidates",
            "flag_red_flags",
            "generate_report",
            "submit_refolding_job",
        }


class TestDispatchTool:
    """dispatch_tool routes calls to correct handlers and returns valid JSON."""

    @pytest.mark.anyio
    async def test_dispatch_classify_intent(self):
        """dispatch_tool classify_intent echoes back design_type, tool, and rationale."""
        result_json = await dispatch_tool(
            "classify_intent",
            {
                "design_type": "binder_design",
                "recommended_tool": "bindcraft",
                "rationale": "BindCraft produces ready-to-express sequences for binder design.",
            },
        )
        result = json.loads(result_json)
        assert result["design_type"] == "binder_design"
        assert result["recommended_tool"] == "bindcraft"
        assert "rationale" in result

    @pytest.mark.anyio
    async def test_dispatch_collect_parameters(self):
        """dispatch_tool collect_parameters returns rfdiffusion defaults including num_designs=10."""
        result_json = await dispatch_tool(
            "collect_parameters",
            {
                "tool": "rfdiffusion",
                "target_chain": "A",
            },
        )
        result = json.loads(result_json)
        assert "parameters" in result
        assert result["parameters"]["num_designs"] == 10  # default from WIZARD_PARAMS
        assert result["target_chain"] == "A"
        assert result["tool"] == "rfdiffusion"

    @pytest.mark.anyio
    async def test_dispatch_collect_parameters_with_overrides(self):
        """dispatch_tool collect_parameters applies user overrides over defaults."""
        result_json = await dispatch_tool(
            "collect_parameters",
            {
                "tool": "rfdiffusion",
                "target_chain": "B",
                "user_overrides": {"num_designs": 25},
            },
        )
        result = json.loads(result_json)
        assert result["parameters"]["num_designs"] == 25
        # Other params fall back to default
        assert result["parameters"]["binder_length"] == 80

    @pytest.mark.anyio
    async def test_dispatch_unknown_tool(self):
        """dispatch_tool returns error JSON for unknown tool name."""
        result_json = await dispatch_tool("nonexistent_tool", {})
        result = json.loads(result_json)
        assert "error" in result
        assert "nonexistent_tool" in result["error"]

    @pytest.mark.anyio
    async def test_dispatch_validate_preflight_param_sanity(self):
        """dispatch_tool validate_preflight detects out-of-range parameters."""
        result_json = await dispatch_tool(
            "validate_preflight",
            {
                "pdb_path": "/tmp/fake.pdb",
                "chain_id": "A",
                "hotspot_residues": [],
                "tool": "rfdiffusion",
                "parameters": {
                    "num_designs": 200,  # max is 100 -> fail
                    "binder_length": 80,
                    "noise_scale": 1.0,
                },
            },
        )
        result = json.loads(result_json)
        assert result["can_proceed"] is False
        fail_checks = [r for r in result["validation_results"] if r["status"] == "fail"]
        assert any("num_designs" in r["check_name"] for r in fail_checks)
