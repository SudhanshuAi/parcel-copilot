"""Server-side tools to be registered with the LLM loop in M4."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from parcelpilot.actions import ActionService
from parcelpilot.auth import AuthContext
from parcelpilot.policy_engine import EvaluationResult, PolicyEvaluator
from parcelpilot.repositories import ScopedRepository


class ToolService:
    """Tool facade with server-injected AuthContext and no arbitrary data access."""

    def __init__(self, database_path: Path, auth: AuthContext) -> None:
        self._records = ScopedRepository(database_path, auth)
        self._evaluator = PolicyEvaluator(database_path, auth)
        self._actions = ActionService(database_path, auth)

    def search_documents(self, query: str, topic: str, limit: int = 5) -> list[dict[str, Any]]:
        return self._records.search_documents(query, topic, limit)

    def lookup_operational_record(self, record_type: Literal["account", "order", "ticket"], record_id: str | None = None) -> dict[str, Any]:
        if record_type == "account":
            return self._records.get_account()
        if record_type == "order" and record_id:
            return self._records.get_order(record_id)
        if record_type == "ticket" and record_id:
            return self._records.get_ticket(record_id)
        raise ValueError("A record ID is required for order and ticket lookups")

    def evaluate_case(
        self,
        case_type: Literal["cancellation", "failed_pickup_credit", "severity", "first_response_sla"],
        record_type: Literal["order", "ticket"],
        record_id: str,
        reported_facts: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        return self._evaluator.evaluate(case_type, record_type, record_id, reported_facts)

    def propose_escalation(
        self,
        target_type: Literal["ticket", "order", "general_request"],
        target_id: str | None,
        reason_code: Literal["P1", "SLA_BREACH", "POLICY_EXCEPTION", "DATA_CONFLICT", "OUT_OF_SCOPE", "OTHER"],
        summary: str,
        evidence_source_ids: list[str],
    ) -> dict[str, Any]:
        return self._actions.propose_escalation(
            target_type=target_type,
            target_id=target_id,
            reason_code=reason_code,
            summary=summary,
            evidence_source_ids=evidence_source_ids,
        )
