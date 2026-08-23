"""Deterministic trust signals and a conservative answerability gate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal


ReliabilityKind = Literal["conflict", "deprecated", "context_only", "missing_evidence"]
ReliabilityState = Literal["grounded", "needs_verification", "insufficient_evidence"]


@dataclass(frozen=True)
class ReliabilitySignal:
    kind: ReliabilityKind
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "message": self.message}


@dataclass(frozen=True)
class AnswerabilityDecision:
    state: ReliabilityState
    signals: tuple[ReliabilitySignal, ...]
    replacement_answer: str | None = None

    @property
    def needs_verification(self) -> bool:
        return self.state != "grounded"

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "signals": [signal.as_dict() for signal in self.signals],
        }


class AnswerabilityGate:
    """Prevents an answer from presenting unverified policy conclusions as facts."""

    _NORMATIVE_CLAIM = re.compile(
        r"\b(fee|eligible|eligibility|credit|cancel(?:lation)?|policy|agreement|sla|"
        r"support target|breach(?:ed)?|p[123]|limit(?:ed)?|waive|waiver|refund)\b",
        re.IGNORECASE,
    )
    _UNCERTAINTY = re.compile(
        r"\b(verify|verification|uncertain|cannot confirm|can't confirm|not confirm|"
        r"need(?:s)? (?:a )?(?:human|support)|pending)\b",
        re.IGNORECASE,
    )

    @staticmethod
    def signals_for_request(message: str) -> tuple[ReliabilitySignal, ...]:
        """Expose excluded-source states without ever retrieving those sources."""
        lowered = message.lower()
        signals: list[ReliabilitySignal] = []
        if "support policy v2" in lowered or "deprecated" in lowered:
            signals.append(ReliabilitySignal("deprecated", "Deprecated source material is excluded from current policy decisions."))
        if "historical resolution" in lowered or "historical ticket" in lowered or "context-only" in lowered:
            signals.append(ReliabilitySignal("context_only", "Historical resolutions are context only and cannot establish a current entitlement."))
        return tuple(signals)

    @staticmethod
    def signals_for_tool_output(output: dict[str, Any]) -> tuple[ReliabilitySignal, ...]:
        signals: list[ReliabilitySignal] = []
        if output.get("confidence") == "needs_verification":
            reasons = output.get("missing_or_conflicting_facts", [])
            if reasons:
                for reason in reasons:
                    signals.append(ReliabilitySignal("conflict", str(reason)))
            else:
                signals.append(ReliabilitySignal("missing_evidence", "The available evidence requires human verification."))
        return tuple(signals)

    @staticmethod
    def decide(
        *,
        answer: str,
        citation_count: int,
        signals: Iterable[ReliabilitySignal],
        has_action_proposal: bool,
    ) -> AnswerabilityDecision:
        unique: list[ReliabilitySignal] = []
        seen: set[tuple[str, str]] = set()
        for signal in signals:
            key = (signal.kind, signal.message)
            if key not in seen:
                seen.add(key)
                unique.append(signal)

        if AnswerabilityGate._NORMATIVE_CLAIM.search(answer) and not citation_count and not has_action_proposal:
            unique.append(
                ReliabilitySignal(
                    "missing_evidence",
                    "No authoritative evidence was retrieved for this policy or entitlement conclusion.",
                )
            )
            return AnswerabilityDecision(
                "insufficient_evidence",
                tuple(unique),
                "I can’t provide a confirmed policy or entitlement answer yet because I don’t have authoritative evidence for this request. Please ask support to review it.",
            )

        blocking_signals = [signal for signal in unique if signal.kind in {"conflict", "missing_evidence"}]
        if blocking_signals:
            if not AnswerabilityGate._UNCERTAINTY.search(answer):
                return AnswerabilityDecision(
                    "needs_verification",
                    tuple(unique),
                    "The available records or policies need human verification before I can give a confirmed conclusion. Please ask support to review the flagged evidence.",
                )
            return AnswerabilityDecision("needs_verification", tuple(unique))
        return AnswerabilityDecision("grounded", tuple(unique))
