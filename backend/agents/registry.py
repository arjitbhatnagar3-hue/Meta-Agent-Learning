"""Manager and specialist catalog with credential-gated, read-only tools."""

from backend.agents.specialist import SpecialistAgent
from backend.config import Settings
from backend.llm.base import LLMProvider
from backend.models import AgentInfo
from backend.tools.base import ToolRegistry
from backend.tools.integrations import HubSpotCRMTool, StripeBillingTool, ZendeskSupportTool


AGENT_CATALOG = [
    {
        "name": "Manager Agent",
        "category": "Orchestration",
        "description": "Decomposes the goal, assigns work, and builds the task dependency graph.",
        "responsibility": "Plan and delegate work; monitor dependencies and failures.",
        "tools": [],
    },
    {
        "name": "Document Agent",
        "category": "Documents",
        "description": "Extracts relevant facts, entities, dates, and figures from uploaded files.",
        "responsibility": "Find source passages and organize document facts.",
        "tools": [],
    },
    {
        "name": "Financial Agent",
        "category": "Finance",
        "description": "Analyzes financial amounts, trends, payment data, and calculations.",
        "responsibility": "Check financial evidence and show calculation assumptions.",
        "tools": [],
    },
    {
        "name": "CSV/Data Agent",
        "category": "Structured data",
        "description": "Analyzes tables, CSV exports, categories, and measurable trends.",
        "responsibility": "Summarize structured records without inventing missing rows.",
        "tools": [],
    },
    {
        "name": "Invoice Agent",
        "category": "Invoices",
        "description": "Extracts invoice totals, dates, parties, and payment status for comparison.",
        "responsibility": "Reconcile invoice fields and identify inconsistencies.",
        "tools": [],
    },
    {
        "name": "Anomaly Agent",
        "category": "Quality & risk",
        "description": "Looks for unusual values, duplicates, missing fields, and outliers.",
        "responsibility": "Flag anomalies as leads to verify, not as proven fraud.",
        "tools": [],
    },
    {
        "name": "Research Agent",
        "category": "Cross-reference",
        "description": "Cross-references supplied documents and any configured business sources.",
        "responsibility": "Compare corroborating and conflicting evidence across sources.",
        "tools": [],
    },
    {
        "name": "Reviewer Agent",
        "category": "Quality review",
        "description": "Checks whether findings are supported, calculations agree, and gaps are visible.",
        "responsibility": "Review source use, support, arithmetic, hallucination risk, and missing information.",
        "tools": [],
    },
    {
        "name": "Synthesis Agent",
        "category": "Final synthesis",
        "description": "Combines reviewed specialist findings into a clear, evidence-backed report.",
        "responsibility": "Summarize verified findings, uncertainty, and practical next steps.",
        "tools": [],
    },
]


def agent_catalog(settings: Settings) -> list[AgentInfo]:
    """Return role cards with only tools actually configured on the server."""
    tools_by_agent: dict[str, list[str]] = {
        "Financial Agent": [],
        "Research Agent": [],
    }
    if settings.stripe_secret_key.get_secret_value().strip():
        tools_by_agent["Financial Agent"].append("stripe_balance_summary")
    if settings.hubspot_access_token.get_secret_value().strip():
        tools_by_agent["Research Agent"].append("hubspot_company_sample")
    if all(
        value.strip()
        for value in (
            settings.zendesk_subdomain,
            settings.zendesk_email,
            settings.zendesk_api_token.get_secret_value(),
        )
    ):
        tools_by_agent["Research Agent"].append("zendesk_ticket_count")
    return [
        AgentInfo.model_validate(
            {**card, "tools": tools_by_agent.get(card["name"], card["tools"])}
        )
        for card in AGENT_CATALOG
    ]


def build_agents(
    llm: LLMProvider,
    settings: Settings,
) -> dict[str, SpecialistAgent]:
    """Create specialists; register real read-only tools only when configured."""
    financial_tools = []
    research_tools = []
    if settings.stripe_secret_key.get_secret_value().strip():
        financial_tools.append(StripeBillingTool(settings))
    if settings.hubspot_access_token.get_secret_value().strip():
        research_tools.append(HubSpotCRMTool(settings))
    if all(
        value.strip()
        for value in (
            settings.zendesk_subdomain,
            settings.zendesk_email,
            settings.zendesk_api_token.get_secret_value(),
        )
    ):
        research_tools.append(ZendeskSupportTool(settings))

    definitions = [
        (
            "Document Agent",
            "Extract only relevant facts from the supplied business profile and uploaded documents. Cite short exact excerpts and filenames. State when the files do not contain enough information.",
            [],
            settings.hf_worker_model_id,
        ),
        (
            "Financial Agent",
            "Analyze financial values and calculations using only supplied documents or configured read-only billing tools. Show units, formulas, assumptions, and gaps.",
            financial_tools,
            settings.hf_worker_model_id,
        ),
        (
            "CSV/Data Agent",
            "Analyze supplied CSV and structured records. State row/column coverage, distinguish totals from samples, and cite the file and relevant fields.",
            [],
            settings.hf_worker_model_id,
        ),
        (
            "Invoice Agent",
            "Extract and compare invoice numbers, dates, vendors, line items, totals, tax, currency, and payment status from supplied files. Do not infer unreadable fields.",
            [],
            settings.hf_worker_model_id,
        ),
        (
            "Anomaly Agent",
            "Flag unusual values, duplicate records, missing data, or outliers in supplied sources. Treat anomalies as items to verify, not proof of misconduct.",
            [],
            settings.hf_worker_model_id,
        ),
        (
            "Research Agent",
            "Cross-reference supplied documents and configured read-only CRM/support sources. Identify corroboration, contradictions, dates, and missing source coverage.",
            research_tools,
            settings.hf_worker_model_id,
        ),
        (
            "Reviewer Agent",
            "Audit every specialist result. Check whether supplied documents were used, claims are supported, calculations are consistent, hallucinations are possible, and important information is missing. Return the structured review checklist.",
            [],
            settings.hf_worker_model_id,
        ),
        (
            "Synthesis Agent",
            "Combine reviewed specialist findings. Include only supported claims, cite evidence, explain uncertainty, and give practical next steps. Never fill gaps with invented company data.",
            [],
            settings.hf_report_model_id,
        ),
    ]
    return {
        name: SpecialistAgent(
            name,
            role,
            llm,
            settings,
            ToolRegistry(tools),
            model,
        )
        for name, role, tools, model in definitions
    }