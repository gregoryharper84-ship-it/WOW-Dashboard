#!/usr/bin/env python3
"""Pre-flight check for the two semantic-review credentials (incident #2136).

Runs the reviewer's own request path against a fixed local sample diff, so a
replacement credential is verified BEFORE it is used on a real PR. The installed
reviewer publishes a HOLD check run onto the PR head on every failed attempt, so
probing here keeps that check-run history clean.

It publishes nothing, writes no file, and prints no key material, header or
response body. Network access is limited to the same two provider endpoints the
reviewer already uses.

Usage:
    # Set the key securely in the environment; never paste it into a shell command or issue.
    python scripts/wow_reviewer_provider_probe.py
    python scripts/wow_reviewer_provider_probe.py --provider anthropic
    python scripts/wow_reviewer_provider_probe.py --provider gemini

Exit status:
    0  every requested provider returned a schema-valid verdict (PASS or REJECT)
    1  at least one provider failed; the typed code is printed
    2  no credential was present for any requested provider
"""
import argparse
import importlib.util
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REVIEWER = REPO_ROOT / ".github" / "scripts" / "wow_simple_security_review.py"

CREDENTIAL_VARS = {
    "anthropic": "WOW_QA_REVIEW_ANTHROPIC_API_KEY",
    "gemini": "WOW_QA_REVIEW_GEMINI_API_KEY",
}
PROVIDER_IDS = {
    "anthropic": "anthropic-claude-api",
    "gemini": "gemini-generative-api",
}
HINTS = {
    "PROVIDER_HTTP_401": "401 = the key was rejected (malformed, expired, revoked, or no access)",
    "PROVIDER_HTTP_403": "403 = access denied; inspect provider policy, project and key permissions",
    "HTTP_403": "403 = access denied; inspect provider policy, project and key permissions",
    "PROVIDER_HTTP_404": "404 = resource not found; check model, API version and account availability",
    "HTTP_404": "404 = resource not found; check model, API version and account availability",
    "PROVIDER_HTTP_429": "429 = rate limited or out of quota",
    "PROVIDER_CREDIT_EXHAUSTED": "the account has no remaining credit",
    "PROVIDER_MODEL_UNAVAILABLE": "the provider reported the model as retired or absent",
    "PROVIDER_TRANSPORT": "the provider endpoint was unreachable from this machine",
    "CREDENTIAL_WHITESPACE": "key contains surrounding whitespace; fix stored secret before testing",
    "PROBE_UNEXPECTED_ERROR": "unexpected local or provider exception; inspect probe setup without logging secrets",
    "CLAUDE_MODEL_UNAPPROVED": "WOW_QA_CLAUDE_MODEL is set to a value the reviewer does not allow",
    "GEMINI_MODEL_INVALID": "WOW_QA_GEMINI_MODEL is set to a value the reviewer does not allow",
}

SAMPLE_DIFF = (
    'diff --git a/sample.py b/sample.py\n'
    '--- a/sample.py\n'
    '+++ b/sample.py\n'
    '@@ -1,2 +1,3 @@\n'
    ' def answer():\n'
    '-    return 1\n'
    '+    # no behaviour change\n'
    '+    return 1\n'
)


def load_reviewer():
    spec = importlib.util.spec_from_file_location("wow_simple_security_review", REVIEWER)
    if spec is None or spec.loader is None:
        raise SystemExit("PROBE_SETUP_FAILED: reviewer module not found at " + str(REVIEWER))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_target():
    return {
        "pr": 1,
        "head_sha": "0" * 40,
        "base_sha": "0" * 40,
        "diff_sha256": "0" * 64,
        "diff": SAMPLE_DIFF,
        "author": "sample",
        "title": "probe sample",
    }


def probe(module, name):
    """Return (ok, code). Never raises for provider failures."""
    env_var = CREDENTIAL_VARS[name]
    key = os.environ.get(env_var, "")
    if not key.strip():
        print(f"PROBE_RESULT:{name}:CREDENTIAL_UNAVAILABLE ({env_var} is unset or blank)")
        return False, "CREDENTIAL_UNAVAILABLE"
    if key != key.strip():
        # The installed reviewer sends the raw stored value. Trimming here would
        # create a false healthy preflight for a broken production credential.
        print(f"PROBE_WARNING:{name}:LEADING_OR_TRAILING_WHITESPACE_IN_CREDENTIAL")
        print(f"PROBE_RESULT:{name}:CREDENTIAL_WHITESPACE")
        return False, "CREDENTIAL_WHITESPACE"
    try:
        decision, _payload = module.call_provider(PROVIDER_IDS[name], key,
                                                  module.prompt_for(sample_target()))
    except module.Hold as exc:
        raw_code = str(exc)
        # Never relay arbitrary provider or future exception strings to stdout.
        # Only typed codes with a closed character set are safe to print.
        code = (raw_code if (raw_code in HINTS or re.fullmatch(
            r"PROVIDER_HTTP_(?:401|408|429|500|502|503|504|529)", raw_code)
            or raw_code in {
                "CLAUDE_REVIEW_INCOMPLETE", "CLAUDE_RESPONSE_INVALID",
                "GEMINI_REVIEW_INCOMPLETE", "GEMINI_RESPONSE_INVALID",
                "CLAUDE_TOOL_USE_MISSING_OR_DUPLICATE",
                "SEMANTIC_RESPONSE_INVALID", "SEMANTIC_SCHEMA_INVALID",
                "SEMANTIC_VERDICT_INVALID", "SEMANTIC_FINDINGS_INVALID",
                "SEMANTIC_RESPONSE_NOT_JSON", "RESPONSE_JSON_INVALID",
            }) else "PROBE_UNEXPECTED_ERROR")
        print(f"PROBE_RESULT:{name}:{code}")
        hint = HINTS.get(code)
        if hint:
            print(f"PROBE_HINT:{name}:{hint}")
        return False, code
    except Exception:
        print(f"PROBE_RESULT:{name}:PROBE_UNEXPECTED_ERROR")
        return False, "PROBE_UNEXPECTED_ERROR"
    # A REJECT on the sample diff is still a healthy provider: it proves transport,
    # authentication and the verdict schema all worked.
    print(f"PROBE_RESULT:{name}:OK (schema-valid verdict, decision={decision})")
    return True, "OK"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=("anthropic", "gemini", "all"), default="all")
    args = parser.parse_args()
    module = load_reviewer()
    names = ("anthropic", "gemini") if args.provider == "all" else (args.provider,)
    print("PROBE_TARGET: reviewer request path at " + str(REVIEWER.relative_to(REPO_ROOT)))
    print("PROBE_NOTE: uses provider API and may incur charges; publishes no GitHub check run.")
    results = [probe(module, name) for name in names]
    if all(code == "CREDENTIAL_UNAVAILABLE" for _ok, code in results):
        print("PROBE_SUMMARY: no credential available to test")
        return 2
    failed = [name for (ok, _code), name in zip(results, names) if not ok]
    if failed:
        print("PROBE_SUMMARY: FAILED " + ",".join(failed))
        return 1
    print("PROBE_SUMMARY: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
