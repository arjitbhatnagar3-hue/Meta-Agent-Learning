"""FastAPI application for Meta AgentX and its static dashboard."""

import asyncio
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from backend.agents.registry import agent_catalog
from backend.config import Settings, get_settings
from backend.db import Database
from backend.integrations import (
    IntegrationNotConfigured,
    IntegrationRequestError,
    list_integrations,
    test_integration,
)
from backend.models import (
    AgentInfo,
    BusinessContextResponse,
    BusinessDocumentResponse,
    BusinessProfileUpdate,
    DashboardStats,
    TaskAccepted,
    TaskEventResponse,
    TaskResponse,
    TaskStatus,
    TaskSubmit,
    TaskTemplate,
    TERMINAL_TASK_STATUSES,
)
from backend.repository import TaskRepository
from backend.storage import SupabaseObjectStorage, SupabaseStorageError


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
TASK_TEMPLATES = [
    TaskTemplate(
        id="invoice-audit",
        title="Invoice audit",
        description="Compare uploaded invoices and flag differences or missing fields.",
        goal=(
            "Review the uploaded invoices. Compare invoice number, vendor, issue "
            "date, currency, subtotal, tax, total, and payment status. Cite each "
            "finding to a source file and list any fields that are missing or unclear."
        ),
        priority="high",
    ),
    TaskTemplate(
        id="csv-summary",
        title="CSV analysis",
        description="Summarize the uploaded table and explain important patterns.",
        goal=(
            "Analyze the uploaded CSV files. Summarize the columns and available "
            "records, calculate only figures supported by those rows, identify "
            "notable patterns, and call out missing or inconsistent data."
        ),
        priority="medium",
    ),
    TaskTemplate(
        id="document-cross-check",
        title="Cross-check documents",
        description="Find facts that agree or conflict across supplied documents.",
        goal=(
            "Cross-reference the uploaded business documents. List claims that "
            "are corroborated, conflicting figures or dates, the source file for "
            "each finding, and what information is still needed."
        ),
        priority="medium",
    ),
    TaskTemplate(
        id="anomaly-review",
        title="Anomaly review",
        description="Look for duplicates, unusual values, and incomplete records.",
        goal=(
            "Inspect the uploaded business documents and CSV exports for "
            "duplicates, unusual amounts, missing fields, or inconsistent dates. "
            "Treat anomalies as items to verify, not proof of misconduct, and cite sources."
        ),
        priority="high",
    ),
]


MAX_BUSINESS_FILE_BYTES = 5 * 1024 * 1024
MAX_BUSINESS_TOTAL_BYTES = 25 * 1024 * 1024
MAX_BUSINESS_DOCUMENTS = 12
MAX_EXTRACTED_DOCUMENT_CHARS = 25_000
ALLOWED_BUSINESS_FILE_TYPES = {".pdf", ".csv", ".txt", ".md"}
BUSINESS_FILE_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".csv": "text/csv; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}


