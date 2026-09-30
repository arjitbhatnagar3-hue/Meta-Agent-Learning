import pytest
from backend.models import ExecutionPlan

def test_plan_rejects_cycle():
    with pytest.raises(ValueError,match='cycle'):
        ExecutionPlan.model_validate({'goal':'x','subtasks':[{'id':'task_a','agent_name':'A','goal':'first task','dependencies':['task_b']},{'id':'task_b','agent_name':'B','goal':'second task','dependencies':['task_a']}]})
def test_postgres_urls_are_adapted_for_async_sqlalchemy():
    from backend.db import normalize_async_database_url

    url = normalize_async_database_url("postgresql://user:secret@db.example.test/postgres?sslmode=require")
    assert url.startswith("postgresql+asyncpg://")
    assert "ssl=require" in url