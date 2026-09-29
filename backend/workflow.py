"""Dependency-aware parallel execution engine."""
import asyncio
from collections.abc import Awaitable,Callable
from backend.models import AgentResult,ExecutionPlan,TaskStatus
from backend.agents.specialist import SpecialistAgent
ProgressCallback=Callable[[TaskStatus,int,str],Awaitable[None]]

class WorkflowEngine:
    def __init__(self,agents:dict[str,SpecialistAgent]):self.agents=agents
    @staticmethod
    def waves(plan:ExecutionPlan)->list[list[str]]:
        remaining={task.id:set(task.dependencies) for task in plan.subtasks};completed=set();waves=[]
        while remaining:
            ready=sorted(tid for tid,deps in remaining.items() if deps<=completed)
            if not ready:raise ValueError('Dependency cycle detected')
            waves.append(ready);completed.update(ready)
            for tid in ready:remaining.pop(tid)
        return waves
    async def execute(self,plan:ExecutionPlan,progress:ProgressCallback)->dict:
        by_id={task.id:task for task in plan.subtasks};results:dict[str,AgentResult]={};waves=self.waves(plan);total=len(plan.subtasks);finished=0
        for index,wave in enumerate(waves,1):
            await progress(TaskStatus.EXECUTING,15+int(65*finished/max(total,1)),f'Executing workflow wave {index}/{len(waves)}')
            jobs=[];ids=[]
            for task_id in wave:
                task=by_id[task_id];deps={dep:results[dep] for dep in task.dependencies}
                failed=[dep for dep,value in deps.items() if not value.success]
                if failed:
                    results[task_id]=AgentResult(task_id=task_id,agent_name=task.agent_name,success=False,answer=f'Skipped due to failed dependencies: {failed}',confidence=0,error='dependency_failure');finished+=1;continue
                jobs.append(asyncio.create_task(self.agents[task.agent_name].run(task,deps)));ids.append(task_id)
            raw=await asyncio.gather(*jobs,return_exceptions=True)
            for task_id,value in zip(ids,raw):
                task=by_id[task_id]
                if isinstance(value,BaseException):results[task_id]=AgentResult(task_id=task_id,agent_name=task.agent_name,success=False,answer='Agent execution failed',confidence=0,error=str(value))
                else:results[task_id]=value
                finished+=1
        report_task=next(task for task in plan.subtasks if task.agent_name=='Report Agent')
        report=results[report_task.id]
        return {'plan':plan.model_dump(mode='json'),'execution_waves':waves,'agent_results':{key:value.model_dump(mode='json') for key,value in results.items()},'final_report':report.answer,'confidence':report.confidence,'success':report.success}
