import pytest
from backend.agents.meta import MetaAgent
from backend.agents.registry import build_agents
from backend.config import Settings
from backend.workflow import WorkflowEngine
from backend.models import TaskStatus
from tests.fakes import FakeLLM
@pytest.mark.asyncio
async def test_llm_plan_tools_dependencies_and_report():
    settings=Settings(hf_token='test',max_agent_iterations=3);llm=FakeLLM();agents=build_agents(llm,settings)
    plan=await MetaAgent(llm,settings,set(agents)).create_plan('Analyze why customer churn increased')
    updates=[]
    async def progress(status,percent,stage):updates.append((status,percent,stage))
    result=await WorkflowEngine(agents).execute(plan,progress)
    assert result['success'] is True
    assert 'onboarding' in result['final_report'].lower()
    assert result['execution_waves']==[['sales_analysis'],['root_cause_analysis'],['final_report']]
    assert result['agent_results']['sales_analysis']['trace'][0]['tool']=='crm_churn_data'
    assert any(item[0]==TaskStatus.EXECUTING for item in updates)
