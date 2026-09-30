"""Worker-side orchestration from a durable task to its final report."""

import asyncio
import logging
from contextlib import suppress

from backend.agents.meta import MetaAgent
from backend.models import TaskStatus
from backend.repository import TaskRepository
from backend.workflow import WorkflowEngine


logger = logging.getLogger(__name__)


class TaskCancelled(Exception):
    """Signal that a user cancelled the task while it was running."""


class TaskProcessor:
    """Coordinate planning, specialist execution, and persisted updates."""

    def __init__(
        self,
        repo: TaskRepository,
        meta: MetaAgent,
        workflow: WorkflowEngine,
        lease_refresh_seconds: float = 10,
    ) -> None:
        self.repo = repo
        self.meta = meta
        self.workflow = workflow
        self.lease_refresh_seconds = max(1.0, lease_refresh_seconds)

    async def process(self, task_id: str, goal: str) -> None:
        """Process one claimed task and safely persist its final state."""

        async def progress(
            status: TaskStatus,
            percent: int,
            stage: str,
        ) -> None:
            current = await self.repo.get(task_id)
            if current is None:
                raise KeyError(task_id)
            if current.status == TaskStatus.CANCELLED.value:
                raise TaskCancelled()

            changed = await self.repo.update(
                task_id,
                status=status,
                progress=percent,
                stage=stage,
            )
            if not changed:
                raise TaskCancelled()

        async def refresh_lease() -> None:
            while True:
                await asyncio.sleep(self.lease_refresh_seconds)
                try:
                    active = await self.repo.refresh_task_lease(task_id)
                except Exception:
                    logger.exception("Task lease refresh failed for %s", task_id)
                    continue
                if not active:
                    return

        lease_task = asyncio.create_task(refresh_lease())
        try:
            business_context = await self.repo.business_context_for_task(task_id)
            await progress(
                TaskStatus.PLANNING,
                8,
                "Manager Agent is creating the execution plan",
            )

            plan = await asyncio.wait_for(
                self.meta.create_plan(goal, business_context),
                timeout=self.workflow.agent_timeout_seconds,
            )
            plan_saved = await self.repo.update(
                task_id,
                plan=plan.model_dump(mode="json"),
                progress=15,
                stage="Execution plan validated",
            )
            if not plan_saved:
                raise TaskCancelled()

            result = await self.workflow.execute(
                plan,
                progress,
                business_context,
                task_id=task_id,
            )

            if not result.get("success", False):
                failed_tasks = [
                    entry.get("agent_name", task_key)
                    for task_key, entry in result.get("agent_results", {}).items()
                    if not entry.get("success", False)
                ]
                await self.repo.update(
                    task_id,
                    status=TaskStatus.FAILED,
                    progress=100,
                    stage="Synthesis failed after bounded retries",
                    result=result,
                    error=(
                        "The final synthesis could not be completed. Failed work: "
                        + (", ".join(failed_tasks) or "Synthesis Agent")
                        + ". Retry the task after checking model/provider availability."
                    ),
                )
                return

            await progress(
                TaskStatus.AGGREGATING,
                90,
                "Final report validated",
            )

            await self.repo.update(
                task_id,
                status=TaskStatus.COMPLETE,
                progress=100,
                stage=(
                    "Complete with partial findings"
                    if result.get("partial_failure")
                    else "Complete · reviewer checked the findings"
                ),
                result=result,
            )

        except TaskCancelled:
            logger.info("Task %s cancelled", task_id)

        except Exception as exc:
            logger.exception("Task %s failed", task_id)
            failure_detail = (
                "Manager Agent timed out while creating the plan. Check model/provider availability and retry."
                if isinstance(exc, TimeoutError)
                else f"{type(exc).__name__}: {str(exc)[:1000]}"
            )
            try:
                saved = await self.repo.update(
                    task_id,
                    status=TaskStatus.FAILED,
                    stage="Failed",
                    error=failure_detail,
                )
                if not saved:
                    logger.info("Task %s was already terminal when failure was persisted", task_id)
            except Exception:
                # The worker loop remains alive; a lost lease can be recovered
                # after the database returns if this failure cannot be saved.
                logger.exception("Could not persist failure for task %s", task_id)
        finally:
            lease_task.cancel()
            with suppress(asyncio.CancelledError):
                await lease_task