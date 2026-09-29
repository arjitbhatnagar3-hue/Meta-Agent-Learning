"""Persistent task repository with atomic worker claiming."""
from datetime import datetime,timedelta,timezone
from typing import Any
from uuid import uuid4
from sqlalchemy import select,update
from backend.db import Database,TaskRow
from backend.models import TaskStatus

class TaskRepository:
    def __init__(self,db:Database): self.db=db
    async def create(self,goal:str,priority:str)->TaskRow:
        now=datetime.now(timezone.utc); row=TaskRow(id=str(uuid4()),goal=goal,priority=priority,status=TaskStatus.QUEUED.value,progress=0,current_stage='Waiting for worker',attempts=0,created_at=now,updated_at=now)
        async with self.db.sessions() as session:
            session.add(row); await session.commit(); await session.refresh(row); return row
    async def get(self,task_id:str)->TaskRow|None:
        async with self.db.sessions() as session:return await session.get(TaskRow,task_id)
    async def list(self,limit:int=100)->list[TaskRow]:
        async with self.db.sessions() as session:return list((await session.execute(select(TaskRow).order_by(TaskRow.created_at.desc()).limit(limit))).scalars())
    async def claim_next(self,worker_id:str,max_attempts:int)->TaskRow|None:
        for _ in range(5):
            async with self.db.sessions() as session:
                candidate=await session.scalar(select(TaskRow.id).where(TaskRow.status==TaskStatus.QUEUED.value,TaskRow.attempts<max_attempts).order_by(TaskRow.created_at).limit(1))
                if candidate is None:return None
                now=datetime.now(timezone.utc); changed=await session.execute(update(TaskRow).where(TaskRow.id==candidate,TaskRow.status==TaskStatus.QUEUED.value).values(status=TaskStatus.PLANNING.value,progress=5,current_stage='Claimed by worker',worker_id=worker_id,locked_at=now,attempts=TaskRow.attempts+1,updated_at=now));await session.commit()
                if changed.rowcount==1:return await self.get(candidate)
        return None
    async def update(self,task_id:str,*,status:TaskStatus|None=None,progress:int|None=None,stage:str|None=None,plan:dict|None=None,result:dict|None=None,error:str|None=None)->None:
        values:dict[str,Any]={'updated_at':datetime.now(timezone.utc)}
        if status is not None:values['status']=status.value
        if progress is not None:values['progress']=progress
        if stage is not None:values['current_stage']=stage
        if plan is not None:values['plan']=plan
        if result is not None:values['result']=result
        if error is not None:values['error']=error
        if status in {TaskStatus.PLANNING,TaskStatus.EXECUTING,TaskStatus.AGGREGATING}:values['locked_at']=datetime.now(timezone.utc)
        if status in {TaskStatus.COMPLETE,TaskStatus.FAILED,TaskStatus.CANCELLED}:values|={'locked_at':None,'worker_id':None}
        async with self.db.sessions() as session:
            changed=await session.execute(update(TaskRow).where(TaskRow.id==task_id).values(**values));await session.commit()
            if changed.rowcount!=1:raise KeyError(task_id)
    async def cancel(self,task_id:str)->bool:
        async with self.db.sessions() as session:
            changed=await session.execute(update(TaskRow).where(TaskRow.id==task_id,TaskRow.status.not_in([TaskStatus.COMPLETE.value,TaskStatus.FAILED.value,TaskStatus.CANCELLED.value])).values(status=TaskStatus.CANCELLED.value,current_stage='Cancelled',worker_id=None,locked_at=None,updated_at=datetime.now(timezone.utc)));await session.commit();return changed.rowcount==1
    async def recover_stale(self,lease_seconds:int,max_attempts:int)->int:
        cutoff=datetime.now(timezone.utc)-timedelta(seconds=lease_seconds)
        async with self.db.sessions() as session:
            requeued=await session.execute(update(TaskRow).where(TaskRow.status.in_([TaskStatus.PLANNING.value,TaskStatus.EXECUTING.value,TaskStatus.AGGREGATING.value]),TaskRow.locked_at<cutoff,TaskRow.attempts<max_attempts).values(status=TaskStatus.QUEUED.value,progress=0,current_stage='Recovered after worker lease expired',worker_id=None,locked_at=None,updated_at=datetime.now(timezone.utc)))
            failed=await session.execute(update(TaskRow).where(TaskRow.status.in_([TaskStatus.PLANNING.value,TaskStatus.EXECUTING.value,TaskStatus.AGGREGATING.value]),TaskRow.locked_at<cutoff,TaskRow.attempts>=max_attempts).values(status=TaskStatus.FAILED.value,current_stage='Retry limit exceeded',error='Worker lease expired repeatedly',worker_id=None,locked_at=None,updated_at=datetime.now(timezone.utc)));await session.commit();return int(requeued.rowcount+failed.rowcount)
