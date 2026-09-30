"""Smoke tests for the dashboard-facing API using an isolated SQLite database."""

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api import create_app
from backend.config import Settings
from backend.models import TaskStatus


@pytest.mark.asyncio
async def test_dashboard_api_task_lifecycle_and_static_frontend():
    app = create_app(Settings(database_url="sqlite+aiosqlite:///:memory:"))
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            home = await client.get("/")
            assert home.status_code == 200
            assert "Meta AgentX" in home.text

            agents = await client.get("/api/v1/agents")
            templates = await client.get("/api/v1/templates")
            assert agents.status_code == templates.status_code == 200
            assert len(agents.json()) == 9
            assert agents.json()[0]["name"] == "Manager Agent"
            assert any(agent["name"] == "Reviewer Agent" for agent in agents.json())
            assert templates.json()[0]["id"] == "invoice-audit"
            health = await client.get("/health")
            assert health.json()["worker"]["status"] == "offline"
            assert health.json()["database_kind"] == "local_sqlite"

            integrations = await client.get("/api/v1/integrations")
            assert integrations.status_code == 200
            integration_ids = {item["id"] for item in integrations.json()}
            assert {"documents", "hubspot", "stripe", "zendesk", "salesforce"}.issubset(integration_ids)
            assert (await client.post("/api/v1/integrations/documents/test")).json()["ok"] is True
            assert (await client.post("/api/v1/integrations/hubspot/test")).status_code == 409

            saved_profile = await client.put(
                "/api/v1/business-context",
                json={
                    "business_name": "Northwind Bikes",
                    "profile_text": "We sell commuter bikes to urban riders.",
                },
            )
            assert saved_profile.status_code == 200
            assert saved_profile.json()["business_name"] == "Northwind Bikes"

            uploaded = await client.post(
                "/api/v1/business-context/documents",
                files={
                    "file": (
                        "product-notes.txt",
                        b"Our main product is a low-maintenance city bike.",
                        "text/plain",
                    )
                },
            )
            assert uploaded.status_code == 201
            document_id = uploaded.json()["id"]
            context_response = await client.get("/api/v1/business-context")
            assert context_response.status_code == 200
            assert context_response.json()["documents"][0]["filename"] == "product-notes.txt"
            download = await client.get(
                f"/api/v1/business-context/documents/{document_id}/download"
            )
            assert download.content == b"Our main product is a low-maintenance city bike."

            created = await client.post(
                "/api/v1/tasks",
                json={
                    "goal": "Investigate why customer retention is changing this quarter.",
                    "priority": "high",
                },
            )
            assert created.status_code == 202
            task_id = created.json()["task_id"]
            assert created.json()["status"] == TaskStatus.QUEUED.value
            task_context = await app.state.repository.business_context_for_task(task_id)
            assert "Northwind Bikes" in task_context
            assert "low-maintenance city bike" in task_context

            events = await client.get(f"/api/v1/tasks/{task_id}/events")
            assert events.status_code == 200
            assert [event["event_type"] for event in events.json()] == ["queued"]

            listed = await client.get("/api/v1/tasks", params={"q": "retention"})
            assert [task["id"] for task in listed.json()] == [task_id]
            assert listed.json()[0]["goal"].startswith("Investigate")

            stats = await client.get("/api/v1/stats")
            assert stats.json()["total"] == 1
            assert stats.json()["queued"] == 1

            cancelled = await client.delete(f"/api/v1/tasks/{task_id}")
            assert cancelled.json()["cancelled"] is True
            assert cancelled.json()["status"] == TaskStatus.CANCELLED.value

            retry = await client.post(f"/api/v1/tasks/{task_id}/retry")
            assert retry.status_code == 200
            assert retry.json()["status"] == TaskStatus.QUEUED.value

            invalid_retry = await client.post(f"/api/v1/tasks/{task_id}/retry")
            assert invalid_retry.status_code == 409

            await app.state.repository.update(
                task_id,
                status=TaskStatus.COMPLETE,
                progress=100,
                stage="Complete",
                result={"final_report": "A useful test report."},
            )
            result = await client.get(f"/api/v1/tasks/{task_id}/result")
            assert result.status_code == 200
            assert result.json()["result"]["final_report"] == "A useful test report."

            invalid_goal = await client.post(
                "/api/v1/tasks",
                json={"goal": "short", "priority": "medium"},
            )
            assert invalid_goal.status_code == 422

            missing = await client.get("/api/v1/tasks/not-a-task")
            assert missing.status_code == 404

            deleted = await client.delete(
                f"/api/v1/business-context/documents/{document_id}"
            )
            assert deleted.status_code == 200
            assert deleted.json()["deleted"] is True
            assert "low-maintenance city bike" in await app.state.repository.business_context_for_task(task_id)

            invalid_upload = await client.post(
                "/api/v1/business-context/documents",
                files={"file": ("malware.exe", b"not allowed", "application/octet-stream")},
            )
            assert invalid_upload.status_code == 415