"""Free deterministic LLM simulator for local development."""

import json
from typing import Any

from backend.llm.base import LLMResponse


class FakeLLMProvider:
    """Return predictable planning and agent decisions without an API call."""

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Inspect the role prompt and return matching structured JSON."""
        system_prompt = messages[0]["content"]
        user_content = messages[-1]["content"]

        if "enterprise planning controller" in system_prompt:
            content = self._create_plan(user_content)
        elif "Sales Agent" in system_prompt:
            content = self._sales_step(user_content)
        elif "Finance Agent" in system_prompt:
            content = self._finance_step(user_content)
        elif "Customer Support Agent" in system_prompt:
            content = self._support_step(user_content)
        elif "IT Agent" in system_prompt:
            content = self._it_step(user_content)
        elif "Analysis Agent" in system_prompt:
            content = self._analysis_step(user_content)
        elif "Report Agent" in system_prompt:
            content = self._report_step(user_content)
        else:
            raise ValueError("Fake LLM received an unknown role prompt")

        return LLMResponse(
            content=json.dumps(content),
            model="fake-local-development-model",
            prompt_tokens=0,
            completion_tokens=0,
        )

    @staticmethod
    def _create_plan(user_content: str) -> dict[str, Any]:
        """Create a fixed, valid multi-department churn plan."""
        goal = "Analyze customer churn and create an action report."
        marker = "BUSINESS GOAL:\n"
        if marker in user_content:
            goal = user_content.split(marker, 1)[1].split(
                "\n\nREGISTERED AGENTS:",
                1,
            )[0].strip()

        investigation_ids = [
            "sales_churn_analysis",
            "finance_churn_analysis",
            "support_churn_analysis",
            "it_churn_analysis",
        ]

        return {
            "goal": goal,
            "subtasks": [
                {
                    "id": "sales_churn_analysis",
                    "agent_name": "Sales Agent",
                    "goal": "Analyze churn by customer plan and tenure.",
                    "dependencies": [],
                },
                {
                    "id": "finance_churn_analysis",
                    "agent_name": "Finance Agent",
                    "goal": "Analyze failed payments, pricing, and refunds.",
                    "dependencies": [],
                },
                {
                    "id": "support_churn_analysis",
                    "agent_name": "Customer Support Agent",
                    "goal": "Analyze support delays and complaint themes.",
                    "dependencies": [],
                },
                {
                    "id": "it_churn_analysis",
                    "agent_name": "IT Agent",
                    "goal": "Analyze product errors and incidents.",
                    "dependencies": [],
                },
                {
                    "id": "root_cause_analysis",
                    "agent_name": "Analysis Agent",
                    "goal": "Combine department evidence into root causes.",
                    "dependencies": investigation_ids,
                },
                {
                    "id": "final_report",
                    "agent_name": "Report Agent",
                    "goal": "Create a prioritized churn action report.",
                    "dependencies": ["root_cause_analysis"],
                },
            ],
        }

    @staticmethod
    def _context(user_content: str) -> dict[str, Any]:
        """Parse the JSON context created by SpecialistAgent."""
        return json.loads(user_content)

    @staticmethod
    def _tool_action(tool: str, reason: str) -> dict[str, Any]:
        """Create a valid tool action."""
        return {
            "action": "use_tool",
            "tool": tool,
            "arguments": {"period": "last_quarter"},
            "reason": reason,
            "answer": None,
            "confidence": 0.65,
        }

    @staticmethod
    def _final(answer: str, confidence: float = 0.85) -> dict[str, Any]:
        """Create a valid final action."""
        return {
            "action": "final",
            "tool": None,
            "arguments": {},
            "reason": "The supplied evidence is sufficient.",
            "answer": answer,
            "confidence": confidence,
        }

    def _sales_step(self, user_content: str) -> dict[str, Any]:
        context = self._context(user_content)
        if not context["prior_tool_trace"]:
            return self._tool_action(
                "crm_churn_data",
                "CRM evidence is required.",
            )
        observation = context["prior_tool_trace"][-1]["observation"]
        return self._final(
            "Monthly-plan churn is "
            f"{observation['monthly_plan_churn'] * 100:.0f}% and churn "
            f"under 90 days is {observation['under_90_days_churn'] * 100:.0f}%."
        )

    def _finance_step(self, user_content: str) -> dict[str, Any]:
        context = self._context(user_content)
        if not context["prior_tool_trace"]:
            return self._tool_action(
                "billing_analysis",
                "Billing evidence is required.",
            )
        observation = context["prior_tool_trace"][-1]["observation"]
        return self._final(
            "Failed payments rose from "
            f"{observation['failed_payment_previous'] * 100:.1f}% to "
            f"{observation['failed_payment_current'] * 100:.1f}%, while "
            f"refunds rose {observation['refund_change'] * 100:.0f}%."
        )

    def _support_step(self, user_content: str) -> dict[str, Any]:
        context = self._context(user_content)
        if not context["prior_tool_trace"]:
            return self._tool_action(
                "support_ticket_analysis",
                "Support evidence is required.",
            )
        observation = context["prior_tool_trace"][-1]["observation"]
        return self._final(
            "Ticket volume rose "
            f"{observation['ticket_volume_change'] * 100:.0f}% and response "
            f"time worsened from {observation['response_hours_previous']} "
            f"to {observation['response_hours_current']} hours."
        )

    def _it_step(self, user_content: str) -> dict[str, Any]:
        context = self._context(user_content)
        if not context["prior_tool_trace"]:
            return self._tool_action(
                "product_telemetry",
                "Product telemetry is required.",
            )
        observation = context["prior_tool_trace"][-1]["observation"]
        return self._final(
            "Login errors rose from "
            f"{observation['login_error_previous'] * 100:.1f}% to "
            f"{observation['login_error_current'] * 100:.1f}%, affecting "
            f"{observation['affected_customers']} customers."
        )

    def _analysis_step(self, user_content: str) -> dict[str, Any]:
        context = self._context(user_content)
        results = context["dependency_results"]
        evidence = " ".join(
            result["answer"]
            for result in results.values()
            if result["success"]
        )
        return self._final(
            "Root causes include weak new-customer retention, payment "
            "friction, slower support, and mobile login failures. "
            f"Department evidence: {evidence}",
            0.84,
        )

    def _report_step(self, user_content: str) -> dict[str, Any]:
        context = self._context(user_content)
        analysis = next(iter(context["dependency_results"].values()))
        return self._final(
            "ROOT CAUSES\n"
            f"{analysis['answer']}\n\n"
            "PRIORITIZED ACTIONS\n"
            "1. Fix mobile login failures.\n"
            "2. Recover failed payments and clarify pricing.\n"
            "3. Restore support response times.\n"
            "4. Improve onboarding for new monthly customers.\n\n"
            "MEASUREMENT\n"
            "Track weekly churn by plan, tenure, payment status, and incident exposure.",
            0.86,
        )
