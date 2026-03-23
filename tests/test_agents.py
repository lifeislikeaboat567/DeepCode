"""Unit tests for the BaseAgent ReAct loop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepcode.agents.base import AgentResponse, BaseAgent
from deepcode.agents.orchestrator import OrchestratorAgent
from deepcode.governance import ApprovalStore, PolicyEngine, PolicyRule, PolicyStore
from deepcode.llm.mock_client import MockLLMClient
from deepcode.tools.base import BaseTool, ToolResult
from deepcode.tools.file_manager import FileManagerTool
from deepcode.tools.script_runner import ScriptRunnerTool


class _EchoTool(BaseTool):
    """A simple tool that echoes its input for testing."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes the provided text back"

    async def run(self, text: str = "", **kwargs) -> ToolResult:
        return ToolResult.ok(self.name, f"ECHO: {text}")


def _final_answer_response(answer: str) -> str:
    return json.dumps(
        {"thought": "I have the answer", "action": "final_answer", "action_input": {"answer": answer}}
    )


def _tool_response(tool: str, **inputs) -> str:
    return json.dumps(
        {"thought": "I should use a tool", "action": tool, "action_input": inputs}
    )


class TestBaseAgent:
    @pytest.mark.asyncio
    async def test_direct_final_answer(self):
        llm = MockLLMClient(responses=[_final_answer_response("42")])
        agent = BaseAgent(llm=llm)
        result = await agent.run("What is 6 * 7?")

        assert isinstance(result, AgentResponse)
        assert result.success is True
        assert result.answer == "42"
        assert len(result.steps) == 1

    @pytest.mark.asyncio
    async def test_tool_use_then_final_answer(self):
        responses = [
            _tool_response("echo", text="hello world"),
            _final_answer_response("Tool said: ECHO: hello world"),
        ]
        llm = MockLLMClient(responses=responses)
        agent = BaseAgent(llm=llm, tools=[_EchoTool()])
        result = await agent.run("Echo hello world")

        assert result.success is True
        assert len(result.steps) == 2
        assert result.steps[0].action == "echo"
        assert "ECHO: hello world" in result.steps[0].observation

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error_observation(self):
        responses = [
            _tool_response("nonexistent_tool"),
            _final_answer_response("Could not use the tool"),
        ]
        llm = MockLLMClient(responses=responses)
        agent = BaseAgent(llm=llm, tools=[])
        result = await agent.run("Use a nonexistent tool")

        # The agent should have continued after the failed tool call
        assert len(result.steps) >= 1
        assert "not found" in result.steps[0].observation.lower()

    @pytest.mark.asyncio
    async def test_max_iterations_respected(self):
        # Never gives a final answer
        tool_resp = _tool_response("echo", text="looping")
        llm = MockLLMClient(responses=[tool_resp] * 20)
        agent = BaseAgent(llm=llm, tools=[_EchoTool()], max_iterations=3)
        result = await agent.run("Loop forever")

        assert result.success is False
        assert "max iterations" in result.error.lower()
        assert len(result.steps) == 3

    @pytest.mark.asyncio
    async def test_unparseable_llm_response_becomes_final_answer(self):
        llm = MockLLMClient(responses=["This is plain text with no JSON"])
        agent = BaseAgent(llm=llm)
        result = await agent.run("What is the answer?")

        assert result.success is True
        assert result.answer == "This is plain text with no JSON"

    @pytest.mark.asyncio
    async def test_function_call_payload_is_supported(self):
        responses = [
            json.dumps(
                {
                    "thought": "Use shell for ping",
                    "function_call": {
                        "name": "echo",
                        "arguments": {"text": "ping-ok"},
                    },
                }
            ),
            _final_answer_response("done"),
        ]
        llm = MockLLMClient(responses=responses)
        agent = BaseAgent(llm=llm, tools=[_EchoTool()])

        result = await agent.run("ping baidu")

        assert result.success is True
        assert result.steps[0].action == "echo"
        assert result.steps[0].action_input == {"text": "ping-ok"}

    @pytest.mark.asyncio
    async def test_stream_run_yields_chunks(self):
        llm = MockLLMClient(responses=[_final_answer_response("Done!")])
        agent = BaseAgent(llm=llm)

        chunks = []
        async for chunk in agent.stream_run("Simple task"):
            chunks.append(chunk)

        full_text = "".join(chunks)
        assert len(chunks) > 0
        assert "Done!" in full_text

    @pytest.mark.asyncio
    async def test_empty_final_answer_turn_retries_until_non_empty(self):
        responses = [
            "",
            _final_answer_response("Recovered answer"),
        ]
        llm = MockLLMClient(responses=responses)
        agent = BaseAgent(llm=llm, max_iterations=3)

        result = await agent.run("Return an answer")

        assert result.success is True
        assert result.answer == "Recovered answer"

    @pytest.mark.asyncio
    async def test_tool_metadata_and_artifacts_are_preserved_for_file_writes(self, tmp_path: Path):
        responses = [
            _tool_response(
                "file_manager",
                action="write",
                path="nested/demo.py",
                content="print('ok')\n",
            ),
            _final_answer_response("File written"),
        ]
        llm = MockLLMClient(responses=responses)
        agent = BaseAgent(llm=llm, tools=[FileManagerTool(root=tmp_path)])

        result = await agent.run("Write a demo file")

        assert result.success is True
        assert result.steps[0].tool_success is True
        assert "path" in result.steps[0].tool_metadata
        assert result.code_artifacts[0]["filename"] == "nested/demo.py"
        assert result.code_artifacts[0]["kind"] == "file"

    @pytest.mark.asyncio
    async def test_policy_deny_blocks_tool_execution(self, tmp_path: Path):
        policy_store = PolicyStore(file_path=str(tmp_path / "policies.json"))
        policy_store.upsert(
            PolicyRule(
                name="deny-echo",
                scope="project",
                target="tool:echo",
                decision="deny",
                enabled=True,
            )
        )

        responses = [
            _tool_response("echo", text="blocked"),
            _final_answer_response("fallback"),
        ]
        llm = MockLLMClient(responses=responses)
        agent = BaseAgent(
            llm=llm,
            tools=[_EchoTool()],
            policy_engine=PolicyEngine(policy_store=policy_store),
        )

        result = await agent.run("Run echo")

        assert result.success is True
        assert "policy denied tool call" in result.steps[0].observation.lower()

    @pytest.mark.asyncio
    async def test_policy_ask_requires_approval_and_blocks_tool_execution(self, tmp_path: Path):
        policy_store = PolicyStore(file_path=str(tmp_path / "policies.json"))
        approval_store = ApprovalStore(file_path=str(tmp_path / "approvals.json"))
        policy_store.upsert(
            PolicyRule(
                name="ask-echo",
                scope="project",
                target="tool:echo",
                decision="ask",
                enabled=True,
            )
        )

        responses = [
            _tool_response("echo", text="blocked"),
            _final_answer_response("approval required"),
        ]
        llm = MockLLMClient(responses=responses)
        agent = BaseAgent(
            llm=llm,
            tools=[_EchoTool()],
            policy_engine=PolicyEngine(policy_store=policy_store),
            approval_store=approval_store,
        )

        result = await agent.run("Run echo with approval")

        assert result.success is True
        assert "policy requires approval" in result.steps[0].observation.lower()
        assert result.steps[0].tool_metadata.get("approval_request_id")

        pending = approval_store.list_all(status="pending")
        assert len(pending) == 1
        assert pending[0].tool_name == "echo"

    @pytest.mark.asyncio
    async def test_policy_ask_allows_execution_with_approved_request(self, tmp_path: Path):
        policy_store = PolicyStore(file_path=str(tmp_path / "policies.json"))
        approval_store = ApprovalStore(file_path=str(tmp_path / "approvals.json"))
        policy_store.upsert(
            PolicyRule(
                name="ask-echo",
                scope="project",
                target="tool:echo",
                decision="ask",
                enabled=True,
            )
        )

        approved = approval_store.create(tool_name="echo", action_input={"text": "hello"}, reason="manual approve")
        approval_store.decide(approved.id, "approved")

        responses = [
            _tool_response("echo", text="hello", approval_request_id=approved.id),
            _final_answer_response("approved run"),
        ]
        llm = MockLLMClient(responses=responses)
        agent = BaseAgent(
            llm=llm,
            tools=[_EchoTool()],
            policy_engine=PolicyEngine(policy_store=policy_store),
            approval_store=approval_store,
        )

        result = await agent.run("Run approved echo")

        assert result.success is True
        assert "ECHO: hello" in result.steps[0].observation


