"""Read-only live-provider tools enabled only by server-side credentials."""

from typing import Any

from pydantic import BaseModel

from backend.config import Settings
from backend.integrations import hubspot_companies, stripe_balance, zendesk_ticket_count
from backend.tools.base import Tool


class NoArguments(BaseModel):
    """Schema for a safe read-only lookup with no user-controlled URL."""


class HubSpotCRMTool(Tool):
    name = "hubspot_company_sample"
    description = "Read a small sample of company records from the configured HubSpot account."
    arguments_schema = NoArguments

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run(self, arguments: NoArguments) -> dict[str, Any]:
        payload = await hubspot_companies(self.settings)
        results = payload.get("results", [])
        companies = []
        for row in results[:10]:
            properties = row.get("properties", {}) if isinstance(row, dict) else {}
            name = properties.get("name") or properties.get("domain")
            if name:
                companies.append(str(name)[:160])
        return {
            "source": "HubSpot live read-only connector",
            "sampled_records": len(results[:10]),
            "company_names": companies,
        }


class StripeBillingTool(Tool):
    name = "stripe_balance_summary"
    description = "Read the available and pending Stripe balance by currency."
    arguments_schema = NoArguments

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run(self, arguments: NoArguments) -> dict[str, Any]:
        payload = await stripe_balance(self.settings)

        def clean(items: Any) -> list[dict[str, Any]]:
            if not isinstance(items, list):
                return []
            return [
                {"currency": item.get("currency"), "amount_minor_units": item.get("amount")}
                for item in items[:20]
                if isinstance(item, dict)
            ]

        return {
            "source": "Stripe live read-only connector",
            "available_balance": clean(payload.get("available")),
            "pending_balance": clean(payload.get("pending")),
        }


class ZendeskSupportTool(Tool):
    name = "zendesk_ticket_count"
    description = "Read the Zendesk ticket count without retrieving ticket messages."
    arguments_schema = NoArguments

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run(self, arguments: NoArguments) -> dict[str, Any]:
        payload = await zendesk_ticket_count(self.settings)
        count = payload.get("count", {}).get("value") if isinstance(payload.get("count"), dict) else None
        return {
            "source": "Zendesk live read-only connector",
            "ticket_count": count,
        }