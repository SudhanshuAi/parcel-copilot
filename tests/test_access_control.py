from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from parcelpilot.api import CSRF_COOKIE, SESSION_COOKIE, create_app
from parcelpilot.auth import AuthenticationError, AuthContext, SessionSigner
from parcelpilot.repositories import NotFoundOrNotAuthorized, ScopedRepository
from tests.helpers import prepare_database


ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET = "this-is-a-test-session-secret-that-is-long-enough"


def auth_for(account_id: str) -> AuthContext:
    return AuthContext(
        user_id=f"test-{account_id}",
        account_id=account_id,
        role="customer",
        session_id="test-session",
        expires_at=datetime(2026, 8, 17, tzinfo=UTC),
    )


class AccessControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "parcelpilot.db"
        prepare_database(self.database_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_session_tampering_and_expiry_are_rejected(self) -> None:
        now = datetime(2026, 8, 16, 11, tzinfo=UTC)
        signer = SessionSigner(TEST_SECRET, lifetime=timedelta(minutes=1), clock=lambda: now)
        token = signer.issue("lumenworks_demo")
        self.assertEqual(signer.verify(token).account_id, "ACCT-002")
        with self.assertRaises(AuthenticationError):
            signer.verify(token[:-1] + ("a" if token[-1] != "a" else "b"))
        expired = SessionSigner(TEST_SECRET, lifetime=timedelta(seconds=-1), clock=lambda: now).issue("lumenworks_demo")
        with self.assertRaises(AuthenticationError):
            signer.verify(expired)

    def test_csrf_token_is_bound_to_the_authenticated_session(self) -> None:
        signer = SessionSigner(TEST_SECRET)
        auth = signer.verify(signer.issue("lumenworks_demo"))
        csrf = signer.issue_csrf(auth.session_id)
        self.assertTrue(signer.verify_csrf(csrf, auth))
        self.assertFalse(signer.verify_csrf(csrf + "tampered", auth))
        self.assertFalse(signer.verify_csrf(csrf, auth_for("ACCT-002")))

    def test_scoped_repositories_do_not_leak_other_account_records_or_agreements(self) -> None:
        repository = ScopedRepository(self.database_path, auth_for("ACCT-002"))
        own_order = repository.get_order("ORD-2001")
        self.assertEqual(own_order["order_id"], "ORD-2001")
        self.assertNotIn("account_id", own_order)
        with self.assertRaises(NotFoundOrNotAuthorized) as order_error:
            repository.get_order("ORD-1001")
        self.assertEqual(str(order_error.exception), "Record not found.")
        with self.assertRaises(NotFoundOrNotAuthorized):
            repository.get_ticket("TKT-501")
        results = repository.search_documents("cancellation fee", topic="cancellation")
        source_ids = {result["source_id"] for result in results}
        self.assertIn("cancellation-credit-sop-v4", source_ids)
        self.assertNotIn("northstar-agreement", source_ids)
        self.assertNotIn("support-policy-v2", source_ids)
        self.assertTrue(all("account_id" not in result for result in results))

    def test_api_enforces_cookie_auth_and_generic_denials(self) -> None:
        app = create_app(self.database_path, session_secret=TEST_SECRET)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/orders/ORD-2001").status_code, 401)
            login = client.post("/auth/demo-login", json={"identity": "lumenworks_demo"})
            self.assertEqual(login.status_code, 200)
            self.assertIn("HttpOnly", login.headers["set-cookie"])
            self.assertIn(CSRF_COOKIE, login.headers["set-cookie"])
            own = client.get("/api/orders/ORD-2001")
            self.assertEqual(own.status_code, 200)
            self.assertEqual(own.json()["record"]["order_id"], "ORD-2001")
            self.assertNotIn("account_id", own.json()["record"])
            cross_account = client.get("/api/orders/ORD-1001")
            self.assertEqual(cross_account.status_code, 404)
            self.assertEqual(cross_account.json(), {"detail": "Record not found."})
            self.assertNotIn("Northstar", cross_account.text)
            documents = client.get("/api/documents/search", params={"query": "cancellation fee", "topic": "cancellation"})
            self.assertEqual(documents.status_code, 200)
            self.assertNotIn("northstar-agreement", {item["source_id"] for item in documents.json()["results"]})

    def test_api_rejects_client_supplied_account_scope_and_tampered_cookie(self) -> None:
        app = create_app(self.database_path, session_secret=TEST_SECRET)
        with TestClient(app) as client:
            response = client.post("/auth/demo-login", json={"identity": "lumenworks_demo", "account_id": "ACCT-001"})
            self.assertEqual(response.status_code, 422)
            client.post("/auth/demo-login", json={"identity": "lumenworks_demo"})
            token = client.cookies.get(SESSION_COOKIE)
            self.assertIsNotNone(token)
            client.cookies.set(SESSION_COOKIE, f"{token}tampered")
            response = client.get("/api/me")
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {"detail": "Authentication required."})

    def test_state_changing_logout_requires_the_session_bound_csrf_token(self) -> None:
        app = create_app(self.database_path, session_secret=TEST_SECRET)
        with TestClient(app) as client:
            client.post("/auth/demo-login", json={"identity": "lumenworks_demo"})
            self.assertEqual(client.post("/auth/logout").status_code, 403)
            csrf = client.cookies.get(CSRF_COOKIE)
            self.assertIsNotNone(csrf)
            self.assertEqual(client.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
