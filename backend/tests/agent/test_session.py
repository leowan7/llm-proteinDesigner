"""Tests for agent session management and wizard flow (AGENT-03)."""
import json

import pytest

from agent.session import SessionManager


class TestSessionManagement:
    """Session message history stored in Redis."""

    @pytest.mark.anyio
    async def test_save_and_load_session(self, mock_redis):
        """Messages saved to a session can be loaded back unchanged."""
        manager = SessionManager(redis_client=mock_redis)
        user_id = "user-abc"

        session_id = await manager.create(user_id)
        messages = [{"role": "user", "content": "hello"}]
        await manager.save(user_id, session_id, messages)

        loaded = await manager.load(user_id, session_id)
        assert loaded == messages

    @pytest.mark.anyio
    async def test_session_isolation(self, mock_redis):
        """Different session IDs return different message histories."""
        manager = SessionManager(redis_client=mock_redis)
        user_a = "user-alice"
        user_b = "user-bob"

        session_a = await manager.create(user_a)
        session_b = await manager.create(user_b)

        messages_a = [{"role": "user", "content": "Alice's message"}]
        messages_b = [{"role": "user", "content": "Bob's message"}]

        await manager.save(user_a, session_a, messages_a)
        await manager.save(user_b, session_b, messages_b)

        loaded_a = await manager.load(user_a, session_a)
        loaded_b = await manager.load(user_b, session_b)

        assert loaded_a == messages_a
        assert loaded_b == messages_b
        # Verify they are truly isolated
        assert loaded_a != loaded_b

    @pytest.mark.anyio
    async def test_load_missing_session_raises_value_error(self, mock_redis):
        """Loading a non-existent session raises ValueError."""
        manager = SessionManager(redis_client=mock_redis)
        with pytest.raises(ValueError, match="not found or expired"):
            await manager.load("user-xyz", "nonexistent-session-id")

    @pytest.mark.anyio
    async def test_delete_session(self, mock_redis):
        """Deleting a session makes it unloadable."""
        manager = SessionManager(redis_client=mock_redis)
        user_id = "user-del"

        session_id = await manager.create(user_id)
        await manager.delete(user_id, session_id)

        with pytest.raises(ValueError):
            await manager.load(user_id, session_id)

    @pytest.mark.anyio
    async def test_get_active_session(self, mock_redis):
        """get_active_session returns the most recently created session ID."""
        manager = SessionManager(redis_client=mock_redis)
        user_id = "user-active"

        session_id = await manager.create(user_id)
        active = await manager.get_active_session(user_id)
        assert active == session_id


class TestWizardCompletion:
    """AGENT-03: Wizard collects parameters and produces JobSpec."""

    @pytest.mark.anyio
    async def test_wizard_completion(self, mock_redis):
        """Simulate wizard flow: messages accumulate tool_use blocks and can reconstruct a JobSpec."""
        from agent.jobspec import JobSpec

        manager = SessionManager(redis_client=mock_redis)
        user_id = "user-wizard"
        session_id = await manager.create(user_id)

        # Simulate a wizard conversation:
        # 1. User asks to design a binder for IL-6R
        # 2. Assistant calls classify_intent (tool_use block)
        # 3. User confirms tool choice
        # 4. Assistant calls collect_parameters (tool_use block)
        # 5. Final parameters allow JobSpec construction
        wizard_messages = [
            {
                "role": "user",
                "content": "I want to design a binder for the IL-6 receptor chain A.",
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_001",
                        "name": "classify_intent",
                        "input": {
                            "design_type": "binder_design",
                            "recommended_tool": "bindcraft",
                            "rationale": "BindCraft is optimal for binder design with integrated sequence design.",
                        },
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_001",
                        "content": json.dumps({
                            "design_type": "binder_design",
                            "recommended_tool": "bindcraft",
                            "rationale": "BindCraft is optimal for binder design.",
                        }),
                    }
                ],
            },
            {"role": "user", "content": "Yes, proceed with BindCraft."},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_002",
                        "name": "collect_parameters",
                        "input": {
                            "tool": "bindcraft",
                            "target_chain": "A",
                            "hotspot_residues": [45, 48, 52],
                        },
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_002",
                        "content": json.dumps({
                            "tool": "bindcraft",
                            "target_chain": "A",
                            "hotspot_residues": [45, 48, 52],
                            "parameters": {
                                "num_designs": 10,
                                "design_cycles": 4,
                                "mpnn_sampling_temp": 0.1,
                                "filter_score_threshold": 80.0,
                            },
                        }),
                    }
                ],
            },
        ]

        await manager.save(user_id, session_id, wizard_messages)
        loaded = await manager.load(user_id, session_id)

        # Verify message history is intact and complete
        assert len(loaded) == len(wizard_messages)

        # Extract the final collect_parameters result from the message history
        final_tool_result_msg = loaded[-1]
        content = final_tool_result_msg["content"]
        assert isinstance(content, list), "Expected list content in final tool_result message"
        tool_result_block = content[0]
        params_data = json.loads(tool_result_block["content"])

        # Construct a JobSpec from the wizard data — this validates the data contract
        job_spec = JobSpec(
            tool=params_data["tool"],
            target_pdb_path="users/user-wizard/jobs/job-001/inputs/target.cif",
            target_chain=params_data["target_chain"],
            hotspot_residues=params_data["hotspot_residues"],
            parameters=params_data["parameters"],
            validation_results=[],
            estimated_cost_usd=0.0,
            rationale="BindCraft is optimal for binder design.",
        )

        assert job_spec.tool == "bindcraft"
        assert job_spec.target_chain == "A"
        assert job_spec.hotspot_residues == [45, 48, 52]
        assert job_spec.parameters["num_designs"] == 10
