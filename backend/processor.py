"""Worker-side orchestration from a durable task to the final report."""

import logging

from backend.agents.meta import MetaAgent
from backend.models import TaskStatus
from backend.repository import TaskRepository
from backend.workflow import WorkflowEngine


logger = logging.getLogger(__name__)


class TaskCancelled(Exception):
    """Signal that the user cancelled a running task."""


class TaskProcessor:
    """Coordinate planning, workflow execution, and task updates."""

    def __init__(
        self,
        repo: TaskRepository,
        meta: MetaAgent,
        workflow: WorkflowEngine,
    ) -> None:
        self.repo = repo
        self.meta = meta
        self.workflow = workflow

    async def process(
        self,
        task_id: str,
        goal: str,
    ) -> None:
        """Process one claimed task and save its final state."""

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

            await self.repo.update(
                task_id,
                status=status,
                progress=percent,
                stage=stage,
            )

        try:
            await progress(
                TaskStatus.PLANNING,
                8,
                "Meta Agent is creating the execution plan",
            )

            plan = await self.meta.create_plan(goal)

            await self.repo.update(
                task_id,
                plan=plan.model_dump(mode="json"),
                progress=15,
                stage="Execution plan validated",
            )

            result = await self.workflow.execute(
                plan,
                progress,
            )

            # The workflow function may finish normally while
            # its agents or dependencies failed.
            if not result.get("success", False):
                failed_tasks = [
                    result_id
                    for result_id, agent_result
                    in result.get(
                        "agent_results",
                        {},
                    ).items()
                    if not agent_result.get(
                        "success",
                        False,
                    )
                ]

                await self.repo.update(
                    task_id,
                    status=TaskStatus.FAILED,
                    progress=100,
                    stage=(
                        "Workflow completed with "
                        "failed dependencies"
                    ),
                    result=result,
                    error=(
                        "Failed workflow tasks: "
                        + ", ".join(failed_tasks)
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
                stage="Complete",
                result=result,
            )

        except TaskCancelled:
            logger.info(
                "Task %s cancelled",
                task_id,
            )

        except Exception as exc:
            logger.exception(
                "Task %s failed",
                task_id,
            )

            await self.repo.update(
                task_id,
                status=TaskStatus.FAILED,
                stage="Failed",
                error=str(exc),
            )