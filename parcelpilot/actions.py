"""Server-enforced proposal and confirmation flow for mocked escalations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from parcelpilot.auth import AuthContext
from parcelpilot.repositories import NotFoundOrNotAuthorized, ScopedRepository


class ActionError(RuntimeError):
    """Safe, user-presentable action failure without source/account leakage."""


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    proposal_id: str
    status: Literal["executed", "already_executed"]
    created_at: str
    audit_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "action_id": self.action_id,
            "proposal_id": self.proposal_id,
            "status": self.status,
            "created_at": self.created_at,
            "audit_id": self.audit_id,
        }


class ActionService:
    def __init__(self, database_path: Path, auth: AuthContext, *, clock: Callable[[], datetime] | None = None) -> None:
        self._database_path = database_path
        self._auth = auth
        self._clock = clock or (lambda: datetime.now(UTC))
        self._records = ScopedRepository(database_path, auth)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.astimezone(UTC).isoformat()

    @staticmethod
    def _payload_hash(payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @staticmethod
    def _public_proposal(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "proposal_id": row["proposal_id"],
            "action_type": row["action_type"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "reason_code": row["reason_code"],
            "summary": row["summary"],
            "status": row["status"],
            "payload_hash": row["payload_hash"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "confirmation_required": True,
        }

    def _validate_target(self, target_type: str, target_id: str | None) -> None:
        if target_type == "general_request":
            if target_id is not None:
                raise ValueError("General requests must not contain a target ID")
            return
        if not target_id:
            raise ValueError("Ticket and order escalations require a target ID")
        if target_type == "ticket":
            self._records.get_ticket(target_id)
        elif target_type == "order":
            self._records.get_order(target_id)
        else:
            raise ValueError("Unsupported escalation target")

    def _validate_evidence(self, connection: sqlite3.Connection, source_ids: list[str]) -> list[str]:
        if len(source_ids) > 12 or len(set(source_ids)) != len(source_ids):
            raise ValueError("Evidence source IDs are invalid")
        validated: list[str] = []
        snapshot_row = connection.execute("SELECT value_json FROM dataset_metadata WHERE key = 'Dataset snapshot'").fetchone()
        if snapshot_row is None:
            raise RuntimeError("Dataset snapshot is missing")
        snapshot = json.loads(snapshot_row["value_json"])
        snapshot_date = str(snapshot).split(" ", 1)[0]
        for source_id in source_ids:
            if not isinstance(source_id, str) or not source_id or len(source_id) > 100:
                raise ValueError("Evidence source IDs are invalid")
            row = connection.execute(
                """SELECT source_id FROM document_sources
                   WHERE source_id = ?
                     AND status != 'DEPRECATED'
                     AND (account_id IS NULL OR account_id = ?)
                     AND (effective_from IS NULL OR effective_from <= ?)
                     AND (effective_to IS NULL OR effective_to >= ?)""",
                (source_id, self._auth.account_id, snapshot_date, snapshot_date),
            ).fetchone()
            if row is None:
                raise NotFoundOrNotAuthorized("Evidence source not found.")
            validated.append(source_id)
        return validated

    def propose_escalation(
        self,
        *,
        target_type: Literal["ticket", "order", "general_request"],
        target_id: str | None,
        reason_code: Literal["P1", "SLA_BREACH", "POLICY_EXCEPTION", "DATA_CONFLICT", "OUT_OF_SCOPE", "OTHER"],
        summary: str,
        evidence_source_ids: list[str],
    ) -> dict[str, Any]:
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 500:
            raise ValueError("Escalation summary must be 1-500 characters")
        self._validate_target(target_type, target_id)
        now = self._clock()
        expires_at = now + timedelta(minutes=10)
        proposal_id = str(uuid.uuid4())
        payload = {
            "action_type": "escalation",
            "target_type": target_type,
            "target_id": target_id,
            "reason_code": reason_code,
            "summary": summary.strip(),
        }
        payload_hash = self._payload_hash(payload)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                evidence = self._validate_evidence(connection, evidence_source_ids)
                connection.execute(
                    """INSERT INTO action_proposals(
                        proposal_id, action_type, account_id, user_id, session_id, target_type, target_id,
                        reason_code, summary, evidence_json, payload_hash, status, created_at, expires_at
                    ) VALUES (?, 'escalation', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)""",
                    (
                        proposal_id, self._auth.account_id, self._auth.user_id, self._auth.session_id,
                        target_type, target_id, reason_code, summary.strip(), json.dumps(evidence), payload_hash,
                        self._timestamp(now), self._timestamp(expires_at),
                    ),
                )
                audit_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO audit_events(audit_id, event_type, actor_user_id, account_id, proposal_id, details_json, created_at)
                       VALUES (?, 'escalation_proposed', ?, ?, ?, ?, ?)""",
                    (audit_id, self._auth.user_id, self._auth.account_id, proposal_id, json.dumps({"reason_code": reason_code, "target_type": target_type}), self._timestamp(now)),
                )
                row = connection.execute("SELECT * FROM action_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self._public_proposal(row)

    def _proposal_for_current_session(self, connection: sqlite3.Connection, proposal_id: str) -> sqlite3.Row:
        row = connection.execute(
            """SELECT * FROM action_proposals
               WHERE proposal_id = ? AND account_id = ? AND user_id = ? AND session_id = ?""",
            (proposal_id, self._auth.account_id, self._auth.user_id, self._auth.session_id),
        ).fetchone()
        if row is None:
            raise NotFoundOrNotAuthorized("Proposal not found.")
        return row

    def cancel_proposal(self, proposal_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._proposal_for_current_session(connection, proposal_id)
                if row["status"] != "proposed":
                    raise ActionError("This proposal can no longer be cancelled.")
                connection.execute("UPDATE action_proposals SET status = 'cancelled' WHERE proposal_id = ?", (proposal_id,))
                now = self._clock()
                audit_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO audit_events(audit_id, event_type, actor_user_id, account_id, proposal_id, details_json, created_at)
                       VALUES (?, 'escalation_cancelled', ?, ?, ?, '{}', ?)""",
                    (audit_id, self._auth.user_id, self._auth.account_id, proposal_id, self._timestamp(now)),
                )
                updated = connection.execute("SELECT * FROM action_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                connection.execute("COMMIT")
                return self._public_proposal(updated)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def confirm_and_execute(self, proposal_id: str, expected_payload_hash: str) -> ActionResult:
        expired = False
        completed_result: ActionResult | None = None
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._proposal_for_current_session(connection, proposal_id)
                if row["payload_hash"] != expected_payload_hash:
                    raise ActionError("This proposal has changed and must be reviewed again.")
                if row["status"] == "executed":
                    action = connection.execute("SELECT * FROM executed_actions WHERE proposal_id = ?", (proposal_id,)).fetchone()
                    audit = connection.execute(
                        "SELECT audit_id FROM audit_events WHERE action_id = ? AND event_type = 'escalation_executed' ORDER BY created_at LIMIT 1",
                        (action["action_id"],),
                    ).fetchone()
                    connection.execute("COMMIT")
                    return ActionResult(action["action_id"], proposal_id, "already_executed", action["created_at"], audit["audit_id"])
                if row["status"] != "proposed":
                    raise ActionError("This proposal is no longer available for confirmation.")
                now = self._clock()
                if datetime.fromisoformat(row["expires_at"]) <= now.astimezone(UTC):
                    connection.execute("UPDATE action_proposals SET status = 'expired' WHERE proposal_id = ?", (proposal_id,))
                    expired = True
                    audit_id = str(uuid.uuid4())
                    connection.execute(
                        """INSERT INTO audit_events(audit_id, event_type, actor_user_id, account_id, proposal_id, details_json, created_at)
                           VALUES (?, 'escalation_expired', ?, ?, ?, '{}', ?)""",
                        (audit_id, self._auth.user_id, self._auth.account_id, proposal_id, self._timestamp(now)),
                    )
                else:
                    connection.execute("UPDATE action_proposals SET status = 'confirmed', confirmed_at = ? WHERE proposal_id = ?", (self._timestamp(now), proposal_id))
                    connection.execute("UPDATE action_proposals SET status = 'executing' WHERE proposal_id = ?", (proposal_id,))
                    action_id = str(uuid.uuid4())
                    details = {"mocked": True, "queue": "support-escalations", "reason_code": row["reason_code"], "target_type": row["target_type"], "target_id": row["target_id"]}
                    connection.execute(
                        """INSERT INTO executed_actions(action_id, proposal_id, action_type, account_id, details_json, created_at)
                           VALUES (?, ?, 'escalation', ?, ?, ?)""",
                        (action_id, proposal_id, self._auth.account_id, json.dumps(details, sort_keys=True), self._timestamp(now)),
                    )
                    connection.execute("UPDATE action_proposals SET status = 'executed', executed_at = ? WHERE proposal_id = ?", (self._timestamp(now), proposal_id))
                    audit_id = str(uuid.uuid4())
                    connection.execute(
                        """INSERT INTO audit_events(audit_id, event_type, actor_user_id, account_id, proposal_id, action_id, details_json, created_at)
                           VALUES (?, 'escalation_executed', ?, ?, ?, ?, ?, ?)""",
                        (audit_id, self._auth.user_id, self._auth.account_id, proposal_id, action_id, json.dumps(details, sort_keys=True), self._timestamp(now)),
                    )
                    completed_result = ActionResult(action_id, proposal_id, "executed", self._timestamp(now), audit_id)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        if expired:
            raise ActionError("This proposal has expired. Please create a new proposal.")
        if completed_result is None:
            raise RuntimeError("Action confirmation did not produce a result")
        return completed_result
