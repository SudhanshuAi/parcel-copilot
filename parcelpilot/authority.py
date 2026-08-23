"""Static metadata that makes source authority explicit and inspectable."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Final


@dataclass(frozen=True)
class DocumentSource:
    source_id: str
    file_name: str
    title: str
    document_type: str
    authority_class: str
    status: str
    effective_from: str | None
    effective_to: str | None
    account_id: str | None
    topics: tuple[str, ...]
    override_topics: tuple[str, ...] = ()
    context_only: bool = False

    def database_values(self) -> dict[str, object]:
        return asdict(self)


SOURCE_MANIFEST: Final[dict[str, str]] = {
    "01_Support_Policy_v3_CURRENT.pdf": "DB7097BB1E881327954282B9A4FBCE8CBBC08D6868AB000478935BC00EE11FA2",
    "02_Support_Policy_v2_DEPRECATED.pdf": "14C3B549474D8079D600935E915483F5085FCC5EDB685C9A9B5CDCC4E687AA27",
    "03_Cancellation_and_Service_Credit_SOP_v4.pdf": "79C20A835F868888236B3166FFB185E5A7921F48BEB309AC79266489E121B006",
    "04_Product_Operations_Guide_and_Known_Issues.pdf": "983BA3A75CEFD62FA39C600E2EF56FB4860B19557D4E42E356C631B45A2249D4",
    "05_Northstar_Logistics_Enterprise_Agreement.pdf": "59E413076B34F5821BF7E3A9834D58CE0ED18883A40D07560D97A998406701CB",
    "06_LumenWorks_Service_Agreement.pdf": "294EAE8CE53BD2FAB97A9A3086C56BE98FEC142A512E9EA8ACF24D88DF70B2A8",
    "ParcelPilot_Assessment_Data.xlsx": "4E69DBFD08B79FDA6266BD7D8CACE2CC3420565A46FAD9D6A4DE3DB9A11E0A72",
}


DOCUMENT_SOURCES: Final[tuple[DocumentSource, ...]] = (
    DocumentSource(
        source_id="support-policy-v3",
        file_name="01_Support_Policy_v3_CURRENT.pdf",
        title="ParcelPilot Support Policy v3",
        document_type="support_policy",
        authority_class="support_policy",
        status="CURRENT",
        effective_from="2026-05-01",
        effective_to=None,
        account_id=None,
        topics=("source_precedence", "severity", "support_sla", "escalation"),
    ),
    DocumentSource(
        source_id="support-policy-v2",
        file_name="02_Support_Policy_v2_DEPRECATED.pdf",
        title="ParcelPilot Support Policy v2",
        document_type="support_policy",
        authority_class="support_policy",
        status="DEPRECATED",
        effective_from="2025-01-01",
        effective_to="2026-04-30",
        account_id=None,
        topics=("severity", "support_sla"),
        context_only=True,
    ),
    DocumentSource(
        source_id="cancellation-credit-sop-v4",
        file_name="03_Cancellation_and_Service_Credit_SOP_v4.pdf",
        title="ParcelPilot Cancellation & Service Credit SOP v4",
        document_type="sop",
        authority_class="sop",
        status="CURRENT",
        effective_from="2026-06-15",
        effective_to=None,
        account_id=None,
        topics=("cancellation", "service_credit", "approval", "uncertainty"),
    ),
    DocumentSource(
        source_id="product-operations-guide",
        file_name="04_Product_Operations_Guide_and_Known_Issues.pdf",
        title="ParcelPilot Product Operations Guide",
        document_type="product_guide",
        authority_class="product_guide",
        status="CURRENT",
        effective_from="2026-08-14",
        effective_to=None,
        account_id=None,
        topics=("product_capability", "known_issue", "shipment_status"),
    ),
    DocumentSource(
        source_id="northstar-agreement",
        file_name="05_Northstar_Logistics_Enterprise_Agreement.pdf",
        title="ParcelPilot - Northstar Logistics Enterprise Agreement",
        document_type="customer_agreement",
        authority_class="agreement",
        status="ACTIVE",
        effective_from="2026-01-01",
        effective_to="2026-12-31",
        account_id="ACCT-001",
        topics=("support_sla", "cancellation", "service_credit", "account_contact"),
        override_topics=("support_sla", "cancellation"),
    ),
    DocumentSource(
        source_id="lumenworks-agreement",
        file_name="06_LumenWorks_Service_Agreement.pdf",
        title="ParcelPilot - LumenWorks Service Agreement",
        document_type="customer_agreement",
        authority_class="agreement",
        status="ACTIVE",
        effective_from="2026-03-01",
        effective_to="2027-02-28",
        account_id="ACCT-002",
        topics=("support_sla", "cancellation", "service_credit"),
        override_topics=("support_sla", "service_credit"),
    ),
)


SOURCE_OVERRIDES: Final[tuple[dict[str, str], ...]] = (
    {
        "source_id": "northstar-agreement",
        "overrides_source_id": "support-policy-v3",
        "topic": "support_sla",
        "reason": "Northstar support targets explicitly replace standard policy targets.",
    },
    {
        "source_id": "northstar-agreement",
        "overrides_source_id": "cancellation-credit-sop-v4",
        "topic": "cancellation",
        "reason": "Northstar waives cancellation fees for BOOKED pre-pickup shipments.",
    },
    {
        "source_id": "lumenworks-agreement",
        "overrides_source_id": "support-policy-v3",
        "topic": "support_sla",
        "reason": "LumenWorks agreement defines its own targets and coverage.",
    },
    {
        "source_id": "lumenworks-agreement",
        "overrides_source_id": "cancellation-credit-sop-v4",
        "topic": "service_credit",
        "reason": "LumenWorks replaces the default failed-pickup threshold and amount.",
    },
)


def source_for_file(file_name: str) -> DocumentSource:
    for source in DOCUMENT_SOURCES:
        if source.file_name == file_name:
            return source
    raise KeyError(f"No document source metadata is registered for {file_name!r}")


def source_path(source_dir: Path, file_name: str) -> Path:
    path = source_dir / file_name
    if not path.is_file():
        raise FileNotFoundError(f"Required source file is missing: {path}")
    return path


def eligible_sources(account_id: str | None, topic: str, as_of: date) -> tuple[DocumentSource, ...]:
    """Return only current/applicable source metadata before any search ranking occurs.

    This pure registry function intentionally knows nothing about request text or
    model instructions. M2 will call the same rule with server-issued account
    context before searching the SQLite index.
    """
    eligible: list[DocumentSource] = []
    for source in DOCUMENT_SOURCES:
        if source.context_only or source.status == "DEPRECATED" or topic not in source.topics:
            continue
        if source.account_id is not None and source.account_id != account_id:
            continue
        if source.effective_from and as_of < date.fromisoformat(source.effective_from):
            continue
        if source.effective_to and as_of > date.fromisoformat(source.effective_to):
            continue
        eligible.append(source)

    def rank(source: DocumentSource) -> tuple[int, str]:
        if source.authority_class == "agreement" and topic in source.override_topics:
            return (0, source.source_id)
        topic_authority = {
            "cancellation": "sop",
            "service_credit": "sop",
            "support_sla": "support_policy",
            "severity": "support_policy",
            "product_capability": "product_guide",
            "known_issue": "product_guide",
            "shipment_status": "product_guide",
        }
        return (1 if source.authority_class == topic_authority.get(topic) else 2, source.source_id)

    return tuple(sorted(eligible, key=rank))
