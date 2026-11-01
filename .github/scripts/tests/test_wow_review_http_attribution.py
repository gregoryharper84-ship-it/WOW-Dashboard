"""Incident #2136: make the installed reviewer's HTTP failure attributable.

The failed #2291/#2293 runs end at `INDEPENDENT_SECURITY_REVIEW_HOLD: HTTP_404`
with `Reviewer: none`. Nothing recorded which route returned the 404, so GitHub
PR/file/check reads, Anthropic `messages` and Gemini `generateContent` were all
indistinguishable. This is a diagnostics-only change: it adds a fixed
allowlisted endpoint label to the runner log while retaining the original receipt reason. It does
NOT reclassify any status, does not promote a 404 to availability, and cannot
turn a HOLD into a PASS.

The tests below assert that absence of behaviour change directly: a provider
404 still raises a plain Hold (never the availability type), and no URL, query
string, token or response body reaches the emitted label.
"""
import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "wow_simple_security_review",
    ROOT / ".github" / "scripts" / "wow_simple_security_review.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

GITHUB = "https://api.github.com/repos/" + m.REPO
ANTHROPIC = "https://api.anthropic.com/v1/messages"
GEMINI = ("https://generativelanguage.googleapis.com/v1beta/models/"
          "gemini-2.5-flash:generateContent")


def fail(url, status, body, *, data=None, headers=None):
    with patch.object(m.HTTP_CLIENT, "open", side_effect=HTTPError(
            url, status, "error", {}, io.BytesIO(json.dumps(body).encode()))):
        return m.request_json(url, data=data, headers=headers)


class HttpAttributionTests(unittest.TestCase):
    def test_every_review_route_has_a_fixed_allowlisted_label(self):
        cases = (
            (GITHUB + "/pulls/2291", False, "GITHUB_API:GET:PR_METADATA"),
            (GITHUB + "/pulls/2291/files?per_page=100&page=2", False,
             "GITHUB_API:GET:PR_FILES"),
            (GITHUB + "/pulls/2291/reviews?per_page=100", False,
             "GITHUB_API:GET:PR_REVIEWS"),
            (GITHUB + "/commits/" + "a" * 40 + "/check-runs?check_name=x", False,
             "GITHUB_API:GET:CHECK_RUNS_READ"),
            (GITHUB + "/commits/" + "a" * 40, False, "GITHUB_API:GET:COMMIT_METADATA"),
            (GITHUB + "/check-runs", True, "GITHUB_API:POST:CHECK_RUN_CREATE"),
            (GITHUB + "/git/ref/heads/main", False, "GITHUB_API:GET:MAIN_REF"),
            (GITHUB + "/issues/2296/comments?per_page=100", False,
             "GITHUB_API:GET:ISSUE_COMMENTS"),
            (ANTHROPIC, True, "ANTHROPIC_API:POST:MESSAGES"),
            (GEMINI, True, "GEMINI_API:POST:GENERATE_CONTENT"),
        )
        for url, has_body, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(m.safe_label(url, has_body), expected)

    def test_label_never_carries_request_data(self):
        for url in (
            GITHUB + "/pulls/2291/files?per_page=100&secret=TOKEN",
            GEMINI + "?key=SECRET",
            "https://attacker.example/steal?key=SECRET",
        ):
            with self.subTest(url=url):
                label = m.safe_label(url, True)
                self.assertNotIn("SECRET", label)
                self.assertNotIn("?", label)
                self.assertNotIn("attacker.example", label)

    def test_github_404_names_the_failing_github_stage(self):
        for url, expected in (
            (GITHUB + "/pulls/2291", "GITHUB_API:GET:PR_METADATA"),
            (GITHUB + "/commits/" + "a" * 40 + "/check-runs?check_name=WOW",
             "GITHUB_API:GET:CHECK_RUNS_READ"),
            (GITHUB + "/check-runs", "GITHUB_API:POST:CHECK_RUN_CREATE"),
        ):
            with self.subTest(url=url):
                import contextlib
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    with self.assertRaises(m.Hold) as error:
                        fail(url, 404, {"message": "Not Found"},
                             data={} if url.endswith("/check-runs") else None)
                self.assertEqual(str(error.exception), "HTTP_404")
                self.assertIn("REVIEW_HTTP_DIAGNOSTIC:HTTP_404:" + expected,
                              stderr.getvalue())
                self.assertNotIn("Not Found", stderr.getvalue())

    def test_provider_404_stays_a_plain_hold_and_is_attributable(self):
        for url, expected in (
            (ANTHROPIC, "ANTHROPIC_API:POST:MESSAGES"),
            (GEMINI, "GEMINI_API:POST:GENERATE_CONTENT"),
        ):
            with self.subTest(url=url):
                import contextlib
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    with self.assertRaises(m.Hold) as error:
                        fail(url, 404, {"error": {"message": "SECRET provider body"}},
                             data={"model": "claude-sonnet-4-6"}, headers={"x-api-key": "k"})
                self.assertNotIsInstance(error.exception, m.ProviderUnavailable)
                self.assertEqual(str(error.exception), "HTTP_404")
                self.assertIn("REVIEW_HTTP_DIAGNOSTIC:HTTP_404:" + expected,
                              stderr.getvalue())
                self.assertNotIn("SECRET", stderr.getvalue())

    def test_diagnostic_line_is_printed_for_every_http_failure(self):
        import contextlib
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(m.Hold):
                fail(ANTHROPIC, 404, {"error": {"message": "not found"}}, data={})
        self.assertIn(
            "REVIEW_HTTP_DIAGNOSTIC:HTTP_404:ANTHROPIC_API:POST:MESSAGES",
            stderr.getvalue())
        self.assertNotIn("not found", stderr.getvalue())

    def test_transport_and_json_failures_keep_their_original_cause(self):
        from urllib.error import URLError
        with patch.object(m.HTTP_CLIENT, "open", side_effect=URLError("boom")):
            with self.assertRaises(m.Hold) as error:
                m.request_json(GITHUB + "/pulls/2291")
        self.assertEqual(str(error.exception), "GITHUB_TRANSPORT")
        self.assertFalse(hasattr(error.exception, "endpoint"))
        with patch.object(m.HTTP_CLIENT, "open") as opened:
            handle = opened.return_value.__enter__.return_value
            handle.read.return_value = b"not json"
            with self.assertRaises(m.Hold) as bad:
                m.request_json(ANTHROPIC, data={})
        self.assertEqual(str(bad.exception), "RESPONSE_JSON_INVALID")

    def test_untrusted_endpoint_is_refused_before_any_request(self):
        with self.assertRaises(m.Hold) as error:
            m.request_json("https://attacker.example/steal?key=SECRET", data={})
        self.assertEqual(str(error.exception), "REVIEW_ENDPOINT_UNTRUSTED")

    def test_non_http_holds_keep_their_exact_reason(self):
        self.assertEqual(str(m.Hold("SEMANTIC_SCHEMA_INVALID")), "SEMANTIC_SCHEMA_INVALID")
        self.assertEqual(str(m.Hold("HTTP_404")), "HTTP_404")
        self.assertFalse(hasattr(m.Hold("HTTP_404"), "endpoint"))


if __name__ == "__main__":
    unittest.main()
