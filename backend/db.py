"""Database connection and persistent task table for Meta AgentX."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Integer, JSON, String, Text
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

    # The unique ID returned to the frontend.
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    # The original business goal submitted by the user.
    goal: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    priority: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # queued, planning, executing, aggregating, complete, failed or cancelled.
    status: Mapped[str] = mapped_column(
        String(30),
        index=True,
        nullable=False,
    )

    progress: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    current_stage: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Validated execution plan proposed by the Meta Agent's LLM.
    plan: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Final workflow result and report.
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Worker reliability fields.
    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    worker_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

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


class Database:
    """Own the asynchronous SQLAlchemy engine and session factory."""

    def __init__(self, url: str) -> None:
        engine_options: dict[str, Any] = {
            "pool_pre_ping": True,
        }

        # Keep an in-memory SQLite test database on one shared connection.
        if url.endswith(":memory:"):
            engine_options["poolclass"] = StaticPool

        self.engine: AsyncEngine = create_async_engine(
            url,
            **engine_options,
        )

        self.sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def create_schema(self) -> None:
        """Create missing tables for local development and tests."""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Close pooled database connections during application shutdown."""
        await self.engine.dispose()
