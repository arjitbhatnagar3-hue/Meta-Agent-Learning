"""Read-only business integration catalog and server-side connection probes."""

from __future__ import annotations

from typing import Any

import httpx

from backend.config import Settings
from backend.models import IntegrationInfo


class IntegrationNotConfigured(ValueError):
    """Raised when a requested provider has no server-side credentials."""


class IntegrationRequestError(RuntimeError):
    """Safe provider error which does not include response bodies or secrets."""


CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "documents",
        "name": "PDF / CSV / business documents",
        "category": "Files",
        "description": "Use uploaded reports, invoices, exports, and reference files.",
        "connection_method": "Upload files in Business context; no external account required.",
        "environment_variables": [],
        "configured": True,
        "available": True,
    },
    {
        "id": "hubspot",
        "name": "HubSpot",
        "category": "CRM",
        "description": "Read company records through a HubSpot private-app token.",
        "connection_method": "Create a read-only private app token and set it on the API and worker servers.",
        "environment_variables": ["HUBSPOT_ACCESS_TOKEN"],
        "available": True,
    },
    {
        "id": "stripe",
        "name": "Stripe",
        "category": "Billing",
        "description": "Read available and pending account balances by currency.",
        "connection_method": "Set a restricted, read-only Stripe key on the API and worker servers.",
        "environment_variables": ["STRIPE_SECRET_KEY"],
        "available": True,
    },
    {
        "id": "zendesk",
        "name": "Zendesk",
        "category": "Support",
        "description": "Read ticket counts from a Zendesk account.",
        "connection_method": "Set the subdomain, agent email, and API token on the API and worker servers.",
        "environment_variables": [
            "ZENDESK_SUBDOMAIN",
            "ZENDESK_EMAIL",
            "ZENDESK_API_TOKEN",
        ],
        "available": True,
    },
    {
        "id": "salesforce",
        "name": "Salesforce",
        "category": "CRM",
        "description": "Provider option for a future OAuth-based Salesforce connector.",
        "connection_method": "OAuth connector adapter is not included in this demo build.",
        "environment_variables": [],
        "available": False,
    },
    {
        "id": "zoho-crm",
        "name": "Zoho CRM",
        "category": "CRM",
        "description": "Provider option for a future Zoho CRM API connector.",
        "connection_method": "Provider adapter is not included in this demo build.",
        "environment_variables": [],
        "available": False,
    },
    {
        "id": "razorpay",
        "name": "Razorpay",
        "category": "Billing",
        "description": "Provider option for a future Razorpay API connector.",
        "connection_method": "Provider adapter is not included in this demo build.",
        "environment_variables": [],
        "available": False,
    },
    {
        "id": "quickbooks",
        "name": "QuickBooks Online",
        "category": "Billing",
        "description": "Provider option for a future QuickBooks OAuth connector.",
        "connection_method": "OAuth connector adapter is not included in this demo build.",
        "environment_variables": [],
        "available": False,
    },
    {
        "id": "freshdesk",
        "name": "Freshdesk",
        "category": "Support",
        "description": "Provider option for a future Freshdesk API connector.",
        "connection_method": "Provider adapter is not included in this demo build.",
        "environment_variables": [],
        "available": False,
    },
    {
        "id": "posthog",
        "name": "PostHog",
        "category": "Product analytics",
        "description": "Provider option for a future PostHog analytics connector.",
        "connection_method": "Provider adapter is not included in this demo build.",
        "environment_variables": [],
        "available": False,
    },
)


def _configured(provider: str, settings: Settings) -> bool:
    if provider == "documents":
        return True
    if provider == "hubspot":
        return bool(settings.hubspot_access_token.get_secret_value().strip())
    if provider == "stripe":
        return bool(settings.stripe_secret_key.get_secret_value().strip())
    if provider == "zendesk":
        return all(
            value.strip()
            for value in (
                settings.zendesk_subdomain,
                settings.zendesk_email,
                settings.zendesk_api_token.get_secret_value(),
            )
        )
    return False


