"""Durable worker that runs fake or Hugging Face agent workflows."""

import asyncio
import logging
import socket
from uuid import uuid4

from backend.agents.meta import MetaAgent
from backend.agents.registry import build_agents
from backend.config import get_settings
from backend.db import Database
from backend.llm.fake import FakeLLMProvider
from backend.llm.huggingface import HuggingFaceProvider
from backend.processor import TaskProcessor
from backend.repository import TaskRepository
from backend.workflow import WorkflowEngine


async def main() -> None:
    """Start the worker and continuously claim queued tasks."""

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    database = Database(
        settings.database_url
    )
    await database.create_schema()

    repository = TaskRepository(
        database
    )

    if settings.llm_mode == "fake":
        llm = FakeLLMProvider()
        logging.info(
            "Using free fake LLM mode"
        )
    else:
        llm = HuggingFaceProvider(
            settings
        )
        logging.info(
            "Using Hugging Face inference mode"
        )

    agents = build_agents(
        llm,
        settings,
    )

    meta_agent = MetaAgent(
        llm,
        settings,
        set(agents),
    )

    workflow = WorkflowEngine(
        agents
    )

    processor = TaskProcessor(
        repository,
        meta_agent,
        workflow,
    )

    worker_id = (
        f"{socket.gethostname()}-"
        f"{str(uuid4())[:8]}"
    )

    logging.info(
        "Worker %s started",
        worker_id,
    )

    try:
        while True:
            await repository.recover_stale(
                settings.worker_lease_seconds,
                settings.max_task_attempts,
            )

            task = await repository.claim_next(
                worker_id,
                settings.max_task_attempts,
            )

            if task is None:
                await asyncio.sleep(
                    settings.worker_poll_seconds
                )
                continue

            await processor.process(
                task.id,
                task.goal,
            )

    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())