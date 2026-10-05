"""Fetch read-only cross-sport audit evidence through GitHub OIDC.

No database/service-role secret is present on the runner. The exact protected
main workflow mints a short-lived GitHub OIDC token for the dedicated audit
audience and calls the governed Supabase Edge read feed.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

AUDIENCE = "wow-v17-cross-sport-audit"
DEFAULT_URL = (
    "https://iczfhsmjrrafhvcpmqhr.supabase.co/functions/v1/"
    "wow-v17-cross-sport-audit-feed"
)
PAGE_SIZE = 250
TOKEN_CACHE_SECONDS = 120.0
_TOKEN: str | None = None
_TOKEN_AT = 0.0


def _mint(*, force: bool = False) -> str:
    global _TOKEN, _TOKEN_AT
    now = time.monotonic()
    if not force and _TOKEN and now - _TOKEN_AT < TOKEN_CACHE_SECONDS:
        return _TOKEN
    request_url = str(os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL") or "").strip()
    request_token = str(os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN") or "").strip()
    if not request_url or not request_token:
        raise RuntimeError("GITHUB_OIDC_REQUEST_CONTEXT_UNAVAILABLE")
    separator = "&" if "?" in request_url else "?"
    url = f"{request_url}{separator}audience={quote(AUDIENCE)}"
    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {request_token}",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"GITHUB_OIDC_MINT_HTTP_{exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("GITHUB_OIDC_MINT_TRANSPORT_FAILED") from exc
    token = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("GITHUB_OIDC_MINT_RESPONSE_INVALID")
    _TOKEN = token.strip()
    _TOKEN_AT = now
    return _TOKEN


def _post(url: str, payload: dict) -> dict:
    token = _mint()
    req = Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            error = json.loads(exc.read().decode("utf-8"))
            code = error.get("code") if isinstance(error, dict) else None
        except Exception:
            code = None
        if exc.code == 401:
            fresh = _mint(force=True)
            retry = Request(
                url,
                data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {fresh}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            try:
                with urlopen(retry, timeout=120) as response:
                    body = json.loads(response.read().decode("utf-8"))
            except HTTPError as retry_exc:
                try:
                    retry_error = json.loads(retry_exc.read().decode("utf-8"))
                    retry_code = (
                        retry_error.get("code")
                        if isinstance(retry_error, dict)
                        else None
                    )
                except Exception:
                    retry_code = None
                raise RuntimeError(
                    retry_code or f"CROSS_SPORT_AUDIT_EDGE_HTTP_{retry_exc.code}"
                ) from retry_exc
            except (URLError, TimeoutError, json.JSONDecodeError) as retry_exc:
                raise RuntimeError("CROSS_SPORT_AUDIT_EDGE_TRANSPORT_FAILED") from retry_exc
        else:
            raise RuntimeError(code or f"CROSS_SPORT_AUDIT_EDGE_HTTP_{exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("CROSS_SPORT_AUDIT_EDGE_TRANSPORT_FAILED") from exc

    if (
        not isinstance(body, dict)
        or body.get("ok") is not True
        or body.get("can_execute") is not False
    ):
        raise RuntimeError(
            str(body.get("code") if isinstance(body, dict) else "EDGE_RESPONSE_INVALID")
        )
    return body


def fetch_bundle(url: str) -> dict:
    candidates = _post(url, {"action": "LATEST_CANDIDATES"}).get("rows") or []
    lanes = []
    for candidate in candidates:
        rows = []
        offset = 0
        while True:
            page = _post(
                url,
                {
                    "action": "COHORT_PAGE",
                    "sport": candidate["sport"],
                    "league": candidate["league"],
                    "feature_schema_version": candidate["feature_schema_version"],
                    "candidate_created_at": candidate["created_at"],
                    "offset": offset,
                    "limit": PAGE_SIZE,
                },
            )
            chunk = page.get("rows") or []
            rows.extend(chunk)
            if page.get("done") is True:
                break
            next_offset = page.get("next_offset")
            if not isinstance(next_offset, int) or next_offset <= offset:
                raise RuntimeError("CROSS_SPORT_AUDIT_PAGINATION_INVALID")
            offset = next_offset
        lanes.append({"candidate": candidate, "rows": rows})
        print(
            f"FETCHED {candidate['sport']}/{candidate['league']} "
            f"rows={len(rows)} can_execute=false",
            flush=True,
        )
    return {
        "source": "SUPABASE_EDGE_GITHUB_OIDC",
        "lanes": lanes,
        "probability_publishable": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default=os.getenv("WOW_CROSS_SPORT_AUDIT_FEED_URL") or DEFAULT_URL,
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    bundle = fetch_bundle(args.url)
    Path(args.output).write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
