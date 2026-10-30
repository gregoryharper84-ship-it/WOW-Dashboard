"""Incident #2136: attributable HTTP failure vs. typed provider availability.

The installed reviewer reports `HTTP_404` with no endpoint, so the failing
route cannot be identified. Two independent defects are covered here:

1. No fixed endpoint label was emitted for an HTTP failure, so a GitHub 404
   and a provider 404 look identical in the log and decision receipt.
2. Claude's own structured "model is not available" 404 (Anthropic returns
   `not_found_error` with `message: "model: <id>"`) matched none of the
   literal text patterns, was downgraded to a generic HOLD, and therefore
   aborted the sequence before the approved Gemini fallback was tried.

A bare or unmasked 404 must still stay an attributable HOLD: promotion to
availability requires the provider's structured not-found shape naming the
exact approved model. A semantic REJECT is unaffected and stays terminal.
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

ANTHROPIC = "https://api.anthropic.com/v1/messages"
GEMINI = ("https://generativelanguage.googleapis.com/v1beta/models/"
          "gemini-2.5-flash:generateContent")
TARGET = {"pr": 2291, "head_sha": "a" * 40, "base_sha": "b" * 40,
          "diff_sha256": "c" * 64, "diff": "untrusted diff"}


def call(url, status, body, data, headers=None):
    with patch.object(m.HTTP_CLIENT, "open", side_effect=HTTPError(
            url, status, "error", {}, io.BytesIO(json.dumps(body).encode()))):
        return m.request_json(url, data=data, headers=headers)


class ProviderAvailabilityTests(unittest.TestCase):
    def test_anthropic_model_not_found_is_typed_availability(self):
        with self.assertRaises(m.ProviderUnavailable) as error:
            call(ANTHROPIC, 404,
                 {"type": "error",
                  "error": {"type": "not_found_error",
                            "message": "model: claude-sonnet-4-6"}},
                 {"model": "claude-sonnet-4-6"})
        self.assertEqual(str(error.exception), "PROVIDER_MODEL_UNAVAILABLE")

    def test_gemini_model_not_found_is_typed_availability(self):
        with self.assertRaises(m.ProviderUnavailable) as error:
            call(GEMINI, 404,
                 {"error": {"code": 404, "status": "NOT_FOUND",
                            "message": "models/gemini-2.5-flash is not found "
                                       "for API version v1beta"}},
                 {})
        self.assertEqual(str(error.exception), "PROVIDER_MODEL_UNAVAILABLE")

    def test_typed_model_unavailable_reaches_the_alternate_reviewer(self):
        calls = []

        def invoke(provider, key, prompt):
            calls.append(provider)
            if provider == m.PROVIDERS[0]:
                return call(ANTHROPIC, 404,
                            {"type": "error",
                             "error": {"type": "not_found_error",
                                       "message": "model: claude-sonnet-4-6"}},
                            {"model": "claude-sonnet-4-6"})
            return "PASS", {"findings": []}

        decision, _, provider, attempts = m.semantic_review(
            TARGET, {name: "test" for name in m.PROVIDERS}, invoke)
        self.assertEqual(calls, list(m.PROVIDERS))
        self.assertEqual(decision, "PASS")
        self.assertEqual(provider, m.PROVIDERS[1])
        self.assertEqual([item["result"] for item in attempts],
                         ["PROVIDER_MODEL_UNAVAILABLE", "PASS"])

    def test_bare_or_mismatched_404_stays_an_attributable_hold(self):
        cases = (
            (ANTHROPIC, {"error": {"message": "Not Found"}},
             {"model": "claude-sonnet-4-6"}, "HTTP_404:ANTHROPIC_API:POST:MESSAGES"),
            (ANTHROPIC, {"error": {"type": "not_found_error", "message": "Not Found"}},
             {"model": "claude-sonnet-4-6"}, "HTTP_404:ANTHROPIC_API:POST:MESSAGES"),
            (ANTHROPIC, {"error": {"type": "not_found_error",
                                   "message": "model: claude-3-opus-20240229"}},
             {"model": "claude-sonnet-4-6"}, "HTTP_404:ANTHROPIC_API:POST:MESSAGES"),
            (ANTHROPIC, {"error": {"message": "model: claude-sonnet-4-6"}},
             {"model": "claude-sonnet-4-6"}, "HTTP_404:ANTHROPIC_API:POST:MESSAGES"),
            (GEMINI, {"error": {"code": 404, "status": "NOT_FOUND",
                                "message": "models/gemini-2.0-flash is not found"}},
             {}, "HTTP_404:GEMINI_API:POST:GENERATE_CONTENT"),
            (GEMINI, {"error": {"code": 404,
                                "message": "models/gemini-2.5-flash is not found"}},
             {}, "HTTP_404:GEMINI_API:POST:GENERATE_CONTENT"),
        )
        for url, body, data, expected in cases:
            with self.subTest(url=url, body=body):
                with self.assertRaises(m.Hold) as error:
                    call(url, 404, body, data)
                self.assertNotIsInstance(error.exception, m.ProviderUnavailable)
                self.assertEqual(str(error.exception), expected)

    def test_ambiguous_provider_404_never_advances_to_the_backup(self):
        calls = []

        def invoke(provider, key, prompt):
            calls.append(provider)
            return call(ANTHROPIC, 404,
                        {"error": {"message": "Not Found"}}, {})

        with self.assertRaises(m.Hold) as error:
            m.semantic_review(TARGET, {name: "test" for name in m.PROVIDERS}, invoke)
        self.assertNotIsInstance(error.exception, m.ProviderUnavailable)
        self.assertEqual(calls, [m.PROVIDERS[0]])

    def test_endpoint_labels_are_fixed_and_never_echo_request_data(self):
        github = "https://api.github.com/repos/" + m.REPO
        cases = (
            (github + "/pulls/2291", False, "GITHUB_API:GET:PR_METADATA"),
            (github + "/pulls/2291/files?per_page=100&secret=TOKEN", False,
             "GITHUB_API:GET:PR_FILES"),
            (github + "/commits/" + "a" * 40 + "/check-runs?check_name=SECRET",
             False, "GITHUB_API:GET:CHECK_RUNS_READ"),
            (github + "/check-runs", True, "GITHUB_API:POST:CHECK_RUN_CREATE"),
            (github + "/git/ref/heads/main", False, "GITHUB_API:GET:MAIN_REF"),
            (ANTHROPIC, True, "ANTHROPIC_API:POST:MESSAGES"),
            (GEMINI + "?key=SECRET", True, "GEMINI_API:POST:GENERATE_CONTENT"),
            ("https://attacker.example/steal?key=SECRET", True,
             "UNKNOWN_API:POST:UNCLASSIFIED"),
        )
        for url, has_body, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(m.safe_label(url, has_body), expected)
                self.assertNotIn("SECRET", m.safe_label(url, has_body))

    def test_untrusted_endpoint_is_never_requested(self):
        with self.assertRaises(m.Hold) as error:
            m.request_json("https://attacker.example/steal?key=SECRET", data={})
        self.assertEqual(str(error.exception), "REVIEW_ENDPOINT_UNTRUSTED")


if __name__ == "__main__":
    unittest.main()
