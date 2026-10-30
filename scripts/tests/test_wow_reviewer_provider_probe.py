"""Offline regression tests for the private, non-publishing provider preflight."""
import contextlib
import importlib.util
import io
import os
import unittest
from pathlib import Path
from unittest.mock import patch

PROBE_FILE = Path(__file__).resolve().parents[1] / "wow_reviewer_provider_probe.py"
spec = importlib.util.spec_from_file_location("wow_reviewer_provider_probe", PROBE_FILE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeHold(Exception):
    pass


class FakeReviewer:
    Hold = FakeHold

    def __init__(self, decision="PASS", error=None):
        self.calls = []
        self.decision = decision
        self.error = error

    def prompt_for(self, target):
        assert target["diff"] == probe.SAMPLE_DIFF
        return "fixed test sample"

    def call_provider(self, provider, credential, prompt):
        self.calls.append((provider, credential, prompt))
        if self.error is not None:
            raise self.error
        return self.decision, {"source_review": self.decision}


class ProbeTests(unittest.TestCase):
    def execute(self, provider, key, reviewer=None):
        reviewer = reviewer or FakeReviewer()
        stream = io.StringIO()
        with patch.dict(os.environ, {probe.CREDENTIAL_VARS[provider]: key}), contextlib.redirect_stdout(stream):
            ok, reason = probe.probe(reviewer, provider)
        return ok, reason, stream.getvalue(), reviewer.calls

    def test_blank_credential_makes_zero_requests(self):
        ok, code, output, calls = self.execute("anthropic", "  ")
        self.assertFalse(ok)
        self.assertEqual(code, "CREDENTIAL_UNAVAILABLE")
        self.assertEqual(calls, [])
        self.assertNotIn("  ", output)

    def test_whitespace_is_not_trimmed_into_a_false_healthy_result(self):
        for key in (" sk-test", "sk-test\\n", "\\tsk-test"):
            with self.subTest(secret=repr(key[:1])):
                ok, code, output, calls = self.execute("anthropic", key)
                self.assertFalse(ok)
                self.assertEqual(code, "CREDENTIAL_WHITESPACE")
                self.assertEqual(calls, [])
                self.assertIn("LEADING_OR_TRAILING_WHITESPACE", output)
                self.assertNotIn("sk-test", output)

    def test_valid_approved_provider_verdict_is_healthy_including_reject(self):
        for decision in ("PASS", "REJECT"):
            with self.subTest(decision=decision):
                ok, code, output, calls = self.execute("gemini", "secret-value",
                                                       FakeReviewer(decision=decision))
                self.assertTrue(ok)
                self.assertEqual(code, "OK")
                self.assertEqual(calls[0][1], "secret-value")
                self.assertIn("PROBE_RESULT:gemini:OK", output)
                self.assertNotIn("secret-value", output)

    def test_typed_provider_401_does_not_disclose_key(self):
        reviewer = FakeReviewer(error=FakeHold("PROVIDER_HTTP_401"))
        ok, code, output, calls = self.execute("anthropic", "key-private-abc", reviewer)
        self.assertFalse(ok)
        self.assertEqual(code, "PROVIDER_HTTP_401")
        self.assertEqual(len(calls), 1)
        self.assertIn("PROBE_HINT:anthropic:401", output)
        self.assertNotIn("key-private-abc", output)

    def test_plain_404_is_neutral_and_not_mislabeled_as_entitlement(self):
        reviewer = FakeReviewer(error=FakeHold("HTTP_404"))
        ok, code, output, calls = self.execute("gemini", "some-key", reviewer)
        self.assertFalse(ok)
        self.assertEqual(code, "HTTP_404")
        self.assertIn("resource not found", output)
        self.assertNotIn("entitlement, not auth", output)

    def test_untrusted_exception_text_never_reaches_stdout(self):
        for error in (FakeHold("SECRET_KEY=do-not-print"), RuntimeError("secret-value")):
            with self.subTest(error_type=type(error).__name__):
                ok, code, output, _ = self.execute("anthropic", "key-private", FakeReviewer(error=error))
                self.assertFalse(ok)
                self.assertEqual(code, "PROBE_UNEXPECTED_ERROR")
                self.assertNotIn("do-not-print", output)
                self.assertNotIn("secret-value", output)
                self.assertNotIn("key-private", output)


if __name__ == "__main__":
    unittest.main()
