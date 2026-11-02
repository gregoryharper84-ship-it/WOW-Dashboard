"""V1 lesson lookup: pure stdlib, fail closed on missing verification."""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "lessons.py"
spec = importlib.util.spec_from_file_location("wow_lessons", SCRIPT)
lessons = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lessons)


def good_record():
    return {
        "postmortem_id": "PM-2026-10-01-001", "state": "VERIFIED_CLOSED",
        "root_cause_status": "CONFIRMED", "root_cause": "missing caller metadata",
        "learning": {
            "failure_category": "OBSERVABILITY_CORRELATION",
            "preventive_control": "include caller headers",
            "regression_test_added": "test_caller_correlation",
            "why_existing_tests_missed_it": "only status asserted",
        },
        "review": {"status": "PASS"}, "qa": {"status": "PASS"},
        "reporter_closure": {"status": "FIXED_VERIFIED"},
        "release": {"status": "PRODUCTION_VERIFIED", "production_verified": True,
                    "merge_commit": "a" * 40, "deployed_commit": "b" * 40,
                    "deployment_id": "dep-verified"},
        "engineering_fix_ids": ["FIX-2026-10-01-001"],
        "engineering_fixes": {"FIX-2026-10-01-001": {
            "state": "VERIFIED_CLOSED", "merge_commit": "a" * 40,
            "pull_request": 999}},
    }


class TestLessons(unittest.TestCase):
    def test_verified_closure_available_by_exact_category(self):
        found = lessons.verified_lessons([good_record()])
        result = lessons.match(found, "OBSERVABILITY_CORRELATION")
        self.assertEqual(result["status"], "VERIFIED_MATCH")
        self.assertEqual(result["matches"][0]["preventive_control"], "include caller headers")
        self.assertFalse(result["production_verified_for_current_incident"])
        self.assertEqual(lessons.match(found, "RELEASE_REVIEW")["matches"], [])

    def test_no_pending_or_incomplete_claims(self):
        for section, field, value in (
            ("", "state", "DEPLOYED_PENDING_VERIFY"),
            ("", "root_cause_status", "NOT_CONFIRMED"),
            ("review", "status", "HOLD"),
            ("qa", "status", "FAIL"),
            ("reporter_closure", "status", "PENDING"),
            ("release", "production_verified", False),
            ("release", "merge_commit", "invalid"),
            ("release", "deployment_id", ""),
            ("engineering_fixes", "FIX-2026-10-01-001", {}),
            ("learning", "regression_test_added", ""),
        ):
            with self.subTest(section=section, field=field):
                row = copy.deepcopy(good_record())
                (row[section] if section else row)[field] = value
                self.assertEqual(lessons.verified_lessons([row]), [])

    def test_duplicate_id_never_learns_twice(self):
        record = good_record()
        self.assertEqual(len(lessons.verified_lessons([record, copy.deepcopy(record)])), 1)

    def test_invalid_category_rejected(self):
        for value in ("correlation", "OBSERVABILITY-CORRELATION", "", "AA\nX"):
            with self.assertRaises(ValueError):
                lessons.match([], value)

    def test_ledger_authority_fail_closed_and_cli_smoke(self):
        document = {"runtime_generation": "V17_ACTIVE", "terminal_authority": "V17_TERMINAL_REDUCER",
                    "can_execute": False, "records": [good_record()]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text(json.dumps(document))
            self.assertEqual(len(lessons.load_ledger(path)), 1)
            proc = subprocess.run([sys.executable, str(SCRIPT), "match", "--ledger", str(path),
                                   "--category", "OBSERVABILITY_CORRELATION"],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["status"], "VERIFIED_MATCH")
            document["can_execute"] = True
            path.write_text(json.dumps(document))
            with self.assertRaises(ValueError):
                lessons.load_ledger(path)


if __name__ == "__main__":
    unittest.main()
