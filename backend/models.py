"""Pydantic contracts shared by the API, agents, and worker."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskStatus(str, Enum):
    """Lifecycle states for a persisted orchestration run."""

    QUEUED = "queued"
    PLANNING = "planning"
    EXECUTING = "executing"
    AGGREGATING = "aggregating"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.COMPLETE.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}
)
ACTIVE_TASK_STATUSES = frozenset(
    {TaskStatus.PLANNING.value, TaskStatus.EXECUTING.value, TaskStatus.AGGREGATING.value}
)


class TaskSubmit(BaseModel):
    """Validated user input for creating a task."""

    goal: str = Field(min_length=10, max_length=20_000)
    priority: Literal["low", "medium", "high", "critical"] = "medium"
    use_memory: bool = True

    @field_validator("goal", mode="before")
    @classmethod
    def trim_goal(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class BusinessProfileUpdate(BaseModel):
    """Editable company profile reused as context for future task runs."""

    business_name: str = Field(default="", max_length=160)
    profile_text: str = Field(default="", max_length=20_000)

    @field_validator("business_name", "profile_text", mode="before")
    @classmethod
    def trim_profile_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class BusinessDocumentResponse(BaseModel):
    """Safe metadata for an uploaded business reference document."""

    id: str
    filename: str
    extension: str
    size_bytes: int
    extracted_characters: int
    was_truncated: bool
    uploaded_at: datetime


class BusinessContextResponse(BaseModel):
    """Saved profile, uploaded references, and their combined context size."""

    business_name: str
    profile_text: str
    updated_at: datetime | None
    documents: list[BusinessDocumentResponse]
    context_characters: int


class EvidenceReference(BaseModel):
    """A short, inspectable source excerpt supporting an agent claim."""

    source: str = Field(min_length=1, max_length=255)
    excerpt: str = Field(min_length=1, max_length=1200)
    location: str | None = Field(default=None, max_length=160)
    kind: Literal["document", "integration", "calculation", "prior_analysis"] = "document"

    @field_validator("source", "excerpt", mode="before")
    @classmethod
    def trim_evidence(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ReviewAssessment(BaseModel):
    """Reviewer checklist for grounding, arithmetic, and missing information."""

    used_supplied_documents: bool = False
    supported_by_data: bool = False
    calculations_consistent: bool | None = None
    hallucination_risk: Literal["low", "medium", "high"] = "medium"
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=2400)


class AgentMessage(BaseModel):
    """Persisted manager delegation or agent-to-agent handoff."""

    task_id: str
    subtask_id: str
    from_agent: str
    to_agent: str
    message_type: Literal["delegation", "handoff", "review", "failure"]
    content: str = Field(max_length=4000)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=20)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SubTask(BaseModel):
    """One specialist assignment in the dependency-aware execution graph."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    agent_name: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=5, max_length=5_000)
    dependencies: list[str] = Field(default_factory=list)

    @field_validator("agent_name", "goal", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("dependencies")
    @classmethod
    def unique_dependencies(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Dependencies must not contain duplicates")
        return value


class ExecutionPlan(BaseModel):
    """A dependency graph of agent work proposed by the Manager Agent."""

    goal: str = Field(min_length=1, max_length=20_000)
    subtasks: list[SubTask] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_graph(self) -> "ExecutionPlan":
        ids = [task.id for task in self.subtasks]
        if len(ids) != len(set(ids)):
            raise ValueError("Subtask IDs must be unique")

        known = set(ids)
        for task in self.subtasks:
            unknown = set(task.dependencies) - known
            if unknown:
                raise ValueError(f"{task.id} has unknown dependencies: {sorted(unknown)}")
            if task.id in task.dependencies:
                raise ValueError(f"{task.id} cannot depend on itself")

        remaining = {task.id: set(task.dependencies) for task in self.subtasks}
        completed: set[str] = set()
        while remaining:
            ready = {
                task_id
                for task_id, dependencies in remaining.items()
                if dependencies <= completed
            }
            if not ready:
                raise ValueError("Dependency cycle detected")
            completed.update(ready)
            for task_id in ready:
                remaining.pop(task_id)
        return self


class AgentAction(BaseModel):
    """Validated final response or tool decision from a specialist."""

    action: Literal["use_tool", "final"]
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=500)
    answer: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=20)
    review: ReviewAssessment | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "AgentAction":
        if self.action == "use_tool" and not self.tool:
            raise ValueError("Tool action requires a tool name")
        if self.action == "final" and not self.answer:
            raise ValueError("Final action requires an answer")
        return self


class AgentResult(BaseModel):
    """The outcome, confidence, and evidence trail from one specialist."""

    task_id: str
    agent_name: str
    success: bool
    answer: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    review: ReviewAssessment | None = None
    trace: list[dict[str, Any]] = Field(default_factory=list)
    attempts: int = Field(default=1, ge=1)
    warnings: list[str] = Field(default_factory=list, max_length=10)
    error: str | None = None


class TaskResponse(BaseModel):
    """Public task representation returned to the dashboard."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    goal: str
    priority: str
    status: TaskStatus
    progress: int
    current_stage: str
    plan: dict[str, Any] | None
    result: dict[str, Any] | None
    error: str | None
    attempts: int
    worker_id: str | None
    created_at: datetime
    updated_at: datetime


class TaskEventResponse(BaseModel):
    """One append-only lifecycle event in a task's audit trail."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: str
    event_type: str
    status: TaskStatus | None
    progress: int | None
    stage: str
    detail: dict[str, Any] | None
    created_at: datetime


class WorkerInfo(BaseModel):
    """Availability summary for one worker process."""

    worker_id: str
    hostname: str
    llm_mode: str
    status: Literal["online", "offline"]
    started_at: datetime
    last_seen_at: datetime


class WorkerHealth(BaseModel):
    """Current worker availability and recent process history."""

    status: Literal["online", "offline"]
    online_workers: int
    workers: list[WorkerInfo]


class TaskAccepted(BaseModel):
    """Acknowledgement returned immediately after a task is queued."""

    task_id: str
    status: TaskStatus
    message: str


class DashboardStats(BaseModel):
    """Lightweight aggregate counts for the workspace overview."""

    total: int
    queued: int
    active: int
    completed: int
    failed: int
    cancelled: int
    success_rate: float


class AgentInfo(BaseModel):
    """Public capability card for a registered agent role."""

    name: str
    category: str
    description: str
    tools: list[str]
    responsibility: str = ""


class IntegrationInfo(BaseModel):
    """Safe connector catalog entry; never returns credential values."""

    id: str
    name: str
    category: str
    description: str
    status: Literal["ready", "configured", "not_configured", "coming_soon"]
    connection_method: str
    environment_variables: list[str] = Field(default_factory=list)
    read_only: bool = True


class TaskTemplate(BaseModel):
    """A safe, reusable starting prompt for a common investigation."""

    id: str
    title: str
    description: str
    goal: str
    priority: Literal["low", "medium", "high", "critical"] = "medium"