class TestOrchestratorAgent:
    @pytest.mark.asyncio
    async def test_high_agency_run_completes_with_structured_state(self, tmp_path: Path):
        normalizer_payload = {
            "goal": "Create hello module",
            "success_criteria": ["Generate hello.py"],
            "constraints": [],
            "deliverables": ["hello.py"],
            "context": {"language": "python"},
            "budget": {"max_steps": 4, "max_runtime_ms": 120000, "max_tool_calls": 10},
        }
        planner_payload = {
            "plan": [
                {
                    "id": "step-1",
                    "title": "Create hello file",
                    "purpose": "Write hello.py with a simple print",
                    "action_type": "code",
                    "expected_output": "hello.py exists",
                    "verification_method": "confirm generated artifact",
                }
            ]
        }
        router_payload = {
            "type": "code",
            "tool_name": "file_manager",
            "reason": "Need to write a file",
            "input": {},
        }
        validator_payload = {
            "passed": True,
            "confidence": 0.95,
            "evidence": ["artifact generated"],
            "issues": [],
        }
        review_payload = {
            "passed": True,
            "score": 8,
            "issues": [],
            "suggestions": [],
        }

        responses = [
            _final_answer_response(json.dumps(normalizer_payload)),
            _final_answer_response(json.dumps(planner_payload)),
            _final_answer_response(json.dumps(router_payload)),
            _tool_response(
                "file_manager",
                action="write",
                path="hello.py",
                content="print('hello')\n",
            ),
            _final_answer_response("Implemented hello module"),
            _final_answer_response(json.dumps(validator_payload)),
            _final_answer_response(json.dumps(review_payload)),
            _final_answer_response("Tests generated"),
            _final_answer_response("Task completed with evidence"),
        ]

        llm = MockLLMClient(responses=responses)
        orchestrator = OrchestratorAgent(llm=llm, tools=[FileManagerTool(root=tmp_path)])
        result = await orchestrator.run("Create a hello module")

        assert result.success is True
        assert result.plan
        assert result.code_artifacts
        assert result.task_state
        assert result.task_state.get("status") == "completed"
        assert result.observations

    @pytest.mark.asyncio
    async def test_script_runner_evidence_is_captured_in_execution_results(self, tmp_path: Path):
        normalizer_payload = {
            "goal": "Generate and run a helper script",
            "success_criteria": ["Run helper script successfully"],
            "constraints": [],
            "deliverables": ["scripts/report.py"],
            "context": {"language": "python"},
            "budget": {"max_steps": 4, "max_runtime_ms": 120000, "max_tool_calls": 10},
        }
        planner_payload = {
            "plan": [
                {
                    "id": "step-1",
                    "title": "Run helper script",
                    "purpose": "Generate a one-off python script and execute it",
                    "action_type": "code",
                    "tool_name": "script_runner",
                    "inputs": {
                        "path": "scripts/report.py",
                        "content": "print('script evidence')\\n",
                        "execute": True,
                    },
                    "expected_output": "Script prints script evidence",
                    "verification_method": "Check exit code and stdout",
                }
            ]
        }
        router_payload = {
            "type": "code",
            "tool_name": "script_runner",
            "reason": "Need a script artifact and runtime evidence",
            "input": {
                "path": "scripts/report.py",
                "content": "print('script evidence')\\n",
                "execute": True,
            },
        }
        validator_payload = {
            "passed": True,
            "confidence": 0.98,
            "evidence": ["script exit code 0", "stdout contains script evidence"],
            "issues": [],
        }
        review_payload = {
            "passed": True,
            "score": 8,
            "issues": [],
            "suggestions": [],
        }

        responses = [
            _final_answer_response(json.dumps(normalizer_payload)),
            _final_answer_response(json.dumps(planner_payload)),
            _final_answer_response(json.dumps(router_payload)),
            _tool_response(
                "script_runner",
                path="scripts/report.py",
                content="print('script evidence')\n",
                execute=True,
            ),
            _final_answer_response("Executed helper script"),
            _final_answer_response(json.dumps(validator_payload)),
            _final_answer_response(json.dumps(review_payload)),
            _final_answer_response("Tests generated"),
            _final_answer_response("Task completed with script evidence"),
        ]

        llm = MockLLMClient(responses=responses)
        orchestrator = OrchestratorAgent(llm=llm, tools=[ScriptRunnerTool(root=tmp_path)])
        result = await orchestrator.run("Generate and run a helper script")

        assert result.success is True
        assert result.code_artifacts
        assert result.code_artifacts[0]["kind"] == "script"
        assert result.execution_results[0]["tool_events"][0]["tool_metadata"]["exit_code"] == 0
        assert result.execution_results[0]["evidence"]
        assert result.task_state["artifacts"][0]["kind"] == "script"

    @pytest.mark.asyncio
    async def test_high_risk_task_requires_approval(self):
        llm = MockLLMClient(responses=[_final_answer_response("{}")] * 3)
        orchestrator = OrchestratorAgent(llm=llm, tools=[], allow_high_risk_actions=False)

        result = await orchestrator.run("Delete production database records")

        assert result.success is False
        assert "approval" in result.error.lower()
        assert result.task_state.get("status") == "needs_input"
