"""End-to-end Reporter intake and historical-lesson integration, no network."""
import contextlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ENGINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ENGINE))
from v17 import nightly_incident_records as reporter


class ReporterLearningIntakeTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        engine = Path(self.work.name) / "artifacts" / "wow-engine"
        root = engine / "v17"
        pm = root / "postmortems"
        fixes = root / "engineering-fixes"
        pm.mkdir(parents=True)
        fixes.mkdir(parents=True)
        (engine / "learning").mkdir()
        shutil.copy2(ENGINE / "learning" / "lessons.py",
                     engine / "learning" / "lessons.py")
        ledger = root / "incident-ledger.json"
        shutil.copy2(ENGINE / "v17" / "incident-ledger.json", ledger)
        self.ledger = ledger
        patches = (
            patch.object(reporter, "ROOT", root),
            patch.object(reporter, "POSTMORTEMS", pm),
            patch.object(reporter, "FIXES", fixes),
            patch.object(reporter, "LEDGER", ledger),
            patch.object(reporter, "_today", lambda: "2026-11-02"),
            patch.object(reporter, "_utc_now", lambda: "2026-11-02T03:45:00Z"),
        )
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for p in patches:
            self.stack.enter_context(p)

    def create(self, evidence, failure_category=None):
        return reporter.create_postmortem(
            title="Self acceptance correlation", severity="P2",
            domain="observability", evidence=evidence,
            failure_category=failure_category,
        )

    def test_exact_category_auto_match_and_ledger_remains_untrusted(self):
        pm = self.create("Seen: OBSERVABILITY_CORRELATION at caller header")
        body = pm.postmortem.read_text()
        self.assertIn("lookup_status: VERIFIED_MATCH", body)
        self.assertIn("prior_incident: PM-2026-09-25-001", body)
        self.assertIn("prior_prevention:", body)
        doc = json.loads(self.ledger.read_text())
        new = doc["records"][-1]
        self.assertEqual(new["state"], "OPEN")
        self.assertEqual(new["workflow_stage"], "REPORTER_INTAKE")
        self.assertNotIn("root_cause", new)
        self.assertIsNone(new["learning"]["failure_category"])
        self.assertEqual(new["root_cause_status"], "PENDING")
        self.assertIs(doc["can_execute"], False)

    def test_optional_explicit_category_is_exact(self):
        pm = self.create("Plain reporter observation", "OBSERVABILITY_CORRELATION")
        self.assertIn("lookup_status: VERIFIED_MATCH", pm.postmortem.read_text())

    def test_missing_and_partial_categories_never_guess(self):
        for evidence in ("caller metadata unknown", "XOBSERVABILITY_CORRELATIONX",
                         "OBSERVABILITY_CORRELATION_UNVERIFIED"):
            pm = self.create(evidence)
            body = pm.postmortem.read_text()
            self.assertIn("lookup_status: NO_VERIFIED_MATCH", body)
            self.assertNotIn("prior_root_cause:", body)

    def test_missing_helper_is_typed_unavailable_not_a_fake_fix(self):
        (reporter.ROOT.parent / "learning" / "lessons.py").unlink()
        pm = self.create("OBSERVABILITY_CORRELATION")
        self.assertIn("lookup_status: UNAVAILABLE", pm.postmortem.read_text())
        self.assertNotIn("prior_root_cause:", pm.postmortem.read_text())

    def test_invalid_category_refused_before_creation(self):
        count_before = len(json.loads(self.ledger.read_text())["records"])
        with self.assertRaises(ValueError):
            self.create("anything", "invalid category")
        self.assertEqual(len(json.loads(self.ledger.read_text())["records"]), count_before)
        self.assertEqual(list(reporter.POSTMORTEMS.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
