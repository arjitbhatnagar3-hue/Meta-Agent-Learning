"""Database connection and persistent Meta AgentX tables."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    """Base class inherited by all SQLAlchemy database tables."""


class TaskRow(Base):
    """Persistent task record shared by FastAPI and worker processes."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_stage: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Worker reliability fields.
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class TaskEventRow(Base):
    """Append-only lifecycle event for the task activity timeline."""

    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class WorkerHeartbeatRow(Base):
    """Heartbeat/availability record for an independent worker process."""

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    llm_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
    )


class BusinessProfileRow(Base):
    """Reusable user-supplied company context for future task runs."""

    __tablename__ = "business_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    business_name: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    profile_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class BusinessDocumentRow(Base):
    """Document metadata/text plus local bytes or a private cloud object path."""

    __tablename__ = "business_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(10), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Keep an empty blob for cloud-backed files so older SQLite schemas with a
    # NOT NULL raw_content column remain compatible while migrating.
    raw_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, default=b"")
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    was_truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class TaskBusinessContextRow(Base):
    """Snapshot of the profile/documents active when a task was submitted."""

    __tablename__ = "task_business_contexts"

    task_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    context_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


def normalize_async_database_url(url: str) -> str:
    """Accept ordinary Postgres URLs and adapt them for SQLAlchemy asyncpg."""
    parsed = make_url(url)
    if parsed.drivername in {"postgres", "postgresql", "postgresql+asyncpg"}:
        query = dict(parsed.query)
        sslmode = query.pop("sslmode", None)
        if sslmode and "ssl" not in query:
            query["ssl"] = sslmode
        parsed = parsed.set(drivername="postgresql+asyncpg", query=query)
    return parsed.render_as_string(hide_password=False)


class Database:
    """Own the asynchronous SQLAlchemy engine and session factory."""

    def __init__(self, url: str) -> None:
        url = normalize_async_database_url(url)
        engine_options: dict[str, Any] = {"pool_pre_ping": True}

        # Keep an in-memory SQLite test database on one shared connection.
        if url.endswith(":memory:"):
            engine_options["poolclass"] = StaticPool

        self.engine: AsyncEngine = create_async_engine(url, **engine_options)
        self.sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def create_schema(self) -> None:
        """Create tables and apply small additive migrations for existing DBs."""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(self._apply_additive_migrations)

    @staticmethod
    def _apply_additive_migrations(sync_connection: Any) -> None:
        """Additive, portable migration for installations predating cloud files."""
        inspector = inspect(sync_connection)
        if not inspector.has_table("business_documents"):
            return
        columns = {column["name"] for column in inspector.get_columns("business_documents")}
        if "storage_path" not in columns:
            sync_connection.execute(
                text("ALTER TABLE business_documents ADD COLUMN storage_path VARCHAR(512)")
            )

    async def close(self) -> None:
        """Close pooled database connections during application shutdown."""
        await self.engine.dispose()