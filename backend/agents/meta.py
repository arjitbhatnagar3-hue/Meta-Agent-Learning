"""Meta Agent planning and plan validation."""
import json
from backend.config import Settings
from backend.llm.base import LLMProvider
from backend.llm.json_output import parse_model_json
from backend.models import ExecutionPlan

class MetaAgent:
    """Uses the large Qwen model to propose a validated dependency plan."""
    def __init__(self,llm:LLMProvider,settings:Settings,registered_agents:set[str]):self.llm=llm;self.settings=settings;self.registered_agents=registered_agents
    async def create_plan(self,goal:str)->ExecutionPlan:
        system='''You are Meta Agent, an enterprise planning controller. Decompose the user's goal into a small dependency-aware plan. Use only registered agent names. Independent domain investigations should have no dependencies. Analysis Agent must depend on domain investigations. Report Agent must depend on Analysis Agent and be the final task. Return JSON only with shape {"goal":string,"subtasks":[{"id":snake_case,"agent_name":string,"goal":string,"dependencies":[ids]}]}. Do not include markdown.'''
        user=f'BUSINESS GOAL:\n{goal}\n\nREGISTERED AGENTS:\n{json.dumps(sorted(self.registered_agents))}'
        messages=[{'role':'system','content':system},{'role':'user','content':user}]
        response=await self.llm.chat(model=self.settings.hf_meta_model_id,messages=messages)
        try:plan=parse_model_json(response.content,ExecutionPlan)
        except ValueError as first_error:
            repair=messages+[{'role':'assistant','content':response.content},{'role':'user','content':f'Your output was invalid: {first_error}. Return corrected JSON only.'}]
            fixed=await self.llm.chat(model=self.settings.hf_meta_model_id,messages=repair)
            plan=parse_model_json(fixed.content,ExecutionPlan)
        unknown={task.agent_name for task in plan.subtasks}-self.registered_agents
        if unknown:raise ValueError(f'Plan selected unregistered agents: {sorted(unknown)}')
        report_tasks=[task for task in plan.subtasks if task.agent_name=='Report Agent']
        if len(report_tasks)!=1:raise ValueError('Plan must contain exactly one Report Agent task')
        report_id=report_tasks[0].id
        if any(report_id in task.dependencies for task in plan.subtasks):raise ValueError('Report task must be terminal')
        return plan
