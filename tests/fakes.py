"""Deterministic LLM used to test orchestration without network or credentials."""
import json
from backend.llm.base import LLMResponse
class FakeLLM:
    async def chat(self,*,model,messages,max_tokens=None,temperature=None):
        system=messages[0]['content'];user=messages[-1]['content']
        if 'enterprise planning controller' in system:
            content={'goal':'Analyze churn','subtasks':[{'id':'sales_analysis','agent_name':'Sales Agent','goal':'Analyze customer churn by plan and tenure.','dependencies':[]},{'id':'root_cause_analysis','agent_name':'Analysis Agent','goal':'Combine all specialist evidence into root causes.','dependencies':['sales_analysis']},{'id':'final_report','agent_name':'Report Agent','goal':'Create a prioritized final report.','dependencies':['root_cause_analysis']}]}
        elif 'Sales Agent' in system:
            context=json.loads(user)
            content={'action':'use_tool','tool':'crm_churn_data','arguments':{'period':'last_quarter'},'reason':'Need CRM evidence','answer':None,'confidence':.6} if not context['prior_tool_trace'] else {'action':'final','tool':None,'arguments':{},'reason':'Evidence sufficient','answer':'Monthly and new customers have the highest churn.','confidence':.85}
        elif 'Analysis Agent' in system:content={'action':'final','tool':None,'arguments':{},'reason':'Dependencies supplied','answer':'Root cause is weak onboarding for monthly customers.','confidence':.82}
        elif 'Report Agent' in system:content={'action':'final','tool':None,'arguments':{},'reason':'Analysis supplied','answer':'Prioritize onboarding improvements and track weekly churn.','confidence':.84}
        else:raise AssertionError(f'Unexpected prompt: {system[:100]}')
        return LLMResponse(content=json.dumps(content),model=model)
