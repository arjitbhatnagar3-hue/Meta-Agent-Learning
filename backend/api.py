"""FastAPI communication layer; LLM execution remains in the worker."""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from fastapi import FastAPI,HTTPException,WebSocket,WebSocketDisconnect,status
from fastapi.middleware.cors import CORSMiddleware
from backend.config import get_settings
from backend.db import Database
from backend.models import TaskResponse,TaskStatus,TaskSubmit
from backend.repository import TaskRepository
settings=get_settings();db=Database(settings.database_url);repo=TaskRepository(db)
TERMINAL={TaskStatus.COMPLETE.value,TaskStatus.FAILED.value,TaskStatus.CANCELLED.value}
@asynccontextmanager
async def lifespan(app:FastAPI)->AsyncIterator[None]:await db.create_schema();yield;await db.close()
app=FastAPI(title='Meta AgentX',version='0.1.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173'],allow_methods=['*'],allow_headers=['*'])
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "llm_mode": settings.llm_mode,
        "llm_provider": (
            "fake-local-development"
            if settings.llm_mode == "fake"
            else "huggingface-inference-providers"
        ),
        "model": (
            "fake-local-development-model"
            if settings.llm_mode == "fake"
            else settings.hf_meta_model_id
        ),
    }
@app.post('/api/v1/tasks',status_code=status.HTTP_202_ACCEPTED)
async def submit(body:TaskSubmit):
    row=await repo.create(body.goal,body.priority);return {'task_id':row.id,'status':row.status,'message':'Task persisted and queued for a worker'}
@app.get('/api/v1/tasks',response_model=list[TaskResponse])
async def list_tasks():return [TaskResponse.model_validate(row) for row in await repo.list()]
@app.get('/api/v1/tasks/{task_id}',response_model=TaskResponse)
async def get_task(task_id:str):
    row=await repo.get(task_id)
    if row is None:raise HTTPException(404,'Task not found')
    return TaskResponse.model_validate(row)
@app.get('/api/v1/tasks/{task_id}/result')
async def result(task_id:str):
    task=await get_task(task_id)
    if task.status!=TaskStatus.COMPLETE or task.result is None:raise HTTPException(409,f'Result not ready; status={task.status.value}')
    return {'task_id':task_id,'result':task.result}
@app.delete('/api/v1/tasks/{task_id}')
async def cancel(task_id:str):
    if await repo.get(task_id) is None:raise HTTPException(404,'Task not found')
    return {'cancelled':await repo.cancel(task_id)}
@app.websocket('/ws/tasks/{task_id}')
async def task_updates(ws:WebSocket,task_id:str):
    await ws.accept()
    try:
        while True:
            row=await repo.get(task_id)
            if row is None:await ws.send_json({'error':'Task not found'});await ws.close(1008);return
            await ws.send_json(TaskResponse.model_validate(row).model_dump(mode='json'))
            if row.status in TERMINAL:await ws.close(1000);return
            await asyncio.sleep(.5)
    except WebSocketDisconnect:return
