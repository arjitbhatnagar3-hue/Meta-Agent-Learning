"""Workflow tests for DAG plans, evidence, communication, and retries."""

import pytest

from backend.agents.meta import MetaAgent
from backend.agents.registry import build_agents
from backend.config import Settings
from backend.llm.fake import FakeLLMProvider
from backend.models import SubTask, TaskStatus
from backend.workflow import WorkflowEngine


@pytest.mark.asyncio
async def test_manager_creates_dag_and_reviewer_synthesis_run():
    settings = Settings(max_agent_iterations=3, agent_retry_attempts=0)
    llm = FakeLLMProvider()
    agents = build_agents(llm, settings)
    manager = MetaAgent(llm, settings, set(agents))
    business_context = "Reference document (ledger.csv):\ninvoice_id,total\nINV-1,25.00"
    plan = await manager.create_plan("Review the uploaded invoices and identify irregularities", business_context)

    updates = []

    async def progress(status, percent, stage):
        updates.append((status, percent, stage))

    result = await WorkflowEngine(agents, agent_retry_attempts=0).execute(
        plan,
        progress,
        business_context,
        task_id="workflow-test",
    )

    assert result["success"] is True
    assert result["partial_failure"] is False
    assert len(plan.subtasks) == 8
    assert result["execution_waves"][-2] == ["review_evidence"]
    assert result["execution_waves"][-1] == ["synthesize_report"]
    assert result["agent_results"]["document_review"]["evidence"][0]["source"] == "ledger.csv"
    assert result["review"]["supported_by_data"] is True
    assert result["review"]["calculations_consistent"] is None
    assert any(message["from_agent"] == "Manager Agent" for message in result["agent_messages"])
    assert any(message["to_agent"] == "Reviewer Agent" for message in result["agent_messages"])
    assert result["evidence"]
    assert any(item[0] == TaskStatus.EXECUTING for item in updates)


class AlwaysFailAgent:
    """Small test double for a network/model failure."""

    async def run(self, *args, **kwargs):
        raise TimeoutError("test network timeout")


@pytest.mark.asyncio
async def test_agent_failure_is_retried_then_reported_as_partial_not_lost():
    settings = Settings(max_agent_iterations=3, agent_retry_attempts=1)
    llm = FakeLLMProvider()
    agents = build_agents(llm, settings)
    agents["Document Agent"] = AlwaysFailAgent()
    plan = await MetaAgent(llm, settings, set(agents)).create_plan(
        "Review supplied invoices and compare the totals",
        "Reference document (invoice.csv):\ninvoice_id,total\nINV-1,25.00",
    )

    async def progress(status, percent, stage):
        return None

    result = await WorkflowEngine(
        agents,
        agent_retry_attempts=1,
        agent_timeout_seconds=1,
    ).execute(
        plan,
        progress,
        "Reference document (invoice.csv):\ninvoice_id,total\nINV-1,25.00",
        task_id="partial-test",
    )

    failed = result["agent_results"]["document_review"]
    assert result["success"] is True
    assert result["partial_failure"] is True
    assert failed["success"] is False
    assert failed["attempts"] == 2
    assert result["review"] is not None
    assert result["failed_agents"][0]["agent_name"] == "Document Agent"


@pytest.mark.asyncio
async def test_tool_failure_is_visible_as_a_partial_source_warning():
    from pydantic import BaseModel

    from backend.tools.base import Tool, ToolRegistry

    class NoArguments(BaseModel):
        pass

    class FailingStripeTool(Tool):
        name = "stripe_balance_summary"
        description = "Test-only failing provider."
        arguments_schema = NoArguments

        async def run(self, arguments):
            raise RuntimeError("provider temporarily unavailable")

    settings = Settings(stripe_secret_key="test-key", max_agent_iterations=3, agent_retry_attempts=0)
    llm = FakeLLMProvider()
    agents = build_agents(llm, settings)
    agent = agents["Financial Agent"]
    agent.tools = ToolRegistry([FailingStripeTool()])
    subtask = SubTask(
        id="financial_review",
        agent_name="Financial Agent",
        goal="Review the available billing source.",
        dependencies=[],
    )

    result = await agent.run(subtask, {}, business_context="")
    assert result.success is True
    assert result.warnings
    assert any("provider temporarily unavailable" in warning for warning in result.warnings)
    assert result.confidence <= 0.45
    assert "Tool warning" in result.answer
    assert "No live provider result was received" in result.answer

    plan = await MetaAgent(llm, settings, set(agents)).create_plan("Check the configured billing source")

    async def progress(status, percent, stage):
        return None

    workflow_result = await WorkflowEngine(agents, agent_retry_attempts=0).execute(
        plan,
        progress,
        task_id="tool-warning-test",
    )
    assert workflow_result["partial_failure"] is True
    assert any(item["agent_name"] == "Financial Agent" for item in workflow_result["agent_warnings"])


@pytest.mark.asyncio
async def test_unmatched_document_citation_is_removed_and_confidence_capped():
    import json

    from backend.llm.base import LLMResponse

    class FabricatingCitationLLM:
        async def chat(self, **kwargs):
            return LLMResponse(
                content=json.dumps({
                    "action": "final",
                    "tool": None,
                    "arguments": {},
                    "reason": "Return a test citation.",
                    "answer": "A fabricated claim says revenue was 999.",
                    "confidence": 0.99,
                    "evidence": [{
                        "source": "missing.csv",
                        "excerpt": "Revenue was 999",
                        "location": None,
                        "kind": "document",
                    }],
                    "review": None,
                }),
                model="test",
                prompt_tokens=0,
                completion_tokens=0,
            )

    agents = build_agents(FabricatingCitationLLM(), Settings())
    result = await agents["Document Agent"].run(
        SubTask(
            id="document_review",
            agent_name="Document Agent",
            goal="Summarize the supplied document.",
            dependencies=[],
        ),
        {},
        "Reference document (ledger.csv):\namount,reference\n25.00,INV-1",
    )
    assert result.evidence == []
    assert result.confidence == 0.35
    assert "could not be matched" in result.answer