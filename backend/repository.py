"""Persistent task, lifecycle-event, and worker-health repository."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import (
    BusinessDocumentRow,
    BusinessProfileRow,
    Database,
    TaskBusinessContextRow,
    TaskEventRow,
    TaskRow,
    WorkerHeartbeatRow,
)
from backend.models import ACTIVE_TASK_STATUSES, TERMINAL_TASK_STATUSES, TaskStatus
from backend.storage import SupabaseObjectStorage


logger = logging.getLogger(__name__)
MAX_BUSINESS_CONTEXT_CHARS = 60_000
MAX_MEMORY_CHARS = 8_000


class TaskRepository:
    """Database access for task execution and workspace operations."""

    def __init__(
        self,
        db: Database,
        object_storage: SupabaseObjectStorage | None = None,
        memory_runs: int = 3,
    ) -> None:
        self.db = db
        self.object_storage = object_storage
        self.memory_runs = max(0, memory_runs)

    @staticmethod
    def _format_business_context(
        profile: BusinessProfileRow | None,
        documents: list[tuple[str, str]],
        memories: list[tuple[str, str]] | None = None,
    ) -> str:
        """Create a bounded, labeled context snapshot for an LLM prompt."""
        sections: list[str] = []
        if profile is not None:
            if profile.business_name.strip():
                sections.append(f"Business name: {profile.business_name.strip()}")
            if profile.profile_text.strip():
                sections.append("Business profile:\n" + profile.profile_text.strip())
        for filename, extracted_text in documents:
            if extracted_text.strip():
                sections.append(
                    f"Reference document ({filename}):\n{extracted_text.strip()}"
                )
        memory_section = ""
        if memories:
            memory_sections = [
                f"Prior question: {goal[:500]}\nPrior analysis: {report[:1800]}"
                for goal, report in memories
                if goal.strip() and report.strip()
            ]
            if memory_sections:
                memory_section = (
                    "Recent conversation memory (historical context, verify against current files):\n"
                    + "\n\n".join(memory_sections)[:MAX_MEMORY_CHARS]
                )

        combined = "\n\n".join(sections)
        if not memory_section and len(combined) <= MAX_BUSINESS_CONTEXT_CHARS:
            return combined

        # Reserve room at the end of the prompt for opted-in memory so a large
        # upload cannot silently evict every prior-analysis summary.
        shortened_note = "\n[Context shortened to fit the task limit.]"
        reserve = len(memory_section) + (2 if memory_section and combined else 0)
        base_limit = max(0, MAX_BUSINESS_CONTEXT_CHARS - reserve - len(shortened_note))
        if len(combined) > base_limit:
            combined = combined[:base_limit].rstrip() + shortened_note
        return combined + ("\n\n" if combined and memory_section else "") + memory_section

    async def _business_context_in_session(
        self,
        session: AsyncSession,
        *,
        include_memory: bool = False,
    ) -> str:
        profile = await session.get(BusinessProfileRow, 1)
        rows = await session.execute(
            select(BusinessDocumentRow.filename, BusinessDocumentRow.extracted_text)
            .order_by(BusinessDocumentRow.uploaded_at, BusinessDocumentRow.id)
        )
        memories: list[tuple[str, str]] = []
        if include_memory and self.memory_runs:
            prior_tasks = await session.execute(
                select(TaskRow.goal, TaskRow.result)
                .where(
                    TaskRow.status == TaskStatus.COMPLETE.value,
                    TaskRow.result.is_not(None),
                )
                .order_by(TaskRow.created_at.desc(), TaskRow.id.desc())
                .limit(self.memory_runs)
            )
            for goal, result in prior_tasks:
                report = result.get("final_report", "") if isinstance(result, dict) else ""
                if report:
                    memories.append((goal, str(report)))
        return self._format_business_context(
            profile,
            list(rows.all()),
            memories,
        )

    async def get_business_profile(self) -> BusinessProfileRow | None:
        """Return the saved company profile, if one has been created."""
        async with self.db.sessions() as session:
            return await session.get(BusinessProfileRow, 1)

    async def update_business_profile(
        self,
        business_name: str,
        profile_text: str,
    ) -> BusinessProfileRow:
        """Create or update the single reusable workspace business profile."""
        now = datetime.now(timezone.utc)
        async with self.db.sessions() as session:
            row = await session.get(BusinessProfileRow, 1)
            if row is None:
                row = BusinessProfileRow(
                    id=1,
                    business_name=business_name,
                    profile_text=profile_text,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.business_name = business_name
                row.profile_text = profile_text
                row.updated_at = now
            await session.commit()
            await session.refresh(row)
            return row

    async def business_document_usage(self) -> tuple[int, int]:
        """Return the current number of uploads and their total byte size."""
        async with self.db.sessions() as session:
            count, size = (
                await session.execute(
                    select(
                        func.count(BusinessDocumentRow.id),
                        func.coalesce(func.sum(BusinessDocumentRow.size_bytes), 0),
                    )
                )
            ).one()
            return int(count or 0), int(size or 0)

    async def list_business_documents(self) -> list[dict[str, Any]]:
        """Return document metadata without loading original file blobs."""
        statement = (
            select(
                BusinessDocumentRow.id,
                BusinessDocumentRow.filename,
                BusinessDocumentRow.extension,
                BusinessDocumentRow.size_bytes,
                func.length(BusinessDocumentRow.extracted_text).label(
                    "extracted_characters"
                ),
                BusinessDocumentRow.was_truncated,
                BusinessDocumentRow.uploaded_at,
            )
            .order_by(BusinessDocumentRow.uploaded_at.desc())
        )
        async with self.db.sessions() as session:
            rows = await session.execute(statement)
            return [dict(row._mapping) for row in rows]

    async def add_business_document(
        self,
        *,
        filename: str,
        extension: str,
        media_type: str,
        size_bytes: int,
        raw_content: bytes,
        extracted_text: str,
        was_truncated: bool,
    ) -> BusinessDocumentRow:
        """Persist document text in Postgres/SQLite and bytes locally or in Storage."""
        document_id = str(uuid4())
        storage_path: str | None = None
        stored_bytes = raw_content
        if self.object_storage is not None:
            storage_path = f"business-documents/{document_id}/{filename}"
            await self.object_storage.upload(storage_path, raw_content, media_type)
            stored_bytes = b""

        row = BusinessDocumentRow(
            id=document_id,
            filename=filename,
            extension=extension,
            media_type=media_type,
            size_bytes=size_bytes,
            raw_content=stored_bytes,
            storage_path=storage_path,
            extracted_text=extracted_text,
            was_truncated=was_truncated,
            uploaded_at=datetime.now(timezone.utc),
        )
        try:
            async with self.db.sessions() as session:
                session.add(row)
                await session.commit()
                await session.refresh(row)
                return row
        except Exception:
            if storage_path and self.object_storage is not None:
                try:
                    await self.object_storage.delete(storage_path)
                except Exception:
                    logger.exception("Could not clean up orphaned uploaded object")
            raise

    async def get_business_document(self, document_id: str) -> BusinessDocumentRow | None:
        """Load one document's metadata and extracted text."""
        async with self.db.sessions() as session:
            return await session.get(BusinessDocumentRow, document_id)

    async def download_business_document(self, document_id: str) -> tuple[BusinessDocumentRow, bytes] | None:
        """Return original bytes from local DB or configured private object storage."""
        row = await self.get_business_document(document_id)
        if row is None:
            return None
        if row.storage_path:
            if self.object_storage is None:
                raise RuntimeError("This file is cloud-backed but Storage is not configured.")
            content = await self.object_storage.download(row.storage_path)
        else:
            content = row.raw_content
        return row, content

    async def delete_business_document(self, document_id: str) -> bool:
        """Delete a document so future tasks no longer receive its contents."""
        async with self.db.sessions() as session:
            row = await session.get(BusinessDocumentRow, document_id)
            if row is None:
                return False
            storage_path = row.storage_path
            await session.delete(row)
            await session.commit()
        if storage_path and self.object_storage is not None:
            try:
                await self.object_storage.delete(storage_path)
            except Exception:
                # The DB entry is gone, so the object is no longer visible to
                # this workspace; log a cleanup task rather than reviving it.
                logger.exception("Supabase object deletion failed for document %s", document_id)
        return True

    async def business_context_for_task(self, task_id: str) -> str:
        """Return the immutable context snapshot stored when a task was queued."""
        async with self.db.sessions() as session:
            snapshot = await session.get(TaskBusinessContextRow, task_id)
            return snapshot.context_text if snapshot is not None else ""

    async def current_business_context(self) -> str:
        """Assemble the latest saved profile and uploaded document text."""
        async with self.db.sessions() as session:
            return await self._business_context_in_session(session)

    @staticmethod
    def _record_event(
        session: AsyncSession,
        *,
        task_id: str,
        event_type: str,
        stage: str,
        status: TaskStatus | None = None,
        progress: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Add an audit event to the transaction currently being committed."""
        session.add(
            TaskEventRow(
                task_id=task_id,
                event_type=event_type,
                status=status.value if status else None,
                progress=progress,
                stage=stage,
                detail=detail,
                created_at=datetime.now(timezone.utc),
            )
        )

    async def create(
        self,
        goal: str,
        priority: str,
        *,
        use_memory: bool = True,
    ) -> TaskRow:
        """Persist a task, immutable context snapshot, and queue event."""
        now = datetime.now(timezone.utc)
        row = TaskRow(
            id=str(uuid4()),
            goal=goal,
            priority=priority,
            status=TaskStatus.QUEUED.value,
            progress=0,
            current_stage="Waiting for worker",
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        async with self.db.sessions() as session:
            context_text = await self._business_context_in_session(
                session,
                include_memory=use_memory,
            )
            session.add(row)
            session.add(
                TaskBusinessContextRow(
                    task_id=row.id,
                    context_text=context_text,
                    created_at=now,
                )
            )
            self._record_event(
                session,
                task_id=row.id,
                event_type="queued",
                status=TaskStatus.QUEUED,
                progress=0,
                stage="Task added to the queue",
            )
            await session.commit()
            await session.refresh(row)
            return row

    async def get(self, task_id: str) -> TaskRow | None:
        """Return one task by ID, or ``None`` when it does not exist."""
        async with self.db.sessions() as session:
            return await session.get(TaskRow, task_id)

    async def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        status: TaskStatus | None = None,
        query: str | None = None,
    ) -> list[TaskRow]:
        """Return the newest matching tasks for list and search views."""
        statement = select(TaskRow).order_by(
            TaskRow.created_at.desc(),
            TaskRow.id.desc(),
        )
        if status is not None:
            statement = statement.where(TaskRow.status == status.value)
        if query:
            term = f"%{query.strip()}%"
            statement = statement.where(
                TaskRow.goal.ilike(term) | TaskRow.id.ilike(term)
            )
        statement = statement.offset(offset).limit(limit)

        async with self.db.sessions() as session:
            result = await session.execute(statement)
            return list(result.scalars())

    async def list_events(
        self,
        task_id: str,
        *,
        limit: int = 200,
    ) -> list[TaskEventRow]:
        """Return the latest audit events in chronological display order."""
        statement = (
            select(TaskEventRow)
            .where(TaskEventRow.task_id == task_id)
            .order_by(TaskEventRow.created_at.desc(), TaskEventRow.id.desc())
            .limit(limit)
        )
        async with self.db.sessions() as session:
            result = await session.execute(statement)
            return list(result.scalars())[::-1]

    async def stats(self) -> dict[str, int | float]:
        """Return status totals and the completed-vs-failed success rate."""
        async with self.db.sessions() as session:
            rows = await session.execute(
                select(TaskRow.status, func.count(TaskRow.id)).group_by(
                    TaskRow.status
                )
            )
            counts = {status: int(count) for status, count in rows}

        queued = counts.get(TaskStatus.QUEUED.value, 0)
        active = sum(counts.get(status, 0) for status in ACTIVE_TASK_STATUSES)
        completed = counts.get(TaskStatus.COMPLETE.value, 0)
        failed = counts.get(TaskStatus.FAILED.value, 0)
        cancelled = counts.get(TaskStatus.CANCELLED.value, 0)
        decided = completed + failed
        return {
            "total": sum(counts.values()),
            "queued": queued,
            "active": active,
            "completed": completed,
            "failed": failed,
            "cancelled": cancelled,
            "success_rate": round(completed / decided * 100, 1) if decided else 0.0,
        }

    async def claim_next(self, worker_id: str, max_attempts: int) -> TaskRow | None:
        """Atomically lease the oldest queued task to one worker."""
        for _ in range(5):
            async with self.db.sessions() as session:
                candidate = await session.scalar(
                    select(TaskRow.id)
                    .where(
                        TaskRow.status == TaskStatus.QUEUED.value,
                        TaskRow.attempts < max_attempts,
                    )
                    .order_by(TaskRow.created_at, TaskRow.id)
                    .limit(1)
                )
                if candidate is None:
                    return None

                now = datetime.now(timezone.utc)
                changed = await session.execute(
                    update(TaskRow)
                    .where(
                        TaskRow.id == candidate,
                        TaskRow.status == TaskStatus.QUEUED.value,
                    )
                    .values(
                        status=TaskStatus.PLANNING.value,
                        progress=5,
                        current_stage="Claimed by worker",
                        worker_id=worker_id,
                        locked_at=now,
                        attempts=TaskRow.attempts + 1,
                        updated_at=now,
                    )
                )
                if changed.rowcount == 1:
                    self._record_event(
                        session,
                        task_id=candidate,
                        event_type="claimed",
                        status=TaskStatus.PLANNING,
                        progress=5,
                        stage="Task claimed by worker",
                        detail={"worker_id": worker_id},
                    )
                    await session.commit()
                    return await self.get(candidate)
                await session.commit()
        return None

    async def refresh_task_lease(self, task_id: str) -> bool:
        """Extend an active task's lease without adding a noisy timeline event."""
        now = datetime.now(timezone.utc)
        async with self.db.sessions() as session:
            changed = await session.execute(
                update(TaskRow)
                .where(
                    TaskRow.id == task_id,
                    TaskRow.status.in_(ACTIVE_TASK_STATUSES),
                )
                .values(locked_at=now, updated_at=now)
            )
            await session.commit()
            return changed.rowcount == 1

    async def update(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        progress: int | None = None,
        stage: str | None = None,
        plan: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        """Update a task without allowing late worker writes to undo cancel.

        The task update and audit event share one transaction. A false result
        means the task is already terminal or no longer exists.
        """
        now = datetime.now(timezone.utc)
        values: dict[str, Any] = {"updated_at": now}
        if status is not None:
            values["status"] = status.value
        if progress is not None:
            values["progress"] = progress
        if stage is not None:
            values["current_stage"] = stage
        if plan is not None:
            values["plan"] = plan
        if result is not None:
            values["result"] = result
        if error is not None:
            values["error"] = error

        if status in {
            TaskStatus.PLANNING,
            TaskStatus.EXECUTING,
            TaskStatus.AGGREGATING,
        }:
            values["locked_at"] = now
        elif status in TERMINAL_TASK_STATUSES:
            values.update(locked_at=None, worker_id=None)
        elif status is None:
            values["locked_at"] = now

        async with self.db.sessions() as session:
            changed = await session.execute(
                update(TaskRow)
                .where(
                    TaskRow.id == task_id,
                    TaskRow.status.not_in(TERMINAL_TASK_STATUSES),
                )
                .values(**values)
            )
            if changed.rowcount == 1:
                event_status = status
                event_type = status.value if status else "stage"
                self._record_event(
                    session,
                    task_id=task_id,
                    event_type=event_type,
                    status=event_status,
                    progress=progress,
                    stage=stage or (status.value if status else "Task updated"),
                )
            await session.commit()
            return changed.rowcount == 1

    async def cancel(self, task_id: str) -> bool:
        """Cancel a queued or running task; leave terminal tasks untouched."""
        now = datetime.now(timezone.utc)
        async with self.db.sessions() as session:
            changed = await session.execute(
                update(TaskRow)
                .where(
                    TaskRow.id == task_id,
                    TaskRow.status.not_in(TERMINAL_TASK_STATUSES),
                )
                .values(
                    status=TaskStatus.CANCELLED.value,
                    current_stage="Cancelled",
                    worker_id=None,
                    locked_at=None,
                    updated_at=now,
                )
            )
            if changed.rowcount == 1:
                self._record_event(
                    session,
                    task_id=task_id,
                    event_type="cancelled",
                    status=TaskStatus.CANCELLED,
                    stage="Task cancelled by user",
                )
            await session.commit()
            return changed.rowcount == 1

    async def retry(self, task_id: str) -> TaskRow | None:
        """Requeue a failed or cancelled task as a clean new attempt."""
        now = datetime.now(timezone.utc)
        async with self.db.sessions() as session:
            changed = await session.execute(
                update(TaskRow)
                .where(
                    TaskRow.id == task_id,
                    TaskRow.status.in_(
                        [TaskStatus.FAILED.value, TaskStatus.CANCELLED.value]
                    ),
                )
                .values(
                    status=TaskStatus.QUEUED.value,
                    progress=0,
                    current_stage="Waiting for worker",
                    plan=None,
                    result=None,
                    error=None,
                    attempts=0,
                    worker_id=None,
                    locked_at=None,
                    updated_at=now,
                )
            )
            if changed.rowcount == 1:
                self._record_event(
                    session,
                    task_id=task_id,
                    event_type="retried",
                    status=TaskStatus.QUEUED,
                    progress=0,
                    stage="Task requeued for a fresh attempt",
                )
            await session.commit()
            if changed.rowcount != 1:
                return None
            return await session.get(TaskRow, task_id)

    async def recover_stale(self, lease_seconds: int, max_attempts: int) -> int:
        """Requeue expired worker leases, or fail them after the retry limit."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=lease_seconds)
        active_statuses = list(ACTIVE_TASK_STATUSES)
        recovered = 0

        async with self.db.sessions() as session:
            stale_rows = await session.execute(
                select(TaskRow.id, TaskRow.attempts).where(
                    TaskRow.status.in_(active_statuses),
                    TaskRow.locked_at < cutoff,
                )
            )
            candidates = list(stale_rows)

            for task_id, attempts in candidates:
                should_requeue = attempts < max_attempts
                status = TaskStatus.QUEUED if should_requeue else TaskStatus.FAILED
                stage = (
                    "Recovered after worker lease expired"
                    if should_requeue
                    else "Retry limit exceeded"
                )
                values: dict[str, Any] = {
                    "status": status.value,
                    "current_stage": stage,
                    "worker_id": None,
                    "locked_at": None,
                    "updated_at": now,
                }
                if should_requeue:
                    values["progress"] = 0
                else:
                    values["error"] = "Worker lease expired repeatedly"

                changed = await session.execute(
                    update(TaskRow)
                    .where(
                        TaskRow.id == task_id,
                        TaskRow.status.in_(active_statuses),
                        TaskRow.locked_at < cutoff,
                        TaskRow.attempts < max_attempts
                        if should_requeue
                        else TaskRow.attempts >= max_attempts,
                    )
                    .values(**values)
                )
                if changed.rowcount == 1:
                    self._record_event(
                        session,
                        task_id=task_id,
                        event_type="lease_recovered",
                        status=status,
                        progress=0 if should_requeue else None,
                        stage=stage,
                        detail={"attempts": attempts},
                    )
                    recovered += 1

            await session.commit()
        return recovered

    async def heartbeat(
        self,
        worker_id: str,
        hostname: str,
        llm_mode: str,
    ) -> None:
        """Create or refresh the liveness record for one worker process."""
        now = datetime.now(timezone.utc)
        async with self.db.sessions() as session:
            worker = await session.get(WorkerHeartbeatRow, worker_id)
            if worker is None:
                worker = WorkerHeartbeatRow(
                    worker_id=worker_id,
                    hostname=hostname,
                    llm_mode=llm_mode,
                    status="online",
                    started_at=now,
                    last_seen_at=now,
                )
                session.add(worker)
            else:
                worker.hostname = hostname
                worker.llm_mode = llm_mode
                worker.status = "online"
                worker.last_seen_at = now
            await session.commit()

    async def stop_heartbeat(self, worker_id: str) -> None:
        """Mark a gracefully-shutdown worker as offline."""
        async with self.db.sessions() as session:
            await session.execute(
                update(WorkerHeartbeatRow)
                .where(WorkerHeartbeatRow.worker_id == worker_id)
                .values(
                    status="offline",
                    last_seen_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    async def worker_health(self, stale_after_seconds: int = 30) -> dict[str, Any]:
        """Return online workers and recent processes for the health dashboard."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=stale_after_seconds)
        async with self.db.sessions() as session:
            online_result = await session.execute(
                select(WorkerHeartbeatRow.worker_id).where(
                    WorkerHeartbeatRow.status == "online",
                    WorkerHeartbeatRow.last_seen_at >= cutoff,
                )
            )
            online_ids = set(online_result.scalars())
            recent_result = await session.execute(
                select(WorkerHeartbeatRow)
                .order_by(WorkerHeartbeatRow.last_seen_at.desc())
                .limit(5)
            )
            recent_workers = list(recent_result.scalars())

        workers = [
            {
                "worker_id": worker.worker_id,
                "hostname": worker.hostname,
                "llm_mode": worker.llm_mode,
                "status": "online" if worker.worker_id in online_ids else "offline",
                "started_at": worker.started_at,
                "last_seen_at": worker.last_seen_at,
            }
            for worker in recent_workers
        ]
        return {
            "status": "online" if online_ids else "offline",
            "online_workers": len(online_ids),
            "workers": workers,
        }