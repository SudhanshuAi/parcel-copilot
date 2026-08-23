from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from parcelpilot.api import create_app
from parcelpilot.auth import AuthContext
from tests.helpers import prepare_database
from parcelpilot.policy_engine import PolicyEvaluator
from parcelpilot.repositories import NotFoundOrNotAuthorized
from parcelpilot.tools import ToolService


ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET = "this-is-a-test-session-secret-that-is-long-enough"


def auth_for(account_id: str) -> AuthContext:
    return AuthContext(
        user_id=f"test-{account_id}",
        account_id=account_id,
        role="customer",
        session_id=f"test-session-{account_id}",
        expires_at=datetime(2026, 8, 17, tzinfo=UTC),
    )


class PolicyEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "parcelpilot.db"
        prepare_database(self.database_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def evaluate(self, account_id: str, case_type: str, record_type: str, record_id: str, reported_facts: dict[str, str] | None = None):
        return PolicyEvaluator(self.database_path, auth_for(account_id)).evaluate(case_type, record_type, record_id, reported_facts)

    def test_northstar_cancellation_agreement_overrides_default_fee(self) -> None:
        result = self.evaluate("ACCT-001", "cancellation", "order", "ORD-1001")
        self.assertEqual(result.outcome, "no_fee")
        self.assertEqual(result.amount_inr, 0)
        self.assertEqual(result.applied_sources[0].source_id, "northstar-agreement")
        self.assertEqual(result.overridden_sources[0].source_id, "cancellation-credit-sop-v4")

    def test_lumen_and_beacon_cancellation_rules_use_default_sop(self) -> None:
        lumen = self.evaluate("ACCT-002", "cancellation", "order", "ORD-2001")
        beacon = self.evaluate("ACCT-003", "cancellation", "order", "ORD-3001")
        self.assertEqual((lumen.outcome, lumen.amount_inr), ("fee_applies", 250))
        self.assertEqual((beacon.outcome, beacon.amount_inr), ("no_fee", 0))
        self.assertEqual(lumen.applied_sources[0].source_id, "cancellation-credit-sop-v4")

    def test_picked_up_order_is_not_cancellable(self) -> None:
        result = self.evaluate("ACCT-001", "cancellation", "order", "ORD-1002")
        self.assertEqual(result.outcome, "not_eligible")
        self.assertIn({"label": "required_workflow", "value": "return_to_origin"}, result.calculation)

    def test_lumen_failed_pickup_uses_contract_threshold_and_fixed_credit(self) -> None:
        result = self.evaluate("ACCT-002", "failed_pickup_credit", "order", "ORD-2002")
        self.assertEqual((result.outcome, result.amount_inr, result.confidence), ("eligible", 300, "high"))
        self.assertEqual(result.applied_sources[0].source_id, "lumenworks-agreement")
        self.assertEqual(result.overridden_sources[0].source_id, "cancellation-credit-sop-v4")
        self.assertIn({"label": "delay_hours", "value": 4.5}, result.calculation)

    def test_swiftship_known_issue_forces_verification_in_delay_window(self) -> None:
        result = self.evaluate(
            "ACCT-001",
            "failed_pickup_credit",
            "order",
            "ORD-1001",
            {"physical_pickup_reported_at": "2026-08-16T10:50:00+05:30"},
        )
        self.assertEqual(result.outcome, "indeterminate")
        self.assertEqual(result.confidence, "needs_verification")
        self.assertIn("stale_status_possible", result.data_quality_flags)
        self.assertEqual(result.applied_sources[0].rule_key, "known_issue.KI-211")

    def test_p1_sla_uses_northstar_override_but_does_not_invent_response_event(self) -> None:
        result = self.evaluate("ACCT-001", "first_response_sla", "ticket", "TKT-501")
        self.assertEqual(result.severity, "P1")
        self.assertEqual(result.deadline.isoformat(), "2026-08-16T10:45:00+05:30")
        self.assertEqual(result.outcome, "deadline_passed_response_unknown")
        self.assertEqual(result.confidence, "needs_verification")
        self.assertEqual(result.recommended_next_step, "propose_escalation")
        self.assertEqual(result.applied_sources[0].source_id, "northstar-agreement")
        self.assertEqual(result.overridden_sources[0].source_id, "support-policy-v3")

    def test_bulk_upload_ticket_is_p2_and_cross_account_evaluation_fails_closed(self) -> None:
        result = self.evaluate("ACCT-002", "severity", "ticket", "TKT-502")
        self.assertEqual((result.outcome, result.severity), ("severity_assessed", "P2"))
        with self.assertRaises(NotFoundOrNotAuthorized):
            self.evaluate("ACCT-002", "cancellation", "order", "ORD-1001")

    def test_remaining_ticket_cases_use_the_current_policy_and_source_trace(self) -> None:
        beacon = self.evaluate("ACCT-003", "first_response_sla", "ticket", "TKT-503")
        northstar = self.evaluate("ACCT-001", "severity", "ticket", "TKT-504")
        axis = self.evaluate("ACCT-004", "first_response_sla", "ticket", "TKT-505")
        self.assertEqual((beacon.severity, beacon.applied_sources[0].source_id), ("P3", "support-policy-v3"))
        self.assertIn({"label": "response_target", "value": "2 business days"}, beacon.calculation)
        self.assertEqual(northstar.severity, "P3")
        self.assertEqual((axis.severity, axis.deadline.isoformat()), ("P1", "2026-08-16T09:00:00+05:30"))
        self.assertEqual(axis.applied_sources[0].source_id, "support-policy-v3")
        self.assertNotIn("support-policy-v2", axis.candidate_source_ids)

    def test_tool_service_and_api_do_not_accept_account_scope_in_evaluation(self) -> None:
        service = ToolService(self.database_path, auth_for("ACCT-002"))
        self.assertEqual(service.evaluate_case("failed_pickup_credit", "order", "ORD-2002").amount_inr, 300)
        app = create_app(self.database_path, session_secret=TEST_SECRET)
        with TestClient(app) as client:
            self.assertEqual(client.post("/auth/demo-login", json={"identity": "lumenworks_demo"}).status_code, 200)
            response = client.post(
                "/api/evaluate",
                json={"case_type": "failed_pickup_credit", "record_type": "order", "record_id": "ORD-2002"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["amount_inr"], 300)
            hostile = client.post(
                "/api/evaluate",
                json={"case_type": "cancellation", "record_type": "order", "record_id": "ORD-1001", "account_id": "ACCT-001"},
            )
            self.assertEqual(hostile.status_code, 422)
            unsupported_fact = client.post(
                "/api/evaluate",
                json={
                    "case_type": "failed_pickup_credit",
                    "record_type": "order",
                    "record_id": "ORD-2002",
                    "reported_facts": {"account_id": "ACCT-001"},
                },
            )
            self.assertEqual(unsupported_fact.status_code, 422)


if __name__ == "__main__":
    unittest.main()
