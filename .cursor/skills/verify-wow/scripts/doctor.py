#!/usr/bin/env python3
"""Read-only WOW V17 backend liveness probe.

This proves only HTTP liveness for the requested origin. It is deliberately
incapable of scoring, modifying state, releasing a lease, or attesting that a
ChatGPT user journey passed. No credentials or raw response bodies are stored.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEFAULT_ORIGIN = "https://wow-governed-probability-engine.onrender.com"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


class NoRedirects(HTTPRedirectHandler):
    """Prevent a redirected foreign endpoint from proving the original host live."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_without_redirects(request, timeout):
    return build_opener(NoRedirects()).open(request, timeout=timeout)


def checked_origin(origin: str) -> str:
    """Allow HTTPS origins or HTTP on loopback only, without URL credentials."""
    parsed = urlsplit(origin.rstrip("/"))
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
    ):
        raise ValueError("origin must be HTTPS or loopback HTTP without credentials or paths")
    return origin.rstrip("/")


def probe(origin: str, *, opener=open_without_redirects) -> dict:
    """Check only GET /health/live; never infer a product or model result."""
    origin = checked_origin(origin)
    request = Request(
        origin + "/health/live",
        headers={"Accept": "application/json", "User-Agent": "wow-v17-verification-skill/1"},
        method="GET",
    )
    started = monotonic()
    result = {
        "schema": "WOW_V17_READ_ONLY_LIVENESS_PROOF_V1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "origin": origin,
        "method": "GET",
        "path": "/health/live",
        "status": "LIVENESS_UNPROVEN",
        "http_status": None,
        "response_sha256": None,
        "response_body_recorded": False,
        "user_journey_verified": False,
        "model_probability_verified": False,
        "deployment_sha_verified": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }
    try:
        with opener(request, timeout=15) as response:
            result["http_status"] = response.status
            body = response.read(65536)
            result["response_sha256"] = sha256(body).hexdigest()
            result["status"] = (
                "BACKEND_LIVENESS_PASS" if response.status == 200
                else "BACKEND_LIVENESS_FAIL"
            )
    except HTTPError as exc:
        result["http_status"] = exc.code
        result["error_type"] = "HTTPError"
    except (URLError, TimeoutError, OSError) as exc:
        result["error_type"] = type(exc).__name__
    result["elapsed_ms"] = round((monotonic() - started) * 1000)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--output", required=True, help="New JSON evidence file (never overwritten)")
    args = parser.parse_args(argv)
    try:
        receipt = probe(args.origin)
    except ValueError as exc:
        parser.error(str(exc))
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create: preserve evidence rather than silently replacing it.
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "BACKEND_LIVENESS_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
