"""Deterministic local simulator which never invents business metrics."""

import json
import re
from typing import Any

from backend.llm.base import LLMResponse


INVESTIGATORS = [
    ("document_review", "Document Agent", "Extract relevant facts and exact source passages from uploaded documents."),
    ("financial_review", "Financial Agent", "Review financial amounts, calculations, and assumptions in supplied material."),
    ("csv_review", "CSV/Data Agent", "Review structured rows, fields, totals, and trends in supplied files."),
    ("invoice_review", "Invoice Agent", "Compare invoice identifiers, dates, totals, tax, currency, and payment details."),
    ("anomaly_review", "Anomaly Agent", "Look for unusual values, duplicates, missing fields, and outliers."),
    ("research_review", "Research Agent", "Cross-reference evidence across documents and configured integrations."),
]


class FakeLLMProvider:
    """Return predictable sample behavior without claiming live company facts."""

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        system_prompt = messages[0]["content"]
        user_content = messages[-1]["content"]
        if "You are the Manager Agent" in system_prompt:
            content = self._create_plan(user_content)
        else:
            content = self._agent_response(system_prompt, user_content)
        return LLMResponse(
            content=json.dumps(content),
            model="fake-local-development-model",
            prompt_tokens=0,
            completion_tokens=0,
        )

    @staticmethod
    def _create_plan(user_content: str) -> dict[str, Any]:
        marker = "BUSINESS GOAL:\n"
        goal = "Review supplied documents and report supported findings."
        if marker in user_content:
            goal = user_content.split(marker, 1)[1].split(
                "\n\nREGISTERED SPECIALISTS:", 1
            )[0].strip()
        tasks = [
            {
                "id": task_id,
                "agent_name": agent,
                "goal": task_goal,
                "dependencies": [],
            }
            for task_id, agent, task_goal in INVESTIGATORS
        ]
        reviewer_id = "review_evidence"
        synthesis_id = "synthesize_report"
        investigation_ids = [task_id for task_id, _, _ in INVESTIGATORS]
        tasks.extend(
            [
                {
                    "id": reviewer_id,
                    "agent_name": "Reviewer Agent",
                    "goal": "Review each specialist result for document use, support, calculations, hallucination risk, and missing information.",
                    "dependencies": investigation_ids,
                },
                {
                    "id": synthesis_id,
                    "agent_name": "Synthesis Agent",
                    "goal": "Combine reviewed findings into a concise answer, cite evidence, and state uncertainty.",
                    "dependencies": [reviewer_id, *investigation_ids],
                },
            ]
        )
        return {"goal": goal, "subtasks": tasks}

    @staticmethod
    def _context(user_content: str) -> dict[str, Any]:
        try:
            value = json.loads(user_content)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _extract_document(context_text: str) -> tuple[str, str] | None:
        match = re.search(
            r"Reference document \(([^)]+)\):\s*\n(.*?)(?=\n\n(?:Reference document|Recent conversation memory)|\Z)",
            context_text,
            flags=re.DOTALL,
        )
        if not match:
            return None
        source = match.group(1).strip()
        excerpt = " ".join(match.group(2).split())[:400]
        return (source, excerpt) if excerpt else None

    @staticmethod
    def _final(
        answer: str,
        confidence: float,
        evidence: list[dict[str, Any]] | None = None,
        review: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "action": "final",
            "tool": None,
            "arguments": {},
            "reason": "Use supplied evidence and be explicit about gaps.",
            "answer": answer,
            "confidence": confidence,
            "evidence": evidence or [],
            "review": review,
        }

    def _agent_response(self, system_prompt: str, user_content: str) -> dict[str, Any]:
        context = self._context(user_content)
        agent_name = next(
            (name for _, name, _ in INVESTIGATORS if f"You are {name}." in system_prompt),
            "",
        )
        if "You are Reviewer Agent." in system_prompt:
            return self._review(context)
        if "You are Synthesis Agent." in system_prompt:
            return self._synthesize(context)
        if not agent_name:
            raise ValueError("Fake LLM received an unknown agent prompt")

        available = context.get("available_tools", [])
        trace = context.get("prior_tool_trace", [])
        context_text = str(context.get("business_context_reference_only", ""))
        preferred_tools = {
            "Financial Agent": ["stripe_balance_summary"],
            "Research Agent": ["hubspot_company_sample", "zendesk_ticket_count"],
        }.get(agent_name, [])
        for tool_name in preferred_tools:
            if any(tool.get("name") == tool_name for tool in available) and not trace:
                return {
                    "action": "use_tool",
                    "tool": tool_name,
                    "arguments": {},
                    "reason": "A configured read-only provider can add a source alongside uploaded material.",
                    "answer": None,
                    "confidence": 0.5,
                    "evidence": [],
                    "review": None,
                }
        if trace:
            observation = trace[-1].get("observation", {})
            source = str(observation.get("source", trace[-1].get("tool", "Configured integration")))
            if isinstance(observation, dict) and observation.get("error"):
                return self._final(
                    f"Demo-mode {agent_name} could not retrieve data from {source}. No live provider result was received, and no company metrics are inferred.",
                    0.1,
                )
            evidence = [{
                "source": source,
                "excerpt": json.dumps(observation, sort_keys=True)[:700],
                "location": None,
                "kind": "integration",
            }]
            return self._final(
                f"Demo-mode {agent_name} received a live read-only tool response from {source}. Review the observation in the evidence panel; the fake model does not independently interpret or verify it.",
                0.45,
                evidence,
            )

        document = self._extract_document(context_text)
        if not document:
            return self._final(
                f"Fake-mode {agent_name} did not find an uploaded document excerpt for this task. No company figures were analyzed. Switch to a real model and upload relevant PDF/CSV files for substantive analysis.",
                0.12,
            )
        source, excerpt = document
        focus = next(description for _, name, description in INVESTIGATORS if name == agent_name)
        citation = [{
            "source": source,
            "excerpt": excerpt,
            "location": None,
            "kind": "document",
        }]
        return self._final(
            f"Fake-mode {agent_name} located supplied material in {source}. This deterministic demo does not calculate or interpret business results; it surfaces the excerpt for the real model to analyze. Assigned focus: {focus}",
            0.3,
            citation,
        )

    def _review(self, context: dict[str, Any]) -> dict[str, Any]:
        dependencies = context.get("dependency_results", {})
        evidence = [
            item
            for result in dependencies.values()
            for item in result.get("evidence", [])
            if isinstance(item, dict)
        ]
        document_used = any(item.get("kind") == "document" for item in evidence)
        supported = bool(evidence)
        review = {
            "used_supplied_documents": document_used,
            "supported_by_data": supported,
            "calculations_consistent": None,
            "hallucination_risk": "medium" if supported else "high",
            "missing_information": [] if document_used else [
                "No uploaded document excerpt was available to validate."
            ],
            "notes": "This local deterministic reviewer checks structure and citations only; it cannot substantively verify calculations or model claims.",
        }
        response_evidence = evidence[:10]
        return self._final(
            "Local demo review complete. The checklist is illustrative; use a real model and verify important figures manually.",
            0.25 if supported else 0.1,
            response_evidence,
            review,
        )

    def _synthesize(self, context: dict[str, Any]) -> dict[str, Any]:
        dependencies = context.get("dependency_results", {})
        reviewer = next(
            (value for value in dependencies.values() if value.get("agent_name") == "Reviewer Agent"),
            {},
        )
        evidence: list[dict[str, Any]] = []
        findings: list[str] = []
        for result in dependencies.values():
            if result.get("agent_name") == "Reviewer Agent":
                continue
            if result.get("success") and result.get("answer"):
                findings.append(str(result["answer"])[:360])
            for item in result.get("evidence", []):
                if isinstance(item, dict) and item not in evidence:
                    evidence.append(item)
        if not findings:
            text = "No specialist findings were available. Review the input files and retry with a real model."
            confidence = 0.1
        else:
            text = "\n".join(f"• {finding}" for finding in findings[:6])
            text += "\n\nThis is a deterministic demo summary, not a substantive financial or business conclusion."
            confidence = 0.25
        review = reviewer.get("review")
        if isinstance(review, dict) and review.get("missing_information"):
            text += "\n\nReviewer noted missing information: " + "; ".join(review["missing_information"][:4])
        return self._final(text, confidence, evidence[:20])