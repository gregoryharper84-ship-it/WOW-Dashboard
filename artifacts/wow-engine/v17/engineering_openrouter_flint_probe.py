"""Inert, fail-closed Flint *transport* canary, not a V17 scorer or agent.

One public fixed synthetic prompt only. A real request is impossible without
an authenticated, approved, manual GitHub Actions workflow run identity.
No local environment toggle can substitute for that GitHub API readback.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Mapping

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "recursive/flint-v1.0:free"
PROMPT = "Write a one-sentence test case for a Python even-number predicate. Do not use tools."
# This protected workflow does NOT exist on main yet. Until its independent
# governance adoption, even a secret-bearing local shell cannot run the smoke.
APPROVED_WORKFLOW_REF = (
    "gregoryharper84-ship-it/WOW-Dashboard/"
    ".github/workflows/wow-v17-flint-engineering-worker.yml@refs/heads/main"
)
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
POSITIVE_INT = re.compile(r"^[1-9][0-9]*$")
MAX_BODY = 65536
MAX_ID = 128


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """No GET/POST, body, or credential may follow an upstream redirect."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _opener():
    return urllib.request.build_opener(NoRedirect()).open


def receipt(status: str, *, attempted: bool = False,
            model_claim_matched: bool = False, usage: Any = None) -> dict[str, Any]:
    # These are NON-AUTHORITATIVE research facts. This module has no
    # V17 terminal reducer, fitted scorer, order writer or release permission.
    # No caller may treat these constants as V17 runtime certification.
    result: dict[str, Any] = {
        "status": status,
        "provider": "openrouter",
        "requested_model": MODEL,
        "provider_model_claim_matched": model_claim_matched,
        "network_attempted": attempted,
        "production_verified": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }
    if isinstance(usage, dict) and status == "SMOKE_PASSED":
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(field)
            if type(value) is int and 0 <= value <= 100000:
                result[field] = value
    return result


def preflight(env: Mapping[str, str]) -> dict[str, Any]:
    if env.get("WOW_FLINT_PROBE_ENABLED") != "true":
        return receipt("DISABLED")
    if not env.get("OPENROUTER_API_KEY", "").strip():
        return receipt("KEY_MISSING")
    if env.get("WOW_FLINT_EXTERNAL_CONSENT") != "true":
        return receipt("CONSENT_MISSING")
    if (env.get("GITHUB_ACTIONS") != "true"
            or env.get("GITHUB_REPOSITORY") != REPO
            or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_WORKFLOW_REF") != APPROVED_WORKFLOW_REF
            or not HEX40.fullmatch(env.get("GITHUB_SHA", ""))
            or not POSITIVE_INT.fullmatch(env.get("GITHUB_RUN_ID", ""))
            or not env.get("GITHUB_TOKEN", "").strip()):
        return receipt("HOST_IDENTITY_UNVERIFIED")
    # NOT success: GitHub's live run must still be read and verified.
    return receipt("HOST_READBACK_REQUIRED")


def _open(request: urllib.request.Request, opener: Any, timeout: int) -> bytes:
    try:
        with opener(request, timeout=timeout) as response:
            # Deny followed redirects even for injected/misconfigured openers.
            if getattr(response, "url", request.full_url) != request.full_url:
                raise ValueError("REDIRECTED_RESPONSE")
            data = response.read(MAX_BODY + 1)
    finally:
        request.remove_header("Authorization")
    if len(data) > MAX_BODY:
        raise ValueError("OVERSIZED_RESPONSE")
    return data


