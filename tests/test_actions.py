from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from parcelpilot.actions import ActionError, ActionService
from parcelpilot.api import CSRF_COOKIE, create_app
from parcelpilot.auth import AuthContext
from parcelpilot.repositories import NotFoundOrNotAuthorized
from tests.helpers import prepare_database


ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET = "this-is-a-test-session-secret-that-is-long-enough"


def auth_for(account_id: str, session_id: str = "test-session") -> AuthContext:
    return AuthContext(
        user_id=f"test-{account_id}",
        account_id=account_id,
        role="customer",
        session_id=session_id,
        expires_at=datetime(2026, 8, 17, tzinfo=UTC),
    )


class ActionFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "parcelpilot.db"
        prepare_database(self.database_path)
        self.now = datetime(2026, 8, 16, 11, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def service(self, account_id: str, session_id: str = "test-session", now: datetime | None = None) -> ActionService:
        return ActionService(self.database_path, auth_for(account_id, session_id), clock=lambda: now or self.now)

    def proposal(self, service: ActionService) -> dict:
        return service.propose_escalation(
            target_type="ticket",
            target_id="TKT-502",
            reason_code="OUT_OF_SCOPE",
            summary="Review a requested goodwill exception for TKT-502.",
            evidence_source_ids=["product-operations-guide"],
        )

    def test_proposal_is_pending_until_explicit_confirm_and_replay_is_idempotent(self) -> None:
        service = self.service("ACCT-002")
        proposal = self.proposal(service)
        self.assertEqual(proposal["status"], "proposed")
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM executed_actions").fetchone()[0], 0)
        first = service.confirm_and_execute(proposal["proposal_id"], proposal["payload_hash"])
        second = service.confirm_and_execute(proposal["proposal_id"], proposal["payload_hash"])
        self.assertEqual(first.status, "executed")
        self.assertEqual(second.status, "already_executed")
        self.assertEqual(first.action_id, second.action_id)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM executed_actions").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT status FROM action_proposals WHERE proposal_id = ?", (proposal["proposal_id"],)).fetchone()[0], "executed")

    def test_cross_account_session_and_payload_tampering_fail_closed(self) -> None:
        service = self.service("ACCT-002")
        proposal = self.proposal(service)
        with self.assertRaises(ActionError):
            service.confirm_and_execute(proposal["proposal_id"], "0" * 64)
        with self.assertRaises(NotFoundOrNotAuthorized):
            self.service("ACCT-001", "other-session").confirm_and_execute(proposal["proposal_id"], proposal["payload_hash"])
        with self.assertRaises(NotFoundOrNotAuthorized):
            service.propose_escalation(
                target_type="ticket",
                target_id="TKT-501",
                reason_code="P1",
                summary="Try to access another customer ticket.",
                evidence_source_ids=[],
            )
        with self.assertRaises(NotFoundOrNotAuthorized):
            service.propose_escalation(
                target_type="general_request",
                target_id=None,
                reason_code="OTHER",
                summary="Try to cite another account agreement.",
                evidence_source_ids=["northstar-agreement"],
            )

    def test_cancelled_and_expired_proposals_cannot_execute(self) -> None:
        service = self.service("ACCT-002")
        proposal = self.proposal(service)
        cancelled = service.cancel_proposal(proposal["proposal_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaises(ActionError):
            service.confirm_and_execute(proposal["proposal_id"], proposal["payload_hash"])

        expiring = self.proposal(service)
        expired_service = self.service("ACCT-002", now=self.now + timedelta(minutes=11))
        with self.assertRaises(ActionError):
            expired_service.confirm_and_execute(expiring["proposal_id"], expiring["payload_hash"])
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(connection.execute("SELECT status FROM action_proposals WHERE proposal_id = ?", (expiring["proposal_id"],)).fetchone()[0], "expired")

    def test_api_confirmation_requires_csrf_and_exposes_a_reviewable_ui(self) -> None:
        app = create_app(self.database_path, session_secret=TEST_SECRET)
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
            self.assertEqual(client.get("/").status_code, 200)
            self.assertIn("Resolve shipments with confidence.", client.get("/").text)
            client.post("/auth/demo-login", json={"identity": "lumenworks_demo"})
            csrf = client.cookies.get(CSRF_COOKIE)
            self.assertIsNotNone(csrf)
            body = {
                "target_type": "ticket",
                "target_id": "TKT-502",
                "reason_code": "OUT_OF_SCOPE",
                "summary": "Review goodwill exception request.",
                "evidence_source_ids": ["product-operations-guide"],
            }
            self.assertEqual(client.post("/api/actions/proposals", json=body).status_code, 403)
            proposed = client.post("/api/actions/proposals", json=body, headers={"X-CSRF-Token": csrf})
            self.assertEqual(proposed.status_code, 200)
            proposal = proposed.json()["proposal"]
            self.assertEqual(client.post(f"/api/actions/{proposal['proposal_id']}/confirm", json={"expected_payload_hash": proposal["payload_hash"]}).status_code, 403)
            confirmed = client.post(
                f"/api/actions/{proposal['proposal_id']}/confirm",
                json={"expected_payload_hash": proposal["payload_hash"]},
                headers={"X-CSRF-Token": csrf},
            )
            self.assertEqual(confirmed.status_code, 200)
            self.assertEqual(confirmed.json()["action"]["status"], "executed")


if __name__ == "__main__":
    unittest.main()
