"""Scheduled, authenticated NHL source re-ingest caller; research data only.

Calls the existing protected endpoint; never fits, certifies, promotes, publishes
model probabilities or executes wagers. All failures are typed and summarized.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

ACTION_ORIGIN = "https://wow-governed-probability-engine.onrender.com"
SOURCE_ROUTE = "/internal/v17/nhl-source-reingest"
TRANSIENT_HTTP = frozenset({404, 502, 503, 504})
CAN_EXECUTE = False


def blocked(code: str) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "code": code,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def classify_response(payload: Any) -> dict[str, Any]:
    """Only an authenticated, actually persisted timeline is accepted as data.

    The result remains inert even on success. A response cannot approve a model.
    """
    if not isinstance(payload, Mapping):
        return blocked("NHL_SOURCE_REFRESH_RESPONSE_INVALID")
    for key in ("can_execute", "automatic_certification", "automatic_promotion",
                "probability_publishable"):
        if payload.get(key) is not False:
            return blocked("NHL_SOURCE_REFRESH_CONTRACT_INVALID")
    if payload.get("status") != "NHL_SOURCE_REINGEST_PERSISTED":
        code = payload.get("code") or payload.get("status") or "NHL_SOURCE_REINGEST_UNPROVEN"
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,100}", code):
            code = "NHL_SOURCE_REFRESH_RESPONSE_INVALID"
        return blocked(code)
    if payload.get("provenance_complete") is not True:
        return blocked("NHL_SOURCE_PROVENANCE_INCOMPLETE")
    if payload.get("timeline_receipt_persisted") is not True:
        return blocked("NHL_SOURCE_TIMELINE_RECEIPT_NOT_PERSISTED")
    if not re.fullmatch(r"[0-9]{8}", str(payload.get("season") or "")):
        return blocked("NHL_SOURCE_REFRESH_SEASON_IDENTITY_INVALID")
    return {
        "status": "SOURCE_TIMELINE_REFRESHED_REQUIRES_SCIENTIFIC_ACCEPTANCE",
        "season": str(payload["season"]),
        "source_event_n": payload.get("source_event_n"),
        "timeline_receipt_persisted": True,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _token() -> str:
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    join = "&" if "?" in url else "?"
    url += join + urllib.parse.urlencode({"audience": "wow-v17-multiscout"})
    request = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        token = json.load(response)["value"]
    if not isinstance(token, str) or not token:
        raise ValueError("NHL_SOURCE_REFRESH_OIDC_TOKEN_INVALID")
    return token


def call_protected_reingest() -> dict[str, Any]:
    """Bounded retry only for endpoint/deploy transitions, never 401/403."""
    url = ACTION_ORIGIN + SOURCE_ROUTE
    for attempt in range(3):
        try:
            request = urllib.request.Request(
                url,
                data=b"",
                method="POST",
                headers={
                    "Authorization": "Bearer " + _token(),
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=900) as response:
                return classify_response(json.load(response))
        except urllib.error.HTTPError as exc:
            if exc.code not in TRANSIENT_HTTP or attempt == 2:
                return blocked("NHL_SOURCE_REFRESH_HTTP_" + str(exc.code))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                return blocked("NHL_SOURCE_REFRESH_TRANSPORT_UNAVAILABLE")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return blocked("NHL_SOURCE_REFRESH_RESPONSE_OR_AUTH_INVALID")
        if attempt < 2:
            time.sleep(5)
    return blocked("NHL_SOURCE_REFRESH_ATTEMPTS_EXHAUSTED")


def main() -> None:
    if os.environ.get("WOW_CAN_EXECUTE") != "false" or os.environ.get("WOW_DRY_RUN_ONLY") != "true":
        outcome = blocked("NHL_SOURCE_REFRESH_NONEXECUTION_ENV_INVALID")
    elif os.environ.get("GITHUB_REF") != "refs/heads/main":
        outcome = blocked("NHL_SOURCE_REFRESH_PROTECTED_MAIN_REQUIRED")
    else:
        try:
            outcome = call_protected_reingest()
        except Exception:
            # Never print OIDC tokens or server responses that may contain secrets.
            outcome = blocked("NHL_SOURCE_REFRESH_UNEXPECTED_CALL_FAILURE")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    line = (
        "NHL source timeline: status=" + outcome["status"]
        + " code=" + str(outcome.get("code") or "NONE")
        + " season=" + str(outcome.get("season") or "UNVERIFIED")
        + " can_execute=false; scientific_certification=false"
    )
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(line + "\n")
    if outcome["status"] != "SOURCE_TIMELINE_REFRESHED_REQUIRES_SCIENTIFIC_ACCEPTANCE":
        print("::warning::" + str(outcome.get("code") or outcome["status"]))
    print(json.dumps(outcome, sort_keys=True))


if __name__ == "__main__":
    main()
