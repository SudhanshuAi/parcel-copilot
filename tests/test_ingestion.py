from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from parcelpilot.authority import eligible_sources
from parcelpilot.ingestion import IngestionError, ingest_all, verify_source_manifest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACK_AVAILABLE = all((ROOT / name).exists() for name in (
    "01_Support_Policy_v3_CURRENT.pdf",
    "02_Support_Policy_v2_DEPRECATED.pdf",
    "03_Cancellation_and_Service_Credit_SOP_v4.pdf",
    "04_Product_Operations_Guide_and_Known_Issues.pdf",
    "05_Northstar_Logistics_Enterprise_Agreement.pdf",
    "06_LumenWorks_Service_Agreement.pdf",
    "ParcelPilot_Assessment_Data.xlsx",
))


@unittest.skipUnless(SOURCE_PACK_AVAILABLE, "Original assessment source pack is intentionally excluded from this repository.")
class IngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "parcelpilot.db"
        self.report = ingest_all(ROOT, self.database_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def test_ingestion_populates_expected_rows(self) -> None:
        self.assertEqual(self.report["documents"], 6)
        self.assertGreaterEqual(self.report["document_chunks"], 16)
        self.assertGreaterEqual(self.report["policy_rules"], 18)
        self.assertEqual(self.report["accounts"], 4)
        self.assertEqual(self.report["orders"], 6)
        self.assertEqual(self.report["tickets"], 7)
        with closing(self.connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM historical_ticket_context").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_overrides").fetchone()[0], 4)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM known_issues").fetchone()[0], 2)

    def test_snapshot_and_contract_scope_are_preserved(self) -> None:
        with closing(self.connect()) as connection:
            snapshot = json.loads(connection.execute("SELECT value_json FROM dataset_metadata WHERE key = 'Dataset snapshot'").fetchone()[0])
            self.assertEqual(snapshot, "2026-08-16 11:00 Asia/Kolkata")
            northstar = connection.execute("SELECT account_id, status, effective_from, effective_to FROM document_sources WHERE source_id = 'northstar-agreement'").fetchone()
            self.assertEqual(dict(northstar), {"account_id": "ACCT-001", "status": "ACTIVE", "effective_from": "2026-01-01", "effective_to": "2026-12-31"})
            deprecated = connection.execute("SELECT status, context_only FROM document_sources WHERE source_id = 'support-policy-v2'").fetchone()
            self.assertEqual(dict(deprecated), {"status": "DEPRECATED", "context_only": 1})

    def test_typed_rules_capture_source_specific_overrides(self) -> None:
        with closing(self.connect()) as connection:
            northstar = json.loads(connection.execute("SELECT payload_json FROM policy_rules WHERE source_id = 'northstar-agreement' AND rule_key = 'cancellation.booked_pre_pickup_fee_waiver'").fetchone()[0])
            lumen_credit = json.loads(connection.execute("SELECT payload_json FROM policy_rules WHERE source_id = 'lumenworks-agreement' AND rule_key = 'service_credit.failed_pickup'").fetchone()[0])
            ki208 = json.loads(connection.execute("SELECT payload_json FROM policy_rules WHERE source_id = 'product-operations-guide' AND rule_key = 'known_issue.KI-208'").fetchone()[0])
        self.assertEqual(northstar["fee_inr"], 0)
        self.assertEqual(lumen_credit, {"delay_hours": 4, "fixed_credit_inr": 300})
        self.assertEqual(ki208["workaround_below_rows"], 3000)

    def test_fts_retains_current_and_audit_material_with_status_metadata(self) -> None:
        with closing(self.connect()) as connection:
            sources = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT source_id FROM document_chunks_fts WHERE document_chunks_fts MATCH 'cancellation fee'"
                )
            }
        self.assertIn("cancellation-credit-sop-v4", sources)
        self.assertIn("northstar-agreement", sources)

    def test_manifest_rejects_missing_source_pack(self) -> None:
        with tempfile.TemporaryDirectory() as empty_dir:
            with self.assertRaises(IngestionError):
                verify_source_manifest(Path(empty_dir))

    def test_authority_registry_excludes_deprecated_and_other_account_sources(self) -> None:
        snapshot_day = date(2026, 8, 16)
        lumen_cancellation = [source.source_id for source in eligible_sources("ACCT-002", "cancellation", snapshot_day)]
        northstar_sla = [source.source_id for source in eligible_sources("ACCT-001", "support_sla", snapshot_day)]
        self.assertEqual(lumen_cancellation, ["cancellation-credit-sop-v4", "lumenworks-agreement"])
        self.assertEqual(northstar_sla[:2], ["northstar-agreement", "support-policy-v3"])
        self.assertNotIn("support-policy-v2", lumen_cancellation + northstar_sla)
        self.assertNotIn("northstar-agreement", lumen_cancellation)


if __name__ == "__main__":
    unittest.main()
