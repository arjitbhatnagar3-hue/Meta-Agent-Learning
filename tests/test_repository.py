"""Repository tests for leases, audit events, and worker health."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from backend.db import Database, TaskRow
from backend.models import TaskStatus
from backend.repository import TaskRepository


@pytest.mark.asyncio
async def test_cancelled_task_cannot_be_overwritten_by_late_worker_update():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    repository = TaskRepository(database)
    try:
        created = await repository.create(
            "Investigate a customer retention trend across multiple segments.",
            "medium",
        )
        claimed = await repository.claim_next("test-worker", max_attempts=3)
        assert claimed is not None
        assert claimed.id == created.id

        assert await repository.cancel(created.id) is True
        saved = await repository.update(
            created.id,
            status=TaskStatus.COMPLETE,
            progress=100,
            stage="Complete",
            result={"final_report": "late worker response"},
        )
        current = await repository.get(created.id)

        assert saved is False
        assert current is not None
        assert current.status == TaskStatus.CANCELLED.value
        assert current.result is None
        events = await repository.list_events(created.id)
        assert [event.event_type for event in events] == [
            "queued",
            "claimed",
            "cancelled",
        ]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_stale_worker_lease_requeues_task_and_records_event():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    repository = TaskRepository(database)
    try:
        task = await repository.create(
            "Investigate a customer retention trend across multiple segments.",
            "high",
        )
        assert await repository.claim_next("test-worker", max_attempts=3)

        async with database.sessions() as session:
            await session.execute(
                update(TaskRow)
                .where(TaskRow.id == task.id)
                .values(
                    locked_at=datetime.now(timezone.utc) - timedelta(minutes=5)
                )
            )
            await session.commit()

        assert await repository.recover_stale(lease_seconds=30, max_attempts=3) == 1
        current = await repository.get(task.id)
        assert current is not None
        assert current.status == TaskStatus.QUEUED.value
        events = await repository.list_events(task.id)
        assert events[-1].event_type == "lease_recovered"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_worker_heartbeat_reports_online_then_offline():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    repository = TaskRepository(database)
    try:
        assert (await repository.worker_health())["status"] == "offline"
        await repository.heartbeat("unit-worker", "test-host", "fake")
        online = await repository.worker_health()
        assert online["status"] == "online"
        assert online["online_workers"] == 1
        assert online["workers"][0]["worker_id"] == "unit-worker"

        await repository.stop_heartbeat("unit-worker")
        offline = await repository.worker_health()
        assert offline["status"] == "offline"
        assert offline["workers"][0]["status"] == "offline"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_new_tasks_snapshot_saved_profile_and_uploaded_document_context():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    repository = TaskRepository(database)
    try:
        await repository.update_business_profile(
            "Acme Audio",
            "We make compact speakers for independent musicians.",
        )
        await repository.add_business_document(
            filename="product-brief.txt",
            extension=".txt",
            media_type="text/plain; charset=utf-8",
            size_bytes=27,
            raw_content=b"Battery lasts twelve hours.",
            extracted_text="Battery lasts twelve hours.",
            was_truncated=False,
        )

        first_task = await repository.create(
            "Find ways to improve repeat purchases this year.",
            "medium",
        )
        first_snapshot = await repository.business_context_for_task(first_task.id)
        assert "Acme Audio" in first_snapshot
        assert "independent musicians" in first_snapshot
        assert "Battery lasts twelve hours" in first_snapshot

        await repository.update_business_profile(
            "Acme Audio",
            "We now focus on studio creators.",
        )
        documents = await repository.list_business_documents()
        assert len(documents) == 1
        assert await repository.delete_business_document(documents[0]["id"])

        second_task = await repository.create(
            "Review the updated product positioning for next quarter.",
            "low",
        )
        second_snapshot = await repository.business_context_for_task(second_task.id)
        assert "studio creators" in second_snapshot
        assert "Battery lasts twelve hours" not in second_snapshot
        assert await repository.business_context_for_task(first_task.id) == first_snapshot
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_recent_analysis_memory_is_snapshotted_for_future_tasks():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    repository = TaskRepository(database, memory_runs=2)
    try:
        prior = await repository.create(
            "Summarize the uploaded customer invoice patterns.",
            "medium",
            use_memory=False,
        )
        await repository.update(
            prior.id,
            status=TaskStatus.COMPLETE,
            progress=100,
            stage="Complete",
            result={"final_report": "Prior finding: invoices are denominated in USD."},
        )
        follow_up = await repository.create(
            "Compare the next month of uploaded invoices with the prior review.",
            "high",
            use_memory=True,
        )
        snapshot = await repository.business_context_for_task(follow_up.id)
        assert "Recent conversation memory" in snapshot
        assert "denominated in USD" in snapshot

        without_memory = await repository.create(
            "Start a separate analysis without using earlier findings.",
            "low",
            use_memory=False,
        )
        clean_snapshot = await repository.business_context_for_task(without_memory.id)
        assert "Recent conversation memory" not in clean_snapshot
    finally:
        await database.close()


class MemoryObjectStorage:
    def __init__(self):
        self.items = {}

    async def upload(self, path, content, media_type):
        self.items[path] = (content, media_type)

    async def download(self, path):
        return self.items[path][0]

    async def delete(self, path):
        self.items.pop(path, None)


@pytest.mark.asyncio
async def test_cloud_backed_document_bytes_use_object_storage():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    storage = MemoryObjectStorage()
    repository = TaskRepository(database, object_storage=storage)
    content = b"Invoice INV-1 total USD 25.00"
    try:
        document = await repository.add_business_document(
            filename="invoice.txt",
            extension=".txt",
            media_type="text/plain",
            size_bytes=len(content),
            raw_content=content,
            extracted_text=content.decode(),
            was_truncated=False,
        )
        assert document.storage_path in storage.items
        assert document.raw_content == b""
        stored = await repository.download_business_document(document.id)
        assert stored is not None
        assert stored[1] == content
        assert await repository.delete_business_document(document.id) is True
        assert storage.items == {}
    finally:
        await database.close()


def test_recent_analysis_memory_is_reserved_with_large_uploads():
    context = TaskRepository._format_business_context(
        None,
        [("oversized.txt", "x" * 70_000)],
        [("Prior question", "Persistent finding from a previous task.")],
    )
    assert len(context) <= 60_000
    assert "Context shortened to fit" in context
    assert "Recent conversation memory" in context
    assert "Persistent finding from a previous task." in context


@pytest.mark.asyncio
async def test_active_task_lease_refresh_keeps_long_agent_calls_recoverable():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    repository = TaskRepository(database)
    try:
        task = await repository.create("Review the uploaded invoice and cite your finding.", "medium")
        assert await repository.claim_next("lease-worker", max_attempts=3)
        async with database.sessions() as session:
            await session.execute(
                update(TaskRow)
                .where(TaskRow.id == task.id)
                .values(locked_at=datetime.now(timezone.utc) - timedelta(minutes=10))
            )
            await session.commit()

        assert await repository.refresh_task_lease(task.id) is True
        current = await repository.get(task.id)
        assert current is not None
        assert current.locked_at is not None
        refreshed_at = current.locked_at.replace(tzinfo=timezone.utc) if current.locked_at.tzinfo is None else current.locked_at
        assert refreshed_at > datetime.now(timezone.utc) - timedelta(minutes=1)
        assert await repository.cancel(task.id) is True
        assert await repository.refresh_task_lease(task.id) is False
    finally:
        await database.close()