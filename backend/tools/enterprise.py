"""Safe teaching adapters standing in for enterprise systems."""
import asyncio
from typing import Any
from pydantic import BaseModel,Field
from backend.tools.base import Tool
class PeriodArgs(BaseModel):period:str=Field(default='last_quarter',pattern=r'^[a-z0-9_-]{2,40}$')

class CRMTool(Tool):
    name='crm_churn_data';description='Returns churn rates by plan and customer tenure.';arguments_schema=PeriodArgs
    async def run(self,arguments:PeriodArgs)->dict[str,Any]:
        await asyncio.sleep(.05);return {'period':arguments.period,'monthly_plan_churn':.19,'annual_plan_churn':.06,'under_90_days_churn':.24,'over_one_year_churn':.05,'source':'teaching CRM'}
class BillingTool(Tool):
    name='billing_analysis';description='Returns failed payments, pricing changes, and refund patterns.';arguments_schema=PeriodArgs
    async def run(self,arguments:PeriodArgs)->dict[str,Any]:
        await asyncio.sleep(.05);return {'period':arguments.period,'failed_payment_previous':.021,'failed_payment_current':.047,'price_increase':.08,'refund_change':.22,'source':'teaching billing system'}
class SupportTool(Tool):
    name='support_ticket_analysis';description='Returns support volume, response times, and complaint themes.';arguments_schema=PeriodArgs
    async def run(self,arguments:PeriodArgs)->dict[str,Any]:
        await asyncio.sleep(.05);return {'period':arguments.period,'ticket_volume_change':.31,'response_hours_previous':4.2,'response_hours_current':9.8,'top_themes':['login failures','billing confusion','slow response'],'source':'teaching support desk'}
class TelemetryTool(Tool):
    name='product_telemetry';description='Returns product errors, incidents, affected customers, and usage changes.';arguments_schema=PeriodArgs
    async def run(self,arguments:PeriodArgs)->dict[str,Any]:
        await asyncio.sleep(.05);return {'period':arguments.period,'login_error_previous':.012,'login_error_current':.083,'critical_incidents':3,'affected_customers':320,'weekly_usage_change':-.09,'source':'teaching telemetry'}