def list_integrations(settings: Settings) -> list[IntegrationInfo]:
    """Return provider choices and safe connection status, never secret values."""
    results: list[IntegrationInfo] = []
    for entry in CATALOG:
        provider = entry["id"]
        is_configured = _configured(provider, settings)
        if not entry["available"]:
            state = "coming_soon"
        elif provider == "documents":
            state = "ready"
        elif is_configured:
            state = "configured"
        else:
            state = "not_configured"
        results.append(
            IntegrationInfo(
                id=provider,
                name=entry["name"],
                category=entry["category"],
                description=entry["description"],
                status=state,
                connection_method=entry["connection_method"],
                environment_variables=entry["environment_variables"],
                read_only=True,
            )
        )
    return results


async def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    auth: httpx.BasicAuth | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                auth=auth,
                params=params,
            )
            if not response.is_success:
                raise IntegrationRequestError(
                    f"Provider returned HTTP {response.status_code}; check its "
                    "read-only token and account permissions."
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise IntegrationRequestError("Provider returned an unreadable response.") from exc
            if not isinstance(payload, dict):
                raise IntegrationRequestError("Provider returned an unexpected response format.")
            return payload
    except IntegrationRequestError:
        raise
    except httpx.HTTPError as exc:
        raise IntegrationRequestError(
            "Could not reach the provider. Check the server connection and provider settings."
        ) from exc


def _require_configured(provider: str, settings: Settings) -> None:
    if not _configured(provider, settings):
        raise IntegrationNotConfigured(
            f"{provider.capitalize()} is not configured on the server."
        )


async def hubspot_companies(settings: Settings) -> dict[str, Any]:
    """Return a small, read-only sample of company names from HubSpot."""
    _require_configured("hubspot", settings)
    token = settings.hubspot_access_token.get_secret_value()
    return await _request_json(
        "GET",
        "https://api.hubapi.com/crm/v3/objects/companies",
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": 10, "properties": "name,domain"},
    )


async def stripe_balance(settings: Settings) -> dict[str, Any]:
    """Read the Stripe balance using the configured restricted secret key."""
    _require_configured("stripe", settings)
    token = settings.stripe_secret_key.get_secret_value()
    return await _request_json(
        "GET",
        "https://api.stripe.com/v1/balance",
        headers={"Authorization": f"Bearer {token}"},
    )


async def zendesk_ticket_count(settings: Settings) -> dict[str, Any]:
    """Read the total ticket count without fetching ticket contents."""
    _require_configured("zendesk", settings)
    subdomain = settings.zendesk_subdomain.strip().removesuffix(".zendesk.com")
    email = settings.zendesk_email.strip()
    token = settings.zendesk_api_token.get_secret_value()
    return await _request_json(
        "GET",
        f"https://{subdomain}.zendesk.com/api/v2/tickets/count.json",
        auth=httpx.BasicAuth(f"{email}/token", token),
    )


async def test_integration(provider: str, settings: Settings) -> dict[str, Any]:
    """Make a low-impact read-only connection test for a supported provider."""
    if provider == "documents":
        return {"ok": True, "message": "Document uploads are available in Business context."}
    if provider == "hubspot":
        payload = await hubspot_companies(settings)
        count = len(payload.get("results", []))
        return {"ok": True, "message": f"HubSpot responded successfully ({count} company records sampled)."}
    if provider == "stripe":
        payload = await stripe_balance(settings)
        currencies = sorted({item.get("currency", "").upper() for item in payload.get("available", []) if item.get("currency")})
        suffix = f" Available balance currencies: {', '.join(currencies)}." if currencies else ""
        return {"ok": True, "message": "Stripe responded successfully." + suffix}
    if provider == "zendesk":
        payload = await zendesk_ticket_count(settings)
        count = payload.get("count", {}).get("value", "unknown")
        return {"ok": True, "message": f"Zendesk responded successfully ({count} tickets)."}
    if provider not in {entry["id"] for entry in CATALOG}:
        raise KeyError(provider)
    raise IntegrationNotConfigured(
        f"{provider.capitalize()} is listed as an option, but its connector is not included in this build."
    )