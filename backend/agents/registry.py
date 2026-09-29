"""Constructs the permission-scoped enterprise agent registry."""
from backend.agents.specialist import SpecialistAgent
from backend.config import Settings
from backend.llm.base import LLMProvider
from backend.tools.base import ToolRegistry
from backend.tools.enterprise import BillingTool,CRMTool,SupportTool,TelemetryTool

def build_agents(llm:LLMProvider,settings:Settings)->dict[str,SpecialistAgent]:
    """Create specialists; each receives only its approved tools."""
    definitions=[
      ('Sales Agent','Analyze CRM segments, customers, pipeline, and churn.',[CRMTool()],settings.hf_worker_model_id),
      ('Finance Agent','Analyze billing, payments, pricing, refunds, and financial impact.',[BillingTool()],settings.hf_worker_model_id),
      ('Customer Support Agent','Analyze support volume, response time, and complaint patterns.',[SupportTool()],settings.hf_worker_model_id),
      ('IT Agent','Analyze product reliability, incidents, errors, and usage.',[TelemetryTool()],settings.hf_worker_model_id),
      ('Analysis Agent','Combine dependency evidence into cross-domain root causes.',[],settings.hf_meta_model_id),
      ('Report Agent','Turn validated analysis into a concise prioritized final report.',[],settings.hf_report_model_id),
    ]
    return {name:SpecialistAgent(name,role,llm,settings,ToolRegistry(tools),model) for name,role,tools,model in definitions}