def verify_host(env: Mapping[str, str], opener: Any) -> bool:
    # GitHub API readback establishes run existence, caller workflow, source
    # SHA, event and repository. A forged env alone cannot authorize egress.
    req = urllib.request.Request(
        "https://api.github.com/repos/" + REPO + "/actions/runs/" + env["GITHUB_RUN_ID"],
        headers={"Accept": "application/vnd.github+json"},
        method="GET",
    )
    req.add_unredirected_header("Authorization", "Bearer " + env["GITHUB_TOKEN"].strip())
    try:
        payload = json.loads(_open(req, opener, 12))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError,
            UnicodeDecodeError, TypeError):
        return False
    if type(payload) is not dict:
        return False
    repo = payload.get("repository")
    return (
        type(repo) is dict and repo.get("full_name") == REPO
        and payload.get("id") == int(env["GITHUB_RUN_ID"])
        and payload.get("event") == "workflow_dispatch"
        and payload.get("head_sha") == env["GITHUB_SHA"]
        and payload.get("head_branch") == "main"
        and payload.get("path") == APPROVED_WORKFLOW_REF
        and payload.get("status") in ("queued", "in_progress", "waiting", "requested")
    )


def classify_http_status(status: int) -> str:
    if status in (301, 302, 303, 307, 308):
        return "REDIRECT_DENIED"
    if status in (401, 403):
        return "AUTH_FAILED"
    if status == 402:
        return "PAYMENT_REQUIRED"
    if status == 429:
        return "RATE_LIMITED"
    if status == 404:
        return "FLINT_HTTP_404_UNATTRIBUTED"
    if status >= 500:
        return "UPSTREAM_FAILURE"
    return "HTTP_FAILURE"


def validate_completion(payload: Any) -> dict[str, Any]:
    if type(payload) is not dict:
        return receipt("OUTPUT_INVALID", attempted=True)
    # An echoed model field is a provider ASSERTION, not independent proof
    # of backend weights, calibration or actual worker execution.
    if (payload.get("model") != MODEL or type(payload.get("id")) is not str
            or not 1 <= len(payload["id"]) <= MAX_ID):
        return receipt("MODEL_IDENTITY_UNVERIFIED", attempted=True)
    choices = payload.get("choices")
    if (type(choices) is not list or len(choices) != 1
            or type(choices[0]) is not dict):
        return receipt("OUTPUT_INVALID", attempted=True)
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        return receipt("FLINT_MODEL_COMPLETION_INCOMPLETE", attempted=True)
    message = choice.get("message")
    content = message.get("content") if type(message) is dict else None
    if type(content) is not str or not content.strip():
        return receipt("OUTPUT_INVALID", attempted=True)
    return receipt("SMOKE_PASSED", attempted=True,
                   model_claim_matched=True, usage=payload.get("usage"))


def smoke(env: Mapping[str, str], *, opener: Any = None) -> dict[str, Any]:
    gate = preflight(env)
    if gate["status"] != "HOST_READBACK_REQUIRED":
        return gate
    opener = opener if opener is not None else _opener()
    if not verify_host(env, opener):
        return receipt("HOST_IDENTITY_UNVERIFIED")
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "temperature": 0,
        "max_tokens": 96,
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    request.add_unredirected_header(
        "Authorization", "Bearer " + env["OPENROUTER_API_KEY"].strip()
    )
    try:
        data = _open(request, opener, 30)
    except urllib.error.HTTPError as exc:
        return receipt(classify_http_status(exc.code), attempted=True)
    except (urllib.error.URLError, OSError, TimeoutError):
        return receipt("TRANSPORT_FAILURE", attempted=True)
    except (ValueError, TypeError):
        return receipt("OUTPUT_INVALID", attempted=True)
    try:
        return validate_completion(json.loads(data))
    except (ValueError, UnicodeDecodeError, TypeError):
        return receipt("OUTPUT_INVALID", attempted=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "smoke"))
    args = parser.parse_args()
    result = preflight(os.environ) if args.action == "preflight" else smoke(os.environ)
    print(json.dumps(result, sort_keys=True))
    # No-request statuses are NOT healthy execution. A completed verified
    # provider assertion is the only zero exit status for the smoke command.
    return 0 if args.action == "smoke" and result["status"] == "SMOKE_PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