def _extract_document_text(extension: str, content: bytes) -> tuple[str, bool]:
    """Extract bounded UTF-8 text from an allowed reference document."""
    was_truncated = False
    if extension == ".pdf":
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise ValueError("Password-protected PDFs are not supported.")
        chunks: list[str] = []
        characters = 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if not page_text:
                continue
            remaining = MAX_EXTRACTED_DOCUMENT_CHARS - characters
            if remaining <= 0:
                was_truncated = True
                break
            if len(page_text) > remaining:
                chunks.append(page_text[:remaining])
                was_truncated = True
                break
            chunks.append(page_text)
            characters += len(page_text)
        text = "\n".join(chunks)
    else:
        text = content.decode("utf-8-sig", errors="replace")
        text = "".join(
            character
            for character in text
            if character in "\n\r\t" or ord(character) >= 32
        )
        if len(text) > MAX_EXTRACTED_DOCUMENT_CHARS:
            text = text[:MAX_EXTRACTED_DOCUMENT_CHARS]
            was_truncated = True

    text = text.strip()
    if not text:
        raise ValueError("No readable text was found in the uploaded file.")
    return text, was_truncated


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Prepare database tables and release pooled connections on shutdown."""
    database: Database = app.state.database
    await database.create_schema()
    try:
        yield
    finally:
        await database.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an application instance; injectable settings keep tests isolated."""
    app_settings = settings or get_settings()
    database = Database(app_settings.database_url)
    object_storage = (
        SupabaseObjectStorage(app_settings)
        if app_settings.supabase_storage_configured
        else None
    )
    repository = TaskRepository(
        database,
        object_storage=object_storage,
        memory_runs=app_settings.conversation_memory_runs,
    )

    app = FastAPI(
        title="Meta AgentX",
        description="A multi-agent workspace for structured business analysis.",
        version="1.0.0",
        lifespan=_lifespan,
    )
    app.state.settings = app_settings
    app.state.database = database
    app.state.object_storage = object_storage
    app.state.repository = repository

    # The production dashboard is served from the API origin. These origins
    # are only needed when running a separate local frontend dev server.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    if FRONTEND_DIR.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=FRONTEND_DIR),
            name="dashboard-assets",
        )

        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        async def dashboard() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Report database, worker liveness, and configured inference mode."""
        try:
            async with database.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database is unavailable",
            ) from exc

        if app_settings.llm_mode == "fake":
            provider = "Local deterministic demo"
            model = "fake-local-development-model"
        else:
            provider = "Hugging Face Inference Providers"
            model = app_settings.hf_meta_model_id

        worker_health = await repository.worker_health(
            stale_after_seconds=max(
                app_settings.worker_heartbeat_seconds * 3,
                30,
            )
        )
        database_kind = (
            "supabase_postgres"
            if database.engine.url.drivername.startswith("postgresql")
            else "local_sqlite"
        )
        file_storage = (
            f"supabase_storage:{app_settings.supabase_storage_bucket}"
            if object_storage is not None
            else "local_database"
        )
        return {
            "status": "healthy",
            "database": "connected",
            "database_kind": database_kind,
            "file_storage": file_storage,
            "storage_configuration_warning": (
                "Supabase Storage URL and service key must be configured together."
                if app_settings.supabase_storage_partially_configured
                else None
            ),
            "llm_mode": app_settings.llm_mode,
            "llm_provider": provider,
            "model": model,
            "worker": worker_health,
        }

    @app.get("/api/v1/stats", response_model=DashboardStats)
    async def dashboard_stats() -> dict[str, int | float]:
        """Return aggregate counts for the workspace cards."""
        return await repository.stats()

    @app.get("/api/v1/agents", response_model=list[AgentInfo])
    async def agents() -> list[AgentInfo]:
        """Describe the manager and specialists with configured tool access."""
        return agent_catalog(app_settings)

    @app.get("/api/v1/integrations")
    async def integrations() -> list[dict[str, Any]]:
        """List supported connector options without exposing any secret values."""
        return [item.model_dump(mode="json") for item in list_integrations(app_settings)]

    @app.post("/api/v1/integrations/{integration_id}/test")
    async def check_integration(integration_id: str) -> dict[str, Any]:
        """Test a configured integration using a low-impact read-only request."""
        try:
            return await test_integration(integration_id, app_settings)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown integration option.") from exc
        except IntegrationNotConfigured as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except IntegrationRequestError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/v1/templates", response_model=list[TaskTemplate])
    async def templates() -> list[TaskTemplate]:
        """Return curated prompts that can be launched from the dashboard."""
        return TASK_TEMPLATES

    async def business_context_response() -> BusinessContextResponse:
        profile = await repository.get_business_profile()
        documents = await repository.list_business_documents()
        return BusinessContextResponse(
            business_name=profile.business_name if profile else "",
            profile_text=profile.profile_text if profile else "",
            updated_at=profile.updated_at if profile else None,
            documents=[BusinessDocumentResponse(**document) for document in documents],
            context_characters=len(await repository.current_business_context()),
        )

    @app.get(
        "/api/v1/business-context",
        response_model=BusinessContextResponse,
    )
    async def get_business_context() -> BusinessContextResponse:
        """Return the saved profile and uploaded reference document metadata."""
        return await business_context_response()

    @app.put(
        "/api/v1/business-context",
        response_model=BusinessContextResponse,
    )
    async def save_business_context(
        body: BusinessProfileUpdate,
    ) -> BusinessContextResponse:
        """Save company details that future tasks should receive as context."""
        await repository.update_business_profile(
            body.business_name,
            body.profile_text,
        )
        return await business_context_response()

    @app.post(
        "/api/v1/business-context/documents",
        response_model=BusinessDocumentResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_business_document(
        file: UploadFile = File(...),
    ) -> BusinessDocumentResponse:
        """Store a supported file and its extracted text in the workspace DB."""
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        extension = Path(filename).suffix.lower()
        if not filename or len(filename) > 255:
            raise HTTPException(status_code=400, detail="Choose a valid filename under 256 characters.")
        if extension not in ALLOWED_BUSINESS_FILE_TYPES:
            raise HTTPException(
                status_code=415,
                detail="Supported file types are PDF, CSV, TXT, and Markdown.",
            )
        try:
            content = await file.read(MAX_BUSINESS_FILE_BYTES + 1)
        finally:
            await file.close()
        if not content:
            raise HTTPException(status_code=400, detail="The selected file is empty.")
        if len(content) > MAX_BUSINESS_FILE_BYTES:
            raise HTTPException(status_code=413, detail="Each file must be 5 MB or smaller.")

        document_count, total_bytes = await repository.business_document_usage()
        if document_count >= MAX_BUSINESS_DOCUMENTS:
            raise HTTPException(
                status_code=409,
                detail=f"This workspace can store up to {MAX_BUSINESS_DOCUMENTS} documents.",
            )
        if total_bytes + len(content) > MAX_BUSINESS_TOTAL_BYTES:
            raise HTTPException(
                status_code=413,
                detail="The workspace document limit is 25 MB total. Remove a file before adding more.",
            )
        try:
            extracted_text, was_truncated = await run_in_threadpool(
                _extract_document_text,
                extension,
                content,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail="The file could not be read. Try an unencrypted, text-based document.",
            ) from exc

        try:
            document = await repository.add_business_document(
                filename=filename,
                extension=extension,
                media_type=BUSINESS_FILE_MEDIA_TYPES[extension],
                size_bytes=len(content),
                raw_content=content,
                extracted_text=extracted_text,
                was_truncated=was_truncated,
            )
        except SupabaseStorageError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return BusinessDocumentResponse(
            id=document.id,
            filename=document.filename,
            extension=document.extension,
            size_bytes=document.size_bytes,
            extracted_characters=len(document.extracted_text),
            was_truncated=document.was_truncated,
            uploaded_at=document.uploaded_at,
        )

    @app.get("/api/v1/business-context/documents/{document_id}/download")
    async def download_business_document(document_id: str) -> Response:
        try:
            stored = await repository.download_business_document(document_id)
        except SupabaseStorageError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if stored is None:
            raise HTTPException(status_code=404, detail="Document not found.")
        document, content = stored
        return Response(
            content=content,
            media_type=document.media_type,
            headers={
                "Content-Disposition": (
                    "attachment; filename*=UTF-8''"
                    + quote(document.filename, safe="")
                )
            },
        )

    @app.delete("/api/v1/business-context/documents/{document_id}")
    async def delete_business_document(document_id: str) -> dict[str, Any]:
        deleted = await repository.delete_business_document(document_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Document not found.")
        return {"document_id": document_id, "deleted": True}

    @app.post(
        "/api/v1/tasks",
        response_model=TaskAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def submit_task(body: TaskSubmit) -> TaskAccepted:
        row = await repository.create(
            body.goal,
            body.priority,
            use_memory=body.use_memory,
        )
        return TaskAccepted(
            task_id=row.id,
            status=TaskStatus(row.status),
            message="Task saved and added to the worker queue.",
        )

    @app.get("/api/v1/tasks", response_model=list[TaskResponse])
    async def list_tasks(
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        task_status: TaskStatus | None = Query(default=None, alias="status"),
        q: str | None = Query(default=None, max_length=200),
    ) -> list[TaskResponse]:
        """Search and filter recent task runs."""
        rows = await repository.list(
            limit=limit,
            offset=offset,
            status=task_status,
            query=q.strip() if q else None,
        )
        return [TaskResponse.model_validate(row) for row in rows]

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(task_id: str) -> TaskResponse:
        row = await repository.get(task_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return TaskResponse.model_validate(row)

    @app.get(
        "/api/v1/tasks/{task_id}/events",
        response_model=list[TaskEventResponse],
    )
    async def get_task_events(task_id: str) -> list[TaskEventResponse]:
        """Return the append-only lifecycle timeline for one task."""
        if await repository.get(task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found")
        events = await repository.list_events(task_id)
        return [TaskEventResponse.model_validate(event) for event in events]

    @app.get("/api/v1/tasks/{task_id}/result")
    async def get_task_result(task_id: str) -> dict[str, object]:
        row = await repository.get(task_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if row.status != TaskStatus.COMPLETE.value or row.result is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Result is not ready; current status is {row.status}.",
            )
        return {"task_id": row.id, "result": row.result}

    @app.delete("/api/v1/tasks/{task_id}")
    async def cancel_task(task_id: str) -> dict[str, object]:
        row = await repository.get(task_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Task not found")
        cancelled = await repository.cancel(task_id)
        current = await repository.get(task_id)
        return {
            "task_id": task_id,
            "cancelled": cancelled,
            "status": current.status if current else None,
        }

    @app.post("/api/v1/tasks/{task_id}/retry", response_model=TaskResponse)
    async def retry_task(task_id: str) -> TaskResponse:
        retried = await repository.retry(task_id)
        if retried is not None:
            return TaskResponse.model_validate(retried)

        current = await repository.get(task_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Task not found")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed or cancelled tasks can be retried.",
        )

    @app.websocket("/ws/tasks/{task_id}")
    async def task_updates(websocket: WebSocket, task_id: str) -> None:
        """Push task snapshots until the task finishes or the client disconnects."""
        await websocket.accept()
        try:
            while True:
                row = await repository.get(task_id)
                if row is None:
                    await websocket.send_json({"error": "Task not found"})
                    await websocket.close(code=1008)
                    return

                task = TaskResponse.model_validate(row)
                await websocket.send_json(task.model_dump(mode="json"))
                if row.status in TERMINAL_TASK_STATUSES:
                    await websocket.close(code=1000)
                    return
                await asyncio.sleep(0.75)
        except WebSocketDisconnect:
            return

    return app


app = create_app()