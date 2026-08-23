"""Deterministic entitlement, severity, and SLA evaluation over scoped data."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from parcelpilot.auth import AuthContext
from parcelpilot.authority import eligible_sources
from parcelpilot.repositories import NotFoundOrNotAuthorized, ScopedRepository


# India Standard Time is UTC+05:30 year-round; an explicit fixed offset avoids
# relying on an OS-provided IANA timezone database on Windows deployments.
IST = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
BUSINESS_OPEN = time(9, 0)
BUSINESS_CLOSE = time(18, 0)
ALLOWED_REPORTED_FACTS = {"physical_pickup_reported_at"}


@dataclass(frozen=True)
class SourceEvidence:
    source_id: str
    rule_key: str
    topic: str
    section: str
    reason: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {
            "source_id": self.source_id,
            "rule_key": self.rule_key,
            "topic": self.topic,
            "section": self.section,
        }
        if self.reason:
            result["reason"] = self.reason
        return result


@dataclass
class EvaluationResult:
    outcome: str
    amount_inr: int | None = None
    deadline: datetime | None = None
    severity: str | None = None
    calculation: list[dict[str, Any]] = field(default_factory=list)
    applied_sources: list[SourceEvidence] = field(default_factory=list)
    overridden_sources: list[SourceEvidence] = field(default_factory=list)
    missing_or_conflicting_facts: list[str] = field(default_factory=list)
    data_quality_flags: list[str] = field(default_factory=list)
    confidence: Literal["high", "needs_verification"] = "high"
    recommended_next_step: Literal["answer", "propose_escalation", "request_fact"] = "answer"
    candidate_source_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "amount_inr": self.amount_inr,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "severity": self.severity,
            "calculation": self.calculation,
            "applied_sources": [source.as_dict() for source in self.applied_sources],
            "overridden_sources": [source.as_dict() for source in self.overridden_sources],
            "missing_or_conflicting_facts": self.missing_or_conflicting_facts,
            "data_quality_flags": self.data_quality_flags,
            "confidence": self.confidence,
            "recommended_next_step": self.recommended_next_step,
            "decision_trace": {"candidate_source_ids": self.candidate_source_ids},
        }


class PolicyEvaluator:
    """Evaluates an account-scoped case without accepting model-owned identity data."""

    def __init__(self, database_path: Path, auth: AuthContext) -> None:
        self._database_path = database_path
        self._auth = auth
        self._records = ScopedRepository(database_path, auth)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _parse_local_datetime(value: str) -> datetime:
        for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(value, pattern).replace(tzinfo=IST)
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Invalid reported timestamp: {value!r}") from exc
        return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed.astimezone(IST)

    def _snapshot_at(self, connection: sqlite3.Connection) -> datetime:
        row = connection.execute("SELECT value_json FROM dataset_metadata WHERE key = 'Dataset snapshot'").fetchone()
        if row is None:
            raise RuntimeError("Dataset snapshot is missing")
        snapshot = json.loads(row["value_json"])
        timestamp, separator, timezone_name = snapshot.rpartition(" ")
        if separator != " " or timezone_name != "Asia/Kolkata":
            raise RuntimeError("Dataset snapshot must use Asia/Kolkata")
        return self._parse_local_datetime(timestamp)

    def _account_plan(self, connection: sqlite3.Connection) -> str:
        row = connection.execute("SELECT plan FROM accounts WHERE account_id = ?", (self._auth.account_id,)).fetchone()
        if row is None:
            raise NotFoundOrNotAuthorized("Record not found.")
        return row["plan"]

    def _candidate_sources(self, connection: sqlite3.Connection, topic: str) -> list[str]:
        snapshot_day = self._snapshot_at(connection).date()
        return [source.source_id for source in eligible_sources(self._auth.account_id, topic, snapshot_day)]

    @staticmethod
    def _section_for_rule(connection: sqlite3.Connection, source_id: str, rule_key: str) -> str:
        topic = rule_key.split(".", 1)[0]
        topic_terms = {
            "support_sla": "%support%",
            "cancellation": "%cancel%",
            "service_credit": "%credit%",
            "known_issue": "%KI-%",
            "product": "%Bulk%",
        }
        row = connection.execute(
            """SELECT section FROM document_chunks
               WHERE source_id = ? AND text LIKE ? COLLATE NOCASE
               ORDER BY chunk_id LIMIT 1""",
            (source_id, topic_terms.get(topic, "%")),
        ).fetchone()
        return row["section"] if row else rule_key

    def _rule(self, connection: sqlite3.Connection, source_id: str, rule_key: str) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT topic, payload_json FROM policy_rules WHERE source_id = ? AND rule_key = ?",
            (source_id, rule_key),
        ).fetchone()
        if row is None:
            return None
        return {"topic": row["topic"], "payload": json.loads(row["payload_json"])}

    def _evidence(self, connection: sqlite3.Connection, source_id: str, rule_key: str, topic: str, reason: str | None = None) -> SourceEvidence:
        return SourceEvidence(source_id, rule_key, topic, self._section_for_rule(connection, source_id, rule_key), reason)

    @staticmethod
    def _require_reported_facts(reported_facts: dict[str, Any] | None) -> dict[str, Any]:
        reported_facts = reported_facts or {}
        unexpected = set(reported_facts) - ALLOWED_REPORTED_FACTS
        if unexpected:
            raise ValueError(f"Unsupported reported facts: {sorted(unexpected)}")
        if not all(isinstance(value, str) for value in reported_facts.values()):
            raise ValueError("Reported facts must be strings")
        return reported_facts

    def evaluate(
        self,
        case_type: Literal["cancellation", "failed_pickup_credit", "severity", "first_response_sla"],
        record_type: Literal["order", "ticket"],
        record_id: str,
        reported_facts: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        facts = self._require_reported_facts(reported_facts)
        if case_type in {"cancellation", "failed_pickup_credit"}:
            if record_type != "order":
                raise ValueError(f"{case_type} requires an order record")
            order = self._records.get_order(record_id)
            if case_type == "cancellation":
                return self._evaluate_cancellation(order)
            return self._evaluate_failed_pickup_credit(order, facts)
        if record_type != "ticket":
            raise ValueError(f"{case_type} requires a ticket record")
        ticket = self._records.get_ticket(record_id)
        if case_type == "severity":
            return self._evaluate_severity(ticket)
        return self._evaluate_first_response_sla(ticket)

    def _evaluate_cancellation(self, order: dict[str, Any]) -> EvaluationResult:
        with self._connection() as connection:
            candidates = self._candidate_sources(connection, "cancellation")
            result = EvaluationResult(outcome="indeterminate", candidate_source_ids=candidates)
            status = order["status"]
            result.calculation.append({"label": "order_status", "value": status})
            if status == "DRAFT":
                result.outcome = "no_fee"
                result.amount_inr = 0
                result.calculation.append({"label": "cancellation_fee_inr", "value": 0})
                result.applied_sources.append(self._evidence(connection, "cancellation-credit-sop-v4", "cancellation.default_fee", "cancellation"))
                return result
            if status == "PICKED_UP":
                result.outcome = "not_eligible"
                result.confidence = "high"
                result.recommended_next_step = "answer"
                result.calculation.append({"label": "required_workflow", "value": "return_to_origin"})
                result.applied_sources.append(self._evidence(connection, "cancellation-credit-sop-v4", "cancellation.default_fee", "cancellation"))
                return result
            if status == "DELIVERED":
                result.outcome = "not_eligible"
                result.calculation.append({"label": "reason", "value": "Delivered shipments cannot be cancelled."})
                result.applied_sources.append(self._evidence(connection, "cancellation-credit-sop-v4", "cancellation.default_fee", "cancellation"))
                return result
            if status != "BOOKED":
                result.missing_or_conflicting_facts.append(f"Unsupported order status {status!r}.")
                result.confidence = "needs_verification"
                result.recommended_next_step = "request_fact"
                return result

            waiver = next((source_id for source_id in candidates if self._rule(connection, source_id, "cancellation.booked_pre_pickup_fee_waiver")), None)
            if waiver:
                result.outcome = "no_fee"
                result.amount_inr = 0
                result.calculation.append({"label": "cancellation_fee_inr", "value": 0})
                result.calculation.append({"label": "reason", "value": "Account agreement waives BOOKED pre-pickup cancellation fees."})
                result.applied_sources.append(self._evidence(connection, waiver, "cancellation.booked_pre_pickup_fee_waiver", "cancellation"))
                result.overridden_sources.append(self._evidence(connection, "cancellation-credit-sop-v4", "cancellation.default_fee", "cancellation", "Account agreement explicitly waives the default fee."))
                return result

            default_rule = self._rule(connection, "cancellation-credit-sop-v4", "cancellation.default_fee")
            if default_rule is None:
                raise RuntimeError("Current cancellation fee rule is missing")
            request_time = order["cancellation_requested_at"]
            if not request_time:
                result.missing_or_conflicting_facts.append("Cancellation request time is not recorded.")
                result.confidence = "needs_verification"
                result.recommended_next_step = "request_fact"
                return result
            minutes_since_booking = (self._parse_local_datetime(request_time) - self._parse_local_datetime(order["booked_at"])).total_seconds() / 60
            grace = default_rule["payload"]["booked_grace_minutes"]
            fee = default_rule["payload"]["fee_inr"]
            result.calculation.extend((
                {"label": "minutes_since_booking", "value": minutes_since_booking},
                {"label": "grace_minutes", "value": grace},
            ))
            result.amount_inr = 0 if minutes_since_booking <= grace else fee
            result.outcome = "no_fee" if result.amount_inr == 0 else "fee_applies"
            result.calculation.append({"label": "cancellation_fee_inr", "value": result.amount_inr})
            result.applied_sources.append(self._evidence(connection, "cancellation-credit-sop-v4", "cancellation.default_fee", "cancellation"))
            return result

    def _evaluate_failed_pickup_credit(self, order: dict[str, Any], reported_facts: dict[str, Any]) -> EvaluationResult:
        with self._connection() as connection:
            candidates = self._candidate_sources(connection, "service_credit")
            result = EvaluationResult(outcome="indeterminate", candidate_source_ids=candidates)
            snapshot = self._snapshot_at(connection)
            if order["carrier"] == "SwiftShip" and order["status"] == "BOOKED" and "physical_pickup_reported_at" in reported_facts:
                physical_pickup_at = self._parse_local_datetime(reported_facts["physical_pickup_reported_at"])
                delay_minutes = (snapshot - physical_pickup_at).total_seconds() / 60
                if 0 <= delay_minutes <= 20:
                    result.missing_or_conflicting_facts.append("Reported physical pickup conflicts with BOOKED status during the KI-211 webhook-delay window.")
                    result.data_quality_flags.append("stale_status_possible")
                    result.confidence = "needs_verification"
                    result.recommended_next_step = "request_fact"
                    result.calculation.append({"label": "reported_pickup_age_minutes", "value": delay_minutes})
                    result.applied_sources.append(self._evidence(connection, "product-operations-guide", "known_issue.KI-211", "known_issue"))
                    return result
            if order["carrier_fault"] != 1:
                result.outcome = "not_eligible"
                result.calculation.append({"label": "carrier_fault", "value": False})
                result.applied_sources.append(self._evidence(connection, "cancellation-credit-sop-v4", "service_credit.default_failed_pickup", "service_credit"))
                return result
            if order["customer_fault"] != 0:
                result.outcome = "not_eligible"
                result.calculation.append({"label": "customer_fault", "value": True})
                result.applied_sources.append(self._evidence(connection, "cancellation-credit-sop-v4", "service_credit.default_failed_pickup", "service_credit"))
                return result
            pickup_end = self._parse_local_datetime(order["pickup_window_end"])
            delay_hours = (snapshot - pickup_end).total_seconds() / 3600
            result.calculation.extend((
                {"label": "pickup_window_end", "value": pickup_end.isoformat()},
                {"label": "dataset_snapshot", "value": snapshot.isoformat()},
                {"label": "delay_hours", "value": delay_hours},
                {"label": "carrier_fault", "value": True},
                {"label": "customer_fault", "value": False},
            ))
            account_rule_source = next((source_id for source_id in candidates if self._rule(connection, source_id, "service_credit.failed_pickup")), None)
            if account_rule_source:
                rule_key = "service_credit.failed_pickup"
                payload = self._rule(connection, account_rule_source, rule_key)["payload"]
                if delay_hours <= payload["delay_hours"]:
                    result.outcome = "not_eligible"
                    result.calculation.append({"label": "required_delay_hours", "value": payload["delay_hours"]})
                else:
                    result.outcome = "eligible"
                    result.amount_inr = payload["fixed_credit_inr"]
                    result.calculation.extend((
                        {"label": "required_delay_hours", "value": payload["delay_hours"]},
                        {"label": "credit_inr", "value": result.amount_inr},
                    ))
                result.applied_sources.append(self._evidence(connection, account_rule_source, rule_key, "service_credit"))
                result.overridden_sources.append(self._evidence(connection, "cancellation-credit-sop-v4", "service_credit.default_failed_pickup", "service_credit", "Account agreement replaces the default timing and amount."))
                return result
            default_rule = self._rule(connection, "cancellation-credit-sop-v4", "service_credit.default_failed_pickup")
            if default_rule is None:
                raise RuntimeError("Current failed-pickup credit rule is missing")
            payload = default_rule["payload"]
            if delay_hours <= payload["delay_hours"]:
                result.outcome = "not_eligible"
                result.calculation.append({"label": "required_delay_hours", "value": payload["delay_hours"]})
                result.applied_sources.append(self._evidence(connection, "cancellation-credit-sop-v4", "service_credit.default_failed_pickup", "service_credit"))
                return result
            shipment_percent_credit = round(float(order["shipment_fee_inr"]) * payload["shipment_fee_percent"])
            result.outcome = "eligible"
            result.amount_inr = min(payload["cap_inr"], shipment_percent_credit)
            result.calculation.extend((
                {"label": "required_delay_hours", "value": payload["delay_hours"]},
                {"label": "shipment_fee_percent_credit_inr", "value": shipment_percent_credit},
                {"label": "credit_cap_inr", "value": payload["cap_inr"]},
                {"label": "credit_inr", "value": result.amount_inr},
            ))
            result.applied_sources.append(self._evidence(connection, "cancellation-credit-sop-v4", "service_credit.default_failed_pickup", "service_credit"))
            monthly_cap_source = next((source_id for source_id in candidates if self._rule(connection, source_id, "service_credit.monthly_cap")), None)
            if monthly_cap_source:
                result.missing_or_conflicting_facts.append("Monthly issued-credit usage is unavailable, so the account-level monthly cap cannot be verified.")
                result.confidence = "needs_verification"
                result.recommended_next_step = "request_fact"
                result.applied_sources.append(self._evidence(connection, monthly_cap_source, "service_credit.monthly_cap", "service_credit"))
            return result

    @staticmethod
    def _classify_severity(ticket: dict[str, Any]) -> tuple[str, str]:
        content = f"{ticket['subject']} {ticket['description']}".lower()
        if ("shipment creation" in content and any(term in content for term in ("all", "every user", "complete", "outage"))) or any(term in content for term in ("credential exposure", "api key exposure", "security incident")):
            return "P1", "Complete shipment-creation outage or suspected credential exposure."
        if any(term in content for term in ("fails", "failure", "unavailable", "degraded", "bulk upload")):
            return "P2", "Major feature is unavailable or degraded while core operations remain possible."
        return "P3", "Issue is limited-impact or a normal support request."

    def _evaluate_severity(self, ticket: dict[str, Any]) -> EvaluationResult:
        with self._connection() as connection:
            candidates = self._candidate_sources(connection, "severity")
            severity, reason = self._classify_severity(ticket)
            result = EvaluationResult(outcome="severity_assessed", severity=severity, candidate_source_ids=candidates)
            result.calculation.append({"label": "severity_reason", "value": reason})
            result.applied_sources.append(self._evidence(connection, "support-policy-v3", "severity.definitions", "severity"))
            if severity == "P1":
                result.recommended_next_step = "propose_escalation"
            return result

    @staticmethod
    def _advance_business_time(start: datetime, business_hours: float) -> datetime:
        current = start.astimezone(IST)
        remaining = business_hours
        while remaining > 0:
            if current.weekday() >= 5:
                days_until_monday = 7 - current.weekday()
                current = datetime.combine((current + timedelta(days=days_until_monday)).date(), BUSINESS_OPEN, tzinfo=IST)
                continue
            opening = datetime.combine(current.date(), BUSINESS_OPEN, tzinfo=IST)
            closing = datetime.combine(current.date(), BUSINESS_CLOSE, tzinfo=IST)
            if current < opening:
                current = opening
            elif current >= closing:
                current = datetime.combine((current + timedelta(days=1)).date(), BUSINESS_OPEN, tzinfo=IST)
                continue
            available = (closing - current).total_seconds() / 3600
            if remaining <= available:
                return current + timedelta(hours=remaining)
            remaining -= available
            current = datetime.combine((current + timedelta(days=1)).date(), BUSINESS_OPEN, tzinfo=IST)
        return current

    @classmethod
    def _sla_deadline(cls, created_at: datetime, target: str) -> datetime:
        normalized = target.lower().replace(", 24x7", "").strip()
        amount_match = re.match(r"(\d+)\s+(.+)", normalized)
        if not amount_match:
            raise ValueError(f"Unsupported SLA target: {target!r}")
        amount = int(amount_match.group(1))
        unit = amount_match.group(2)
        if "24x7" in target.lower() or unit.startswith("minute") or unit == "hours" or unit == "hour":
            if unit.startswith("minute"):
                return created_at + timedelta(minutes=amount)
            return created_at + timedelta(hours=amount)
        if unit.startswith("business hour"):
            return cls._advance_business_time(created_at, amount)
        if unit.startswith("business day"):
            return cls._advance_business_time(created_at, amount * 9)
        raise ValueError(f"Unsupported SLA target: {target!r}")

    def _evaluate_first_response_sla(self, ticket: dict[str, Any]) -> EvaluationResult:
        severity, severity_reason = self._classify_severity(ticket)
        with self._connection() as connection:
            candidates = self._candidate_sources(connection, "support_sla")
            plan = self._account_plan(connection)
            rule_key = f"support_sla.{plan.lower()}"
            agreement_source = next(
                (source_id for source_id in candidates if self._rule(connection, source_id, rule_key) and source_id.endswith("agreement")),
                None,
            )
            selected_source = agreement_source or "support-policy-v3"
            selected_rule = self._rule(connection, selected_source, rule_key)
            if selected_rule is None:
                raise RuntimeError(f"No SLA rule found for {plan}")
            target = selected_rule["payload"][severity.lower()]
            deadline = self._sla_deadline(self._parse_local_datetime(ticket["created_at"]), target)
            snapshot = self._snapshot_at(connection)
            result = EvaluationResult(
                outcome="deadline_passed_response_unknown" if snapshot > deadline else "not_breached",
                deadline=deadline,
                severity=severity,
                candidate_source_ids=candidates,
            )
            result.calculation.extend((
                {"label": "severity_reason", "value": severity_reason},
                {"label": "plan", "value": plan},
                {"label": "response_target", "value": target},
                {"label": "ticket_created_at", "value": ticket["created_at"]},
                {"label": "dataset_snapshot", "value": snapshot.isoformat()},
                {"label": "business_hours_assumption", "value": "Monday-Friday 09:00-18:00 Asia/Kolkata; no holiday calendar."},
            ))
            result.applied_sources.append(self._evidence(connection, selected_source, rule_key, "support_sla"))
            if agreement_source:
                result.overridden_sources.append(self._evidence(connection, "support-policy-v3", rule_key, "support_sla", "Active account agreement replaces the default target."))
            if snapshot > deadline:
                result.missing_or_conflicting_facts.append("No first-agent response event exists in the supplied data, so a missed deadline cannot prove an SLA breach.")
                result.confidence = "needs_verification"
                result.recommended_next_step = "propose_escalation" if severity == "P1" else "request_fact"
            return result
