"""Repeatable assessment evals for authority, privacy, and action safety.

Run with ``python -m parcelpilot.evals path/to/parcelpilot.db``.  These evals
exercise the same scoped services used by the API, so they work without an API
key and make a useful release gate in CI.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from parcelpilot.actions import ActionService
from parcelpilot.auth import AuthContext
from parcelpilot.repositories import NotFoundOrNotAuthorized
from parcelpilot.tools import ToolService


SNAPSHOT_CLOCK = lambda: datetime(2026, 8, 16, 11, tzinfo=UTC)


def _auth(account_id: str, session_id: str = "eval-session") -> AuthContext:
    return AuthContext(f"eval-{account_id}", account_id, "customer", session_id, datetime(2026, 8, 17, tzinfo=UTC))


@dataclass(frozen=True)
class EvalResult:
    case_id: int
    category: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "category": self.category, "passed": self.passed, "detail": self.detail}


def run_evals(database_path: Path) -> dict[str, Any]:
    """Run the 12 published assessment cases and return a JSON-serializable report."""
    def tool(account_id: str) -> ToolService:
        return ToolService(database_path, _auth(account_id))

    def action(account_id: str, session_id: str = "eval-session") -> ActionService:
        return ActionService(database_path, _auth(account_id, session_id), clock=SNAPSHOT_CLOCK)

    checks: list[tuple[str, Callable[[], None]]] = []

    def check_1() -> None:
        result = tool("ACCT-001").evaluate_case("cancellation", "order", "ORD-1001", None).as_dict()
        assert result["outcome"] == "no_fee" and result["amount_inr"] == 0
        assert {s["source_id"] for s in result["applied_sources"]} >= {"northstar-agreement"}
        assert {s["source_id"] for s in result["overridden_sources"]} >= {"cancellation-credit-sop-v4"}

    def check_2() -> None:
        result = tool("ACCT-001").evaluate_case("cancellation", "order", "ORD-1002", None).as_dict()
        assert result["outcome"] == "not_eligible"

    def check_3() -> None:
        result = tool("ACCT-002").evaluate_case("cancellation", "order", "ORD-2001", None).as_dict()
        assert result["outcome"] == "fee_applies" and result["amount_inr"] == 250

    def check_4() -> None:
        result = tool("ACCT-002").evaluate_case("failed_pickup_credit", "order", "ORD-2002", None).as_dict()
        assert result["outcome"] == "eligible" and result["amount_inr"] == 300
        assert any(s["source_id"] == "lumenworks-agreement" for s in result["applied_sources"])

    def check_5() -> None:
        result = tool("ACCT-003").evaluate_case("cancellation", "order", "ORD-3001", None).as_dict()
        assert result["outcome"] == "no_fee" and result["amount_inr"] == 0

    def check_6() -> None:
        service = tool("ACCT-001")
        severity = service.evaluate_case("severity", "ticket", "TKT-501", None).as_dict()
        sla = service.evaluate_case("first_response_sla", "ticket", "TKT-501", None).as_dict()
        assert severity["severity"] == "P1" and sla["deadline"] is not None
        assert sla["confidence"] == "needs_verification"

    def check_7() -> None:
        results = tool("ACCT-002").search_documents("bulk upload limit known issue", "known_issue", 8)
        assert any(item["source_id"] == "product-operations-guide" for item in results)
        assert all(item["source_id"] != "support-policy-v2" for item in results)

    def check_8() -> None:
        service = tool("ACCT-001")
        ticket = service.lookup_operational_record("ticket", "TKT-504")
        results = service.search_documents("SwiftShip webhook delay BOOKED pickup KI-211", "known_issue", 8)
        assert ticket["ticket_id"] == "TKT-504"
        assert any("KI-211" in item["text"] for item in results)

    def check_9() -> None:
        results = tool("ACCT-003").search_documents("P2 target two business days support policy", "support_sla", 8)
        ids = {item["source_id"] for item in results}
        assert "support-policy-v3" in ids and "support-policy-v2" not in ids

    def check_10() -> None:
        try:
            tool("ACCT-002").lookup_operational_record("order", "ORD-1001")
        except NotFoundOrNotAuthorized:
            return
        raise AssertionError("Cross-account lookup was not denied")

    def check_11() -> None:
        service = action("ACCT-002")
        proposal = service.propose_escalation(target_type="order", target_id="ORD-2001", reason_code="POLICY_EXCEPTION", summary="Review requested goodwill exception.", evidence_source_ids=["cancellation-credit-sop-v4"])
        assert proposal["status"] == "proposed" and proposal["confirmation_required"] is True

    def check_12() -> None:
        owner = action("ACCT-002")
        proposal = owner.propose_escalation(target_type="ticket", target_id="TKT-502", reason_code="OUT_OF_SCOPE", summary="Review escalation request.", evidence_source_ids=["product-operations-guide"])
        first = owner.confirm_and_execute(proposal["proposal_id"], proposal["payload_hash"])
        replay = owner.confirm_and_execute(proposal["proposal_id"], proposal["payload_hash"])
        assert first.action_id == replay.action_id and replay.status == "already_executed"
        try:
            action("ACCT-001", "other-session").confirm_and_execute(proposal["proposal_id"], proposal["payload_hash"])
        except NotFoundOrNotAuthorized:
            return
        raise AssertionError("Cross-account confirmation was not denied")

    checks.extend([
        ("authority", check_1), ("action_safety", check_2), ("authority", check_3), ("authority", check_4),
        ("authority", check_5), ("grounding", check_6), ("authority", check_7), ("grounding", check_8),
        ("authority", check_9), ("privacy", check_10), ("action_safety", check_11), ("action_safety", check_12),
    ])
    results: list[EvalResult] = []
    for number, (category, check) in enumerate(checks, start=1):
        try:
            check()
            results.append(EvalResult(number, category, True, "passed"))
        except (AssertionError, NotFoundOrNotAuthorized, ValueError) as exc:
            results.append(EvalResult(number, category, False, str(exc) or exc.__class__.__name__))

    totals: dict[str, dict[str, int | float]] = {}
    for result in results:
        bucket = totals.setdefault(result.category, {"passed": 0, "total": 0, "pass_rate": 0.0})
        bucket["total"] = int(bucket["total"]) + 1
        bucket["passed"] = int(bucket["passed"]) + int(result.passed)
    for bucket in totals.values():
        bucket["pass_rate"] = round(100 * int(bucket["passed"]) / int(bucket["total"]), 1)
    passed = sum(result.passed for result in results)
    return {
        "release_gate": {"privacy_and_action_safety": "100%", "overall": ">=90%", "deprecated_or_context_authority": "0"},
        "results": [result.as_dict() for result in results],
        "categories": totals,
        "total": len(results),
        "passed": passed,
        "pass_rate": round(100 * passed / len(results), 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ParcelPilot's deterministic 12-case evaluation suite.")
    parser.add_argument("database", type=Path, help="Path to an ingested ParcelPilot SQLite database")
    args = parser.parse_args()
    print(json.dumps(run_evals(args.database), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
