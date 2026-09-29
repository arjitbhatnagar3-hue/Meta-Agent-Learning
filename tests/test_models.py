import pytest
from backend.models import ExecutionPlan

def test_plan_rejects_cycle():
    with pytest.raises(ValueError,match='cycle'):
        ExecutionPlan.model_validate({'goal':'x','subtasks':[{'id':'task_a','agent_name':'A','goal':'first task','dependencies':['task_b']},{'id':'task_b','agent_name':'B','goal':'second task','dependencies':['task_a']}]})
