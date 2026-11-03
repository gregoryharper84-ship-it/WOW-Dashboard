"""V1 lesson lookup: pure stdlib, fail closed on missing verification."""
import contextlib
import copy
import importlib.util
import io
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_default_canonical_ledger_cli_lookup(self):
        """Exercise the committed ledger and real CLI default, not a fixture."""
        canonical = SCRIPT.parents[1] / "v17" / "incident-ledger.json"
        self.assertEqual(lessons.LEDGER, canonical)
        records = lessons.load_ledger(lessons.LEDGER)
        verified = lessons.verified_lessons(records)
        result = lessons.match(verified, "OBSERVABILITY_CORRELATION")
        self.assertEqual(result["status"], "VERIFIED_MATCH")
        self.assertTrue(any(
            item["incident_id"] == "PM-2026-09-25-001"
            for item in result["matches"]
        ))
        self.assertFalse(result["production_verified_for_current_incident"])
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "match",
             "--category", "OBSERVABILITY_CORRELATION"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        output = json.loads(proc.stdout)
        self.assertEqual(output["status"], "VERIFIED_MATCH")
        self.assertEqual(output["category"], "OBSERVABILITY_CORRELATION")
        self.assertTrue(any(
            item["incident_id"] == "PM-2026-09-25-001"
            for item in output["matches"]
        ))
        self.assertFalse(output["production_verified_for_current_incident"])

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


class TestLessonLookupFailClosedBranches(unittest.TestCase):
    """Every unverified ledger shape is skipped, never guessed at."""

    def test_load_ledger_rejects_a_non_object_record(self):
        document = {"runtime_generation": "V17_ACTIVE",
                    "terminal_authority": "V17_TERMINAL_REDUCER",
                    "can_execute": False, "records": ["not a record"]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text(json.dumps(document))
            with self.assertRaises(ValueError) as caught:
                lessons.load_ledger(path)
        self.assertEqual(str(caught.exception), "invalid incident record")

    def test_verified_lessons_skips_every_non_object_evidence_section(self):
        for field in ("learning", "review", "qa", "release",
                      "reporter_closure", "engineering_fixes"):
            with self.subTest(field=field):
                row = copy.deepcopy(good_record())
                row[field] = "unverified"
                self.assertEqual(lessons.verified_lessons([row]), [])

    def test_verified_lessons_skips_unregistered_categories(self):
        for category in ("correlation", "AA\nX", 5, None):
            with self.subTest(category=category):
                row = copy.deepcopy(good_record())
                row["learning"]["failure_category"] = category
                self.assertEqual(lessons.verified_lessons([row]), [])

    def test_verified_lessons_skips_a_non_integer_pull_request(self):
        for pull_request in ("999", None, 0, -1, True):
            with self.subTest(pull_request=pull_request):
                row = copy.deepcopy(good_record())
                row["engineering_fixes"]["FIX-2026-10-01-001"]["pull_request"] = pull_request
                self.assertEqual(lessons.verified_lessons([row]), [])


class TestLessonCliInProcess(unittest.TestCase):
    """Exercise main() directly: the CLI contract, not only a spawned process."""

    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.work = Path(work.name)
        self.ledger = self.work / "ledger.json"
        self.ledger.write_text(json.dumps({
            "runtime_generation": "V17_ACTIVE",
            "terminal_authority": "V17_TERMINAL_REDUCER",
            "can_execute": False, "records": [good_record()]}))

    def call_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["lessons.py", *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lessons.main()
        return code, out.getvalue(), err.getvalue()

    def test_list_reports_read_only_lessons(self):
        code, out, _ = self.call_main(["list", "--ledger", str(self.ledger)])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "READ_ONLY_LESSONS")
        self.assertEqual(payload["count"], 1)
        self.assertTrue(payload["advisory_only"])
        self.assertEqual(payload["lessons"][0]["failure_category"],
                         "OBSERVABILITY_CORRELATION")

    def test_match_requires_a_category(self):
        with self.assertRaises(SystemExit) as caught:
            self.call_main(["match", "--ledger", str(self.ledger)])
        self.assertEqual(caught.exception.code, 2)

    def test_unreadable_ledger_and_bad_category_exit_two_with_a_typed_error(self):
        for argv in (["list", "--ledger", str(self.work / "missing.json")],
                     ["match", "--ledger", str(self.ledger),
                      "--category", "correlation"]):
            with self.subTest(argv=argv):
                out, err = io.StringIO(), io.StringIO()
                with patch.object(sys, "argv", ["lessons.py", *argv]), \
                        contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as caught:
                        lessons.main()
                self.assertEqual(caught.exception.code, 2)
                self.assertIn("LESSON_LOOKUP_ERROR", err.getvalue())

    def test_script_entrypoint_exits_with_the_cli_status(self):
        out = io.StringIO()
        with patch.object(sys, "argv",
                          ["lessons.py", "list", "--ledger", str(self.ledger)]), \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(SCRIPT), run_name="__main__")
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "READ_ONLY_LESSONS")


