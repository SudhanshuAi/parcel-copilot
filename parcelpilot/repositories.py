"""Account-scoped data access. These repositories are the privacy boundary."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from parcelpilot.auth import AuthContext
from parcelpilot.authority import eligible_sources


class NotFoundOrNotAuthorized(LookupError):
    """Intentionally generic to prevent record-ID enumeration."""


class ScopedRepository:
    def __init__(self, database_path: Path, auth: AuthContext) -> None:
        self._database_path = database_path
        self._auth = auth

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _public_row(row: sqlite3.Row, allowed_fields: tuple[str, ...]) -> dict[str, Any]:
        return {field: row[field] for field in allowed_fields}

    def get_account(self) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT account_name, plan, status, csm, premium_support
                   FROM accounts WHERE account_id = ?""",
                (self._auth.account_id,),
            ).fetchone()
        if row is None:
            raise NotFoundOrNotAuthorized("Record not found.")
        return self._public_row(row, ("account_name", "plan", "status", "csm", "premium_support"))

    def get_order(self, order_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT order_id, carrier, status, booked_at, pickup_window_start, pickup_window_end,
                          pickup_actual_at, shipment_fee_inr, carrier_fault, customer_fault,
                          cancellation_requested_at
                   FROM orders WHERE order_id = ? AND account_id = ?""",
                (order_id, self._auth.account_id),
            ).fetchone()
        if row is None:
            raise NotFoundOrNotAuthorized("Record not found.")
        return self._public_row(
            row,
            (
                "order_id", "carrier", "status", "booked_at", "pickup_window_start", "pickup_window_end",
                "pickup_actual_at", "shipment_fee_inr", "carrier_fault", "customer_fault", "cancellation_requested_at",
            ),
        )

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT ticket_id, created_at, status, subject, description, channel,
                          assigned_to, last_customer_message_at
                   FROM tickets WHERE ticket_id = ? AND account_id = ?""",
                (ticket_id, self._auth.account_id),
            ).fetchone()
        if row is None:
            raise NotFoundOrNotAuthorized("Record not found.")
        return self._public_row(
            row,
            ("ticket_id", "created_at", "status", "subject", "description", "channel", "assigned_to", "last_customer_message_at"),
        )

    def _snapshot_day(self, connection: sqlite3.Connection):
        stored = connection.execute("SELECT value_json FROM dataset_metadata WHERE key = 'Dataset snapshot'").fetchone()
        if stored is None:
            raise RuntimeError("Dataset snapshot is missing")
        snapshot = json.loads(stored["value_json"])
        local_timestamp, separator, timezone_name = snapshot.rpartition(" ")
        if separator != " " or timezone_name != "Asia/Kolkata":
            raise RuntimeError("Dataset snapshot must use Asia/Kolkata")
        return datetime.strptime(local_timestamp, "%Y-%m-%d %H:%M").date()

    @staticmethod
    def _fts_terms(query: str) -> str:
        terms = re.findall(r"[A-Za-z0-9_-]+", query)
        # Quote each token so punctuation such as ``4,200-row`` cannot become
        # FTS syntax (or an accidental column reference).  The raw user query
        # is never interpolated into SQL or the MATCH expression.
        # OR improves recall for natural-language questions while retaining
        # quoted tokens; source eligibility remains the authority boundary.
        return " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:12])

    def search_documents(self, query: str, topic: str, limit: int = 5) -> list[dict[str, Any]]:
        if limit < 1 or limit > 8:
            raise ValueError("limit must be between 1 and 8")
        fts_terms = self._fts_terms(query)
        if not fts_terms:
            return []
        with self._connection() as connection:
            source_ids = [source.source_id for source in eligible_sources(self._auth.account_id, topic, self._snapshot_day(connection))]
            if not source_ids:
                return []
            placeholders = ",".join("?" for _ in source_ids)
            rows = connection.execute(
                f"""SELECT c.source_id, c.section, c.text, c.page_number, s.title, s.status,
                            s.effective_from, s.effective_to, s.authority_class, s.account_id
                     FROM document_chunks_fts f
                     JOIN document_chunks c ON c.chunk_id = f.chunk_id
                     JOIN document_sources s ON s.source_id = c.source_id
                     WHERE document_chunks_fts MATCH ? AND c.source_id IN ({placeholders})
                     ORDER BY bm25(document_chunks_fts), c.chunk_id
                     LIMIT ?""",
                (fts_terms, *source_ids, limit),
            ).fetchall()
        return [
            self._public_row(
                row,
                ("source_id", "title", "section", "text", "page_number", "status", "effective_from", "effective_to", "authority_class"),
            )
            for row in rows
        ]
