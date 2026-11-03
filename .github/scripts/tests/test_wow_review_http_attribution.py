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

    def test_structured_gemini_legacy_model_404_is_typed_unavailable(self):
        payload = {"error": {"code": 404, "status": "NOT_FOUND",
                   "message": "models/gemini-2.5-flash is not found for API version v1beta, "
                              "or is not supported for generateContent"}}
        with self.assertRaises(m.ProviderUnavailable) as unavailable:
            fail(GEMINI, 404, payload, data={"contents": []})
        self.assertEqual(str(unavailable.exception), "PROVIDER_MODEL_UNAVAILABLE")

    def test_ambiguous_gemini_404_never_becomes_typed_fallback(self):
        message = ("models/gemini-2.5-flash is not found for API version v1beta, "
                   "or is not supported for generateContent")
        for body in ({"error": {"message": message}},
                     {"error": {"code": 404, "status": "INVALID_ARGUMENT", "message": message}},
                     {"error": {"code": 404, "status": "NOT_FOUND", "message": "arbitrary 404"}},
                     [], {"error": "malformed"}):
            with self.subTest(body=body):
                with self.assertRaises(m.Hold) as hold:
                    fail(GEMINI, 404, body, data={"contents": []})
                self.assertNotIsInstance(hold.exception, m.ProviderUnavailable)
                self.assertEqual(str(hold.exception), "HTTP_404")

    def test_safe_preflight_get_metadata_endpoint_label(self):
        metadata_url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                        "gemini-3.8-flash")
        self.assertEqual(m.safe_label(metadata_url, False), "GEMINI_API:GET:MODEL_METADATA")
        self.assertNotIn("secret", m.safe_label(metadata_url + "?secret=hidden", False))
        with self.assertRaises(m.Hold):
            fail(metadata_url, 404, {"error": {"code": 404, "status": "NOT_FOUND",
                                              "message": "model not available"}})
        # A credential-safe metadata GET cannot become a semantic-review PASS.

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

    def test_ambiguous_provider_rejection_names_category_and_parameter(self):
        """A Claude 400 must be attributable without changing its decision."""
        import contextlib
        body = {"error": {"type": "invalid_request_error",
                          "param": "tools.0.input_schema",
                          "message": "SECRET provider detail"}}
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(m.Hold) as error:
                fail(ANTHROPIC, 400, body, data={"model": "claude-sonnet-4-6"},
                     headers={"x-api-key": "k"})
        self.assertNotIsInstance(error.exception, m.ProviderUnavailable)
        self.assertEqual(str(error.exception), "HTTP_400")
        self.assertIn("REVIEW_HTTP_PROVIDER_DIAGNOSTIC:HTTP_400:"
                      "ANTHROPIC_API:POST:MESSAGES:"
                      "ERROR_TYPE_INVALID_REQUEST_ERROR:PARAM_TOOLS",
                      stderr.getvalue())
        self.assertNotIn("SECRET", stderr.getvalue())
        self.assertNotIn("input_schema", stderr.getvalue())

    def test_gemini_structured_rejection_status_is_diagnostic_only(self):
        import contextlib
        body = {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                          "message": "SECRET provider detail"}}
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(m.Hold) as error:
                fail(GEMINI, 400, body, data={"contents": []})
        self.assertNotIsInstance(error.exception, m.ProviderUnavailable)
        self.assertEqual(str(error.exception), "HTTP_400")
        self.assertIn("REVIEW_HTTP_PROVIDER_DIAGNOSTIC:HTTP_400:"
                      "GEMINI_API:POST:GENERATE_CONTENT:STATUS_INVALID_ARGUMENT",
                      stderr.getvalue())
        self.assertNotIn("SECRET", stderr.getvalue())

    def test_unknown_provider_rejection_text_is_never_emitted(self):
        import contextlib
        body = {"error": {"type": "SECRET_TYPE", "param": "SECRET_PARAM",
                          "status": "SECRET_STATUS", "message": "SECRET"}}
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(m.Hold):
                fail(ANTHROPIC, 400, body, data={})
        output = stderr.getvalue()
        self.assertIn("ERROR_TYPE_UNCLASSIFIED:PARAM_UNCLASSIFIED:"
                      "STATUS_UNCLASSIFIED", output)
        self.assertNotIn("SECRET", output)

    def test_non_object_provider_rejection_body_stays_unclassified(self):
        self.assertEqual(m.provider_rejection_label([]), "ERROR_UNCLASSIFIED")
        self.assertEqual(m.provider_rejection_label({"error": "malformed"}),
                         "ERROR_UNCLASSIFIED")
        self.assertEqual(m.provider_rejection_label({"error": {"type": "x"}}),
                         "ERROR_TYPE_UNCLASSIFIED")

    def test_truncated_provider_error_body_keeps_status_based_decision(self):
        """A broken chunk must not interrupt 429/503 reviewer-provider fallback."""
        import contextlib
        from http.client import IncompleteRead

        class BrokenBody:
            def read(self, _limit):
                raise IncompleteRead(b"SECRET PARTIAL BODY", 100)

        for url, code, expected in (
            (ANTHROPIC, 429, "PROVIDER_HTTP_429"),
            (GEMINI, 503, "PROVIDER_HTTP_503"),
        ):
            with self.subTest(url=url, status=code):
                error = HTTPError(url, code, "broken", {}, BrokenBody())
                stderr = io.StringIO()
                with patch.object(m.HTTP_CLIENT, "open", side_effect=error):
                    with contextlib.redirect_stderr(stderr):
                        with self.assertRaises(m.ProviderUnavailable) as outcome:
                            m.request_json(url, data={"model": "approved"}, headers={})
                self.assertEqual(str(outcome.exception), expected)
                self.assertIn("ERROR_UNCLASSIFIED", stderr.getvalue())
                self.assertNotIn("SECRET", stderr.getvalue())
                self.assertNotIn("PARTIAL BODY", stderr.getvalue())

    def test_broken_diagnostic_body_keeps_typed_status_and_fails_closed(self):
        """HTTPException and deep JSON must not change provider fallback typing."""
        import contextlib
        from http.client import LineTooLong

        class BrokenBody:
            def __init__(self, error):
                self.error = error

            def read(self, _limit):
                raise self.error

        for error in (LineTooLong("SECRET malformed chunk"),
                      RecursionError("SECRET deeply nested JSON")):
            for url, code, expected in (
                (ANTHROPIC, 429, "PROVIDER_HTTP_429"),
                (GEMINI, 503, "PROVIDER_HTTP_503"),
            ):
                with self.subTest(code=code, error=type(error).__name__):
                    denied = HTTPError(url, code, "SECRET", {}, BrokenBody(error))
                    output = io.StringIO()
                    with patch.object(m.HTTP_CLIENT, "open", side_effect=denied):
                        with contextlib.redirect_stderr(output):
                            with self.assertRaises(m.ProviderUnavailable) as result:
                                m.request_json(url, data={"model": "approved"})
                    self.assertEqual(str(result.exception), expected)
                    self.assertIn("REVIEW_HTTP_PROVIDER_DIAGNOSTIC:", output.getvalue())
                    self.assertNotIn("SECRET", output.getvalue())

    def test_github_403_body_http_exception_remains_hold_without_retry(self):
        import contextlib
        from http.client import LineTooLong

        class BrokenBody:
            def read(self, _limit):
                raise LineTooLong("SECRET malformed chunk")

        url = GITHUB + "/pulls/2382"
        denied = HTTPError(url, 403, "SECRET", {}, BrokenBody())
        output = io.StringIO()
        with patch.object(m.HTTP_CLIENT, "open", side_effect=denied) as opened:
            with patch.object(m.time, "sleep") as sleep:
                with contextlib.redirect_stderr(output):
                    with self.assertRaises(m.Hold) as result:
                        m.request_json(url, token="SECRET_TOKEN")
        self.assertEqual(str(result.exception), "HTTP_403")
        self.assertEqual(opened.call_count, 1)
        sleep.assert_not_called()
        self.assertIn("GITHUB_API:GET:PR_METADATA:UNCLASSIFIED", output.getvalue())
        self.assertNotIn("SECRET", output.getvalue())

    def test_rate_limit_rejection_is_attributed_and_still_fallback_eligible(self):
        import contextlib
        body = {"type": "error",
                "error": {"type": "rate_limit_error", "message": "SECRET"}}
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(m.ProviderUnavailable) as error:
                fail(ANTHROPIC, 429, body, data={"model": "claude-sonnet-4-6"},
                     headers={"x-api-key": "k"})
        self.assertEqual(str(error.exception), "PROVIDER_HTTP_429")
        self.assertIn("REVIEW_HTTP_PROVIDER_DIAGNOSTIC:HTTP_429:"
                      "ANTHROPIC_API:POST:MESSAGES:"
                      "ERROR_TYPE_RATE_LIMIT_ERROR", stderr.getvalue())
        self.assertNotIn("SECRET", stderr.getvalue())

    def test_upstream_5xx_is_attributed_without_changing_fallback(self):
        import contextlib
        for status, url, body, data, expected in (
            (529, ANTHROPIC, {"error": {"type": "overloaded_error"}},
             {"model": "claude-sonnet-4-6"}, "ERROR_TYPE_OVERLOADED_ERROR"),
            (503, GEMINI, {"error": {"code": 503, "status": "UNAVAILABLE"}},
             {"contents": []}, "STATUS_UNAVAILABLE"),
        ):
            with self.subTest(status=status):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    with self.assertRaises(m.ProviderUnavailable) as error:
                        fail(url, status, body, data=data, headers={})
                self.assertEqual(str(error.exception),
                                 "PROVIDER_HTTP_" + str(status))
                self.assertIn("REVIEW_HTTP_PROVIDER_DIAGNOSTIC:HTTP_"
                              + str(status) + ":", stderr.getvalue())
                self.assertIn(expected, stderr.getvalue())

    def test_non_retryable_5xx_stays_hold_even_when_attributed(self):
        import contextlib
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(m.Hold) as error:
                fail(ANTHROPIC, 501, {"error": {"type": "api_error"}}, data={})
        self.assertNotIsInstance(error.exception, m.ProviderUnavailable)
        self.assertEqual(str(error.exception), "HTTP_501")
        self.assertIn("HTTP_501:ANTHROPIC_API:POST:MESSAGES:"
                      "ERROR_TYPE_API_ERROR", stderr.getvalue())

    def test_github_5xx_is_never_labelled_with_provider_vocabulary(self):
        import contextlib
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(m.Hold) as error:
                fail(GITHUB + "/pulls/2291", 502, {"message": "Bad Gateway"})
        self.assertEqual(str(error.exception), "HTTP_502")
        self.assertNotIn("REVIEW_HTTP_PROVIDER_DIAGNOSTIC", stderr.getvalue())
        self.assertIn("REVIEW_HTTP_DIAGNOSTIC:HTTP_502:"
                      "GITHUB_API:GET:PR_METADATA", stderr.getvalue())

    def test_provider_metadata_get_rate_limit_is_attributed_but_not_fallback(self):
        import contextlib
        for url, label, body, expected in (
            ("https://api.anthropic.com/v1/models?limit=1000",
             "ANTHROPIC_API:GET:MODEL_CATALOG",
             {"error": {"type": "rate_limit_error"}},
             "ERROR_TYPE_RATE_LIMIT_ERROR"),
            ("https://generativelanguage.googleapis.com/v1beta/models"
             "/gemini-3.8-flash", "GEMINI_API:GET:MODEL_METADATA",
             {"error": {"code": 503, "status": "UNAVAILABLE"}},
             "STATUS_UNAVAILABLE"),
        ):
            with self.subTest(url=url):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    with self.assertRaises(m.Hold) as error:
                        fail(url, 429 if "anthropic" in url else 503, body)
                self.assertNotIsInstance(error.exception, m.ProviderUnavailable)
                self.assertIn("REVIEW_HTTP_PROVIDER_DIAGNOSTIC:",
                              stderr.getvalue())
                self.assertIn(label + ":" + expected, stderr.getvalue())

    def test_attribution_composes_with_gemini_429_model_rollover(self):
        """#2382 rotates on 429; the diagnostic must label each attempt, not block it."""
        import contextlib
        verdict = {"source_review": "PASS", "security_review": "PASS",
                   "probability_integrity_review": "PASS", "findings": []}
        good = json.dumps({"candidates": [{"finishReason": "STOP", "content": {
            "parts": [{"text": json.dumps(verdict)}]}}]}).encode()
        rate_limited = json.dumps({"error": {"code": 429,
                                             "status": "RESOURCE_EXHAUSTED",
                                             "message": "SECRET"}}).encode()
        calls = []

        def fake_open(request, timeout=None):
            calls.append(request.full_url)
            if "gemini-3.8-flash" in request.full_url:
                raise HTTPError(request.full_url, 429, "rate limited", {},
                                io.BytesIO(rate_limited))
            return contextlib.nullcontext(
                type("R", (), {"read": lambda self, n: good})())

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), \
             patch.dict(m.os.environ, {"WOW_QA_GEMINI_MODEL": ""}), \
             patch.object(m, "review_models",
                          return_value=["gemini-3.8-flash", "gemini-3.6-flash"]), \
             patch.object(m, "HTTP_CLIENT") as client:
            client.open.side_effect = fake_open
            decision, payload = m.call_provider("gemini-generative-api", "key", "diff")
        self.assertEqual(decision, "PASS")
        self.assertEqual(payload, verdict)
        self.assertEqual(len(calls), 2)
        self.assertIn("gemini-3.8-flash", calls[0])
        self.assertIn("gemini-3.6-flash", calls[1])
        output = stderr.getvalue()
        self.assertIn("REVIEW_HTTP_PROVIDER_DIAGNOSTIC:HTTP_429:"
                      "GEMINI_API:POST:GENERATE_CONTENT:STATUS_RESOURCE_EXHAUSTED",
                      output)
        self.assertIn("REVIEW_GEMINI_MODEL_RATE_LIMITED:gemini-3.8-flash", output)
        self.assertNotIn("SECRET", output)

    def test_429_without_structured_body_is_still_typed_unavailable(self):
        import contextlib
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(m.ProviderUnavailable) as error:
                fail(ANTHROPIC, 429, [], data={})
        self.assertEqual(str(error.exception), "PROVIDER_HTTP_429")
        self.assertIn("HTTP_429:ANTHROPIC_API:POST:MESSAGES:ERROR_UNCLASSIFIED",
                      stderr.getvalue())

    def test_github_403_distinguishes_rate_limit_permission_and_unknown(self):
        """Diagnostic-only evidence, never a retry, approval, or raw-header log."""
        import contextlib
        cases = (
            ({"X-RateLimit-Remaining": "0"}, {"message": "SECRET"},
             "PRIMARY_RATE_LIMIT"),
            ({}, {"message": "You have exceeded a secondary rate limit. SECRET"},
             "SECONDARY_RATE_LIMIT"),
            ({}, {"message": "Resource not accessible by integration"},
             "CREDENTIAL_PERMISSION_DENIED"),
            ({"Retry-After": "120"}, {"message": "SECRET"},
             "RETRY_AFTER_PRESENT"),
            ({}, {"message": "SECRET unknown GitHub denial"}, "UNCLASSIFIED"),
        )
        url = GITHUB + "/pulls/2375"
        for headers, body, label in cases:
            with self.subTest(label=label):
                error = HTTPError(url, 403, "SECRET HTTP reason", headers,
                                  io.BytesIO(json.dumps(body).encode()))
                output = io.StringIO()
                with patch.object(m.HTTP_CLIENT, "open", side_effect=error):
                    with contextlib.redirect_stderr(output):
                        with self.assertRaises(m.Hold) as raised:
                            m.request_json(url, token="SECRET_TOKEN")
                self.assertNotIsInstance(raised.exception, m.ProviderUnavailable)
                self.assertEqual(str(raised.exception), "HTTP_403")
                self.assertIn("REVIEW_HTTP_GITHUB_DIAGNOSTIC:HTTP_403:"
                              "GITHUB_API:GET:PR_METADATA:" + label, output.getvalue())
                self.assertNotIn("SECRET", output.getvalue())
                self.assertNotIn("120", output.getvalue())
                self.assertNotIn(url, output.getvalue())

    def test_github_read_only_rate_limit_retries_once_with_exact_auth(self):
        """A proved GitHub rate limit gets one paced GET, never a silent PASS."""
        import contextlib
        url = GITHUB + "/pulls/2382"
        for reason_headers, message in (
            ({"X-RateLimit-Remaining": "0"}, "SECRET exhaustion"),
            ({}, "You have exceeded a secondary rate limit. SECRET"),
        ):
            with self.subTest(headers=reason_headers):
                denied = HTTPError(url, 403, "SECRET", reason_headers,
                                   io.BytesIO(json.dumps({"message": message}).encode()))
                success = io.BytesIO(b'{"head":{"sha":"abc"}}')
                output = io.StringIO()
                with patch.object(m.HTTP_CLIENT, "open",
                                  side_effect=[denied, success]) as opened:
                    with patch.object(m.time, "sleep") as sleep:
                        with contextlib.redirect_stderr(output):
                            result = m.request_json(url, token="SECRET_TOKEN")
                self.assertEqual(result, {"head": {"sha": "abc"}})
                self.assertEqual(opened.call_count, 2)
                sleep.assert_called_once_with(60)
                self.assertEqual(opened.call_args_list[0].args[0].get_header(
                    "Authorization"), "Bearer SECRET_TOKEN")
                self.assertEqual(opened.call_args_list[1].args[0].get_header(
                    "Authorization"), "Bearer SECRET_TOKEN")
                self.assertIn("REVIEW_GITHUB_READ_RETRY_ONCE:", output.getvalue())
                self.assertNotIn("SECRET", output.getvalue())
                self.assertNotIn(url, output.getvalue())

    def test_github_get_second_rate_limit_fails_closed_without_third_attempt(self):
        import contextlib
        url = GITHUB + "/pulls/2382"
        def limit():
            return HTTPError(url, 403, "SECRET",
                             {"X-RateLimit-Remaining": "0"},
                             io.BytesIO(b'{"message":"rate limit"}'))
        output = io.StringIO()
        with patch.object(m.HTTP_CLIENT, "open",
                          side_effect=[limit(), limit()]) as opened:
            with patch.object(m.time, "sleep") as sleep:
                with contextlib.redirect_stderr(output):
                    with self.assertRaises(m.Hold) as err:
                        m.request_json(url, token="SECRET_TOKEN")
        self.assertEqual(str(err.exception), "HTTP_403")
        self.assertEqual(opened.call_count, 2)
        sleep.assert_called_once_with(60)
        self.assertEqual(output.getvalue().count("REVIEW_GITHUB_READ_RETRY_ONCE:"), 1)

    def test_github_permission_or_ambiguous_403_never_retries(self):
        import contextlib
        url = GITHUB + "/pulls/2382"
        for body in (
            {"message": "Resource not accessible by integration"},
            {"message": "SECRET unknown 403"},
        ):
            with self.subTest(body=body):
                denied = HTTPError(url, 403, "SECRET", {},
                                   io.BytesIO(json.dumps(body).encode()))
                with patch.object(m.HTTP_CLIENT, "open",
                                  side_effect=denied) as opened:
                    with patch.object(m.time, "sleep") as sleep:
                        with contextlib.redirect_stderr(io.StringIO()):
                            with self.assertRaises(m.Hold) as err:
                                m.request_json(url, token="SECRET_TOKEN")
                self.assertEqual(str(err.exception), "HTTP_403")
                self.assertEqual(opened.call_count, 1)
                sleep.assert_not_called()

    def test_github_check_publication_never_retries_rate_limit(self):
        import contextlib
        url = GITHUB + "/check-runs"
        denied = HTTPError(url, 429, "SECRET", {},
                           io.BytesIO(b'{"message":"secondary rate limit"}'))
        with patch.object(m.HTTP_CLIENT, "open", side_effect=denied) as opened:
            with patch.object(m.time, "sleep") as sleep:
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(m.Hold) as err:
                        m.request_json(url, token="SECRET_TOKEN", data={"name": "check"})
        self.assertEqual(str(err.exception), "HTTP_429")
        self.assertEqual(opened.call_count, 1)
        sleep.assert_not_called()

    def test_github_429_keeps_hold_and_reports_secondary_limit(self):
        import contextlib
        url = GITHUB + "/pulls/2375"
        error = HTTPError(
            url, 429, "SECRET", {},
            io.BytesIO(json.dumps({
                "message": "You have exceeded a secondary rate limit. SECRET"
            }).encode()))
        output = io.StringIO()
        with patch.object(m.HTTP_CLIENT, "open", side_effect=error):
            with contextlib.redirect_stderr(output):
                with self.assertRaises(m.Hold) as raised:
                    m.request_json(url, token="SECRET_TOKEN")
        self.assertNotIsInstance(raised.exception, m.ProviderUnavailable)
        self.assertEqual(str(raised.exception), "HTTP_429")
        self.assertIn("REVIEW_HTTP_GITHUB_DIAGNOSTIC:HTTP_429:"
                      "GITHUB_API:GET:PR_METADATA:SECONDARY_RATE_LIMIT",
                      output.getvalue())
        self.assertNotIn("SECRET", output.getvalue())

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