class TestIntakeAdviceAndMarkdown(unittest.TestCase):
    """The Reporter advisory is exact, fail-closed, and never authoritative."""

    def test_explicit_category_is_validated_even_without_lessons(self):
        result = lessons.intake_advice([], title="t", evidence="e",
                                       failure_category="OBSERVABILITY_CORRELATION")
        self.assertEqual(result["status"], "NO_VERIFIED_MATCH")
        self.assertEqual(result["category"], "OBSERVABILITY_CORRELATION")
        self.assertEqual(result["matches"], [])
        self.assertTrue(result["advisory_only"])
        self.assertFalse(result["production_verified_for_current_incident"])
        with self.assertRaises(ValueError):
            lessons.intake_advice([], title="t", evidence="e",
                                  failure_category="correlation")

    def test_detected_category_requires_one_verbatim_whole_token(self):
        matched = lessons.intake_advice([good_record()], title="repair reporter",
                                        evidence="seen OBSERVABILITY_CORRELATION at intake")
        self.assertEqual(matched["status"], "VERIFIED_MATCH")
        self.assertEqual(matched["category"], "OBSERVABILITY_CORRELATION")
        self.assertEqual([item["incident_id"] for item in matched["matches"]],
                         ["PM-2026-10-01-001"])
        for text in ("no category here", "XOBSERVABILITY_CORRELATIONX",
                     "OBSERVABILITY_CORRELATION_UNVERIFIED"):
            with self.subTest(text=text):
                result = lessons.intake_advice([good_record()], title="repair",
                                               evidence=text)
                self.assertEqual(result["status"], "NO_VERIFIED_MATCH")
                self.assertIsNone(result["category"])

    def test_two_detected_categories_are_ambiguous_not_ranked(self):
        second = copy.deepcopy(good_record())
        second["postmortem_id"] = "PM-2026-10-02-001"
        second["learning"]["failure_category"] = "RELEASE_REVIEW"
        second["engineering_fix_ids"] = ["FIX-2026-10-02-001"]
        second["engineering_fixes"] = {"FIX-2026-10-02-001": {
            "state": "VERIFIED_CLOSED", "merge_commit": "a" * 40,
            "pull_request": 998}}
        result = lessons.intake_advice(
            [good_record(), second], title="two",
            evidence="OBSERVABILITY_CORRELATION and RELEASE_REVIEW")
        self.assertEqual(result["status"], "AMBIGUOUS_CATEGORY")
        self.assertIsNone(result["category"])
        self.assertEqual(result["matches"], [])

    def test_markdown_is_advisory_and_carries_no_authority(self):
        result = lessons.intake_advice([good_record()], title="t",
                                       evidence="OBSERVABILITY_CORRELATION")
        text = lessons.intake_markdown(result)
        self.assertTrue(text.endswith("\n"))
        self.assertIn("## Historical Verified Lessons (advisory only)", text)
        self.assertIn("do not infer current root cause", text)
        self.assertIn("- lookup_status: VERIFIED_MATCH", text)
        self.assertIn("- exact_failure_category: OBSERVABILITY_CORRELATION", text)
        for field in ("prior_incident", "reference_pr", "prior_root_cause",
                      "prior_test_gap", "prior_regression", "prior_prevention"):
            self.assertIn("- " + field + ":", text)
        self.assertIn("/pull/999", text)
        for forbidden in ("authorized", "approved", "waived"):
            self.assertNotIn(forbidden, text)

    def test_markdown_reports_a_miss_without_prior_evidence(self):
        text = lessons.intake_markdown(
            lessons.intake_advice([], title="t", evidence="nothing"))
        self.assertIn("- lookup_status: NO_VERIFIED_MATCH", text)
        self.assertIn("- exact_failure_category: NONE", text)
        self.assertNotIn("prior_incident", text)


if __name__ == "__main__":
    unittest.main()
