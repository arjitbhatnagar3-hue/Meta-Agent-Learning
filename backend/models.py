"""Pydantic models for tasks, plans, agent actions, and API responses."""
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class TaskStatus(str, Enum):
    QUEUED='queued'; PLANNING='planning'; EXECUTING='executing'; AGGREGATING='aggregating'; COMPLETE='complete'; FAILED='failed'; CANCELLED='cancelled'

class TaskSubmit(BaseModel):
    goal: str = Field(min_length=10, max_length=20000)
    priority: Literal['low','medium','high','critical']='medium'

class SubTask(BaseModel):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{2,63}$')
    agent_name: str
    goal: str = Field(min_length=5, max_length=5000)
    dependencies: list[str] = Field(default_factory=list)

class ExecutionPlan(BaseModel):
    goal: str
    subtasks: list[SubTask] = Field(min_length=1, max_length=20)
    @model_validator(mode='after')
    def validate_graph(self):
        ids=[t.id for t in self.subtasks]
        if len(ids)!=len(set(ids)): raise ValueError('Subtask IDs must be unique')
        known=set(ids)
        for task in self.subtasks:
            unknown=set(task.dependencies)-known
            if unknown: raise ValueError(f'{task.id} has unknown dependencies: {sorted(unknown)}')
            if task.id in task.dependencies: raise ValueError(f'{task.id} cannot depend on itself')
        remaining={t.id:set(t.dependencies) for t in self.subtasks}; completed=set()
        while remaining:
            ready={tid for tid,deps in remaining.items() if deps<=completed}
            if not ready: raise ValueError('Dependency cycle detected')
            completed|=ready
            for tid in ready: remaining.pop(tid)
        return self

class AgentAction(BaseModel):
    action: Literal['use_tool','final']
    tool: str|None=None
    arguments: dict[str,Any]=Field(default_factory=dict)
    reason: str=Field(min_length=1,max_length=500)
    answer: str|None=None
    confidence: float=Field(default=.75,ge=0,le=1)
    @model_validator(mode='after')
    def validate_action(self):
        if self.action=='use_tool' and not self.tool: raise ValueError('Tool action requires tool name')
        if self.action=='final' and not self.answer: raise ValueError('Final action requires answer')
        return self

class AgentResult(BaseModel):
    task_id: str; agent_name: str; success: bool; answer: str; confidence: float=Field(ge=0,le=1); trace:list[dict[str,Any]]=Field(default_factory=list); error:str|None=None

class TaskResponse(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id:str; goal:str; priority:str; status:TaskStatus; progress:int; current_stage:str; plan:dict[str,Any]|None; result:dict[str,Any]|None; error:str|None; attempts:int; worker_id:str|None; created_at:datetime; updated_at:datetime
