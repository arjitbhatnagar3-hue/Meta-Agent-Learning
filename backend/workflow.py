"""Dependency-aware DAG execution, agent messaging, and bounded recovery."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from backend.agents.specialist import SpecialistAgent
from backend.models import AgentMessage, AgentResult, ExecutionPlan, TaskStatus


ProgressCallback = Callable[[TaskStatus, int, str], Awaitable[None]]


class WorkflowEngine:
    """Execute DAG waves in parallel, retry work, and keep partial findings."""

    def __init__(
        self,
        agents: dict[str, SpecialistAgent],
        *,
        agent_retry_attempts: int = 2,
        agent_timeout_seconds: float = 120,
    ) -> None:
        self.agents = agents
        self.agent_retry_attempts = max(0, agent_retry_attempts)
        self.agent_timeout_seconds = max(1.0, agent_timeout_seconds)

    @staticmethod
    def waves(plan: ExecutionPlan) -> list[list[str]]:
        """Return deterministic topological waves for a validated DAG."""
        remaining = {task.id: set(task.dependencies) for task in plan.subtasks}
        completed: set[str] = set()
        waves: list[list[str]] = []
        while remaining:
            ready = sorted(
                task_id
                for task_id, dependencies in remaining.items()
                if dependencies <= completed
            )
            if not ready:
                raise ValueError("Dependency cycle detected")
            waves.append(ready)
            completed.update(ready)
            for task_id in ready:
                remaining.pop(task_id)
        return waves

    async def _run_agent_with_retries(
        self,
        agent: SpecialistAgent,
        task: Any,
        dependencies: dict[str, AgentResult],
        business_context: str,
        incoming_messages: list[dict[str, Any]],
    ) -> AgentResult:
        """Retry transient model/tool errors and return a useful failure record."""
        last_error = "Agent returned an unsuccessful result."
        total_attempts = self.agent_retry_attempts + 1
        for attempt in range(1, total_attempts + 1):
            try:
                result = await asyncio.wait_for(
                    agent.run(
                        task,
                        dependencies,
                        business_context,
                        incoming_messages,
                    ),
                    timeout=self.agent_timeout_seconds,
                )
                if result.success:
                    return result.model_copy(update={"attempts": attempt})
                last_error = result.error or result.answer or last_error
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if isinstance(exc, TimeoutError):
                    last_error = f"Agent timed out after {self.agent_timeout_seconds:g} seconds."
                else:
                    last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            if attempt < total_attempts:
                await asyncio.sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))
        return AgentResult(
            task_id=task.id,
            agent_name=task.agent_name,
            success=False,
            answer="This agent could not complete its assignment after retries. Its downstream reviewer will be told that the evidence is missing.",
            confidence=0,
            attempts=total_attempts,
            error=last_error[:1000],
        )

    async def execute(
        self,
        plan: ExecutionPlan,
        progress: ProgressCallback,
        business_context: str = "",
        task_id: str = "",
    ) -> dict[str, Any]:
        """Run dependency waves and return results, review, evidence and messages."""
        by_id = {task.id: task for task in plan.subtasks}
        results: dict[str, AgentResult] = {}
        messages: list[AgentMessage] = []
        waves = self.waves(plan)
        total = len(plan.subtasks)
        finished = 0

        # Manager delegation is explicit and saved in the final communication log.
        for task in plan.subtasks:
            messages.append(
                AgentMessage(
                    task_id=task_id,
                    subtask_id=task.id,
                    from_agent="Manager Agent",
                    to_agent=task.agent_name,
                    message_type="delegation",
                    content=f"Delegated task: {task.goal}",
                )
            )

        for wave_index, wave in enumerate(waves, start=1):
            await progress(
                TaskStatus.EXECUTING,
                15 + int(65 * finished / max(total, 1)),
                f"Executing DAG wave {wave_index}/{len(waves)}",
            )
            jobs: list[asyncio.Task[AgentResult]] = []
            job_ids: list[str] = []

            for subtask_id in wave:
                subtask = by_id[subtask_id]
                dependencies = {
                    dependency: results[dependency]
                    for dependency in subtask.dependencies
                }
                failed_dependencies = [
                    dependency
                    for dependency, result in dependencies.items()
                    if not result.success
                ]
                # The Reviewer and Synthesis Agent are deliberately allowed to
                # run with partial inputs so the final answer can explain gaps.
                is_recovery_stage = subtask.agent_name in {"Reviewer Agent", "Synthesis Agent"}
                if failed_dependencies and not is_recovery_stage:
                    results[subtask_id] = AgentResult(
                        task_id=subtask_id,
                        agent_name=subtask.agent_name,
                        success=False,
                        answer="Skipped because required upstream work failed: "
                        + ", ".join(failed_dependencies),
                        confidence=0,
                        attempts=1,
                        error="dependency_failure",
                    )
                    finished += 1
                    continue

                agent = self.agents.get(subtask.agent_name)
                if agent is None:
                    results[subtask_id] = AgentResult(
                        task_id=subtask_id,
                        agent_name=subtask.agent_name,
                        success=False,
                        answer="The assigned specialist is not registered.",
                        confidence=0,
                        attempts=1,
                        error="unregistered_agent",
                    )
                    finished += 1
                    continue

                incoming = [
                    message.model_dump(mode="json")
                    for message in messages
                    if message.to_agent == subtask.agent_name
                    and message.message_type in {"delegation", "handoff", "review", "failure"}
                ]
                jobs.append(
                    asyncio.create_task(
                        self._run_agent_with_retries(
                            agent,
                            subtask,
                            dependencies,
                            business_context,
                            incoming,
                        )
                    )
                )
                job_ids.append(subtask_id)

            raw_results = await asyncio.gather(*jobs, return_exceptions=True)
            for subtask_id, value in zip(job_ids, raw_results):
                task = by_id[subtask_id]
                if isinstance(value, asyncio.CancelledError):
                    raise value
                if isinstance(value, Exception):
                    result = AgentResult(
                        task_id=subtask_id,
                        agent_name=task.agent_name,
                        success=False,
                        answer="Agent execution failed after its retry policy.",
                        confidence=0,
                        attempts=self.agent_retry_attempts + 1,
                        error=f"{type(value).__name__}: {str(value)[:500]}",
                    )
                else:
                    result = value
                results[subtask_id] = result
                finished += 1

                consumers = [
                    candidate
                    for candidate in plan.subtasks
                    if subtask_id in candidate.dependencies
                ]
                message_type = "failure" if not result.success else (
                    "review" if task.agent_name == "Reviewer Agent" else "handoff"
                )
                content = result.answer if result.success else (
                    f"Assignment unavailable after {result.attempts} attempt(s): {result.error or result.answer}"
                )
                targets = [consumer.agent_name for consumer in consumers] or ["Manager Agent"]
                for target in targets:
                    messages.append(
                        AgentMessage(
                            task_id=task_id,
                            subtask_id=subtask_id,
                            from_agent=task.agent_name,
                            to_agent=target,
                            message_type=message_type,
                            content=content[:4000],
                            evidence=result.evidence[:20],
                        )
                    )

        synthesis_task = next(
            task for task in plan.subtasks if task.agent_name == "Synthesis Agent"
        )
        final_result = results[synthesis_task.id]
        reviewer_result = next(
            (result for result in results.values() if result.agent_name == "Reviewer Agent"),
            None,
        )
        failed_agents = [
            {"task_id": task_id, "agent_name": result.agent_name, "error": result.error}
            for task_id, result in results.items()
            if not result.success
        ]
        agent_warnings = [
            {
                "task_id": task_id,
                "agent_name": result.agent_name,
                "warnings": result.warnings,
            }
            for task_id, result in results.items()
            if result.warnings
        ]
        evidence: list[dict[str, Any]] = []
        for result in results.values():
            for reference in result.evidence:
                item = reference.model_dump(mode="json")
                if item not in evidence:
                    evidence.append(item)

        return {
            "plan": plan.model_dump(mode="json"),
            "execution_waves": waves,
            "agent_results": {
                key: value.model_dump(mode="json") for key, value in results.items()
            },
            "agent_messages": [message.model_dump(mode="json") for message in messages],
            "final_report": final_result.answer,
            "confidence": final_result.confidence,
            "evidence": evidence[:30],
            "review": reviewer_result.review.model_dump(mode="json")
            if reviewer_result and reviewer_result.review
            else None,
            "success": final_result.success,
            "partial_failure": bool(failed_agents or agent_warnings),
            "failed_agents": failed_agents,
            "agent_warnings": agent_warnings,
        }