"""Optional, bounded Highlightly All Sports fixture discovery for V17 research.

No arbitrary targets, paid endpoints, bookmaker odds, event verification,
fitted features, model scoring, or market execution. Disabled without explicit
free-tier opt-in and a server-side key. Exactly one bounded request per run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

HOST = "https://sports.highlightly.net"
SPORT_PREFIXES = ("american-football", "nba", "nhl", "baseball", "football", "cricket")
MAX_BODY_BYTES = 256_000
MAX_RECORDS = 20
_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def select_prefix(at: datetime) -> str:
    if at.tzinfo is None:
        raise ValueError("SOURCE_TIMEZONE_UNRESOLVED")
    return SPORT_PREFIXES[int(at.timestamp() // 21600) % len(SPORT_PREFIXES)]


def _receipt(code: str, prefix: str, *, http_status: int | None = None,
             records: list[dict[str, Any]] | None = None,
             quota_remaining: int | None = None,
             plan_tier: str | None = None) -> dict[str, Any]:
    matches = records or []
    return {
        "schema_version": "wow.v17.optional_structured_research.v1",
        "provider": "HIGHLIGHTLY",
        "source_status": "OK" if code == "SOURCE_FETCH_OK" else "BLOCKED",
        "source_code": code, "sport_prefix": prefix,
        "source_observed_at": _utc_now(),
        "endpoint_family": "/{sport_prefix}/matches",
        "request_count": 0 if code in (
            "SOURCE_DISABLED", "SOURCE_CREDENTIAL_UNCONFIGURED",
            "SOURCE_TARGET_INVALID", "SOURCE_DATE_INVALID",
            "SOURCE_KEY_POLICY_NOT_ACCEPTED") else 1,
        "http_status": http_status, "quota_remaining": quota_remaining,
        "plan_tier": plan_tier, "match_count": len(matches),
        "matches": matches,
        "source_is_league_authoritative": False,
        "official_event_identity_verified": False,
        "publisher_claim_verified": False,
        "model_feature_eligible": False,
        "model_rescore_verified": False,
        "prediction_authority": False, "can_execute": False,
    }


def discover_matches(*, prefix: str, event_date: str, client: httpx.Client | None = None,
                     enabled: bool | None = None,
                     key: str | None = None) -> dict[str, Any]:
    """Fetch only the documented /matches endpoint; research identities only."""
    if prefix not in SPORT_PREFIXES:
        return _receipt("SOURCE_TARGET_INVALID", prefix)
    try:
        if date.fromisoformat(event_date).isoformat() != event_date:
            raise ValueError()
    except (ValueError, TypeError):
        return _receipt("SOURCE_DATE_INVALID", prefix)
    active = (os.environ.get("WOW_HIGHLIGHTLY_ENABLED", "").lower() == "true"
              if enabled is None else enabled)
    if not active:
        return _receipt("SOURCE_DISABLED", prefix)
    # Explicit terms/rights authorization required before live acquisition.
    if os.environ.get("WOW_HIGHLIGHTLY_SOURCE_ALLOWED", "").lower() != "true":
        return _receipt("SOURCE_KEY_POLICY_NOT_ACCEPTED", prefix)
    secret = os.environ.get("WOW_HIGHLIGHTLY_API_KEY") if key is None else key
    if not secret:
        return _receipt("SOURCE_CREDENTIAL_UNCONFIGURED", prefix)
    endpoint = HOST + "/" + prefix + "/matches"
    owns_client = client is None
    http = client or httpx.Client(timeout=12, follow_redirects=False)
    try:
        with http.stream(
            "GET", endpoint,
            params={"date": event_date, "limit": MAX_RECORDS},
            headers={"x-rapidapi-key": secret, "Accept": "application/json",
                     "Accept-Encoding": "identity"},
            follow_redirects=False, timeout=12,
        ) as response:
            status = response.status_code
            if 300 <= status < 400:
                return _receipt("SOURCE_REDIRECT_NOT_ALLOWED", prefix, http_status=status)
            if status >= 400:
                reason = ("SOURCE_RATE_LIMITED" if status == 429 else
                          "SOURCE_AUTH_REJECTED" if status in (401, 403) else
                          "SOURCE_UPSTREAM_UNAVAILABLE" if status >= 500 else
                          "SOURCE_UPSTREAM_REJECTED")
                return _receipt(reason, prefix, http_status=status)
            if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                return _receipt("SOURCE_CONTENT_ENCODING_UNSUPPORTED", prefix, http_status=status)
            try:
                size = int(response.headers.get("Content-Length", "0"))
            except ValueError:
                size = 0
            if size > MAX_BODY_BYTES:
                return _receipt("SOURCE_BODY_TOO_LARGE", prefix, http_status=status)
            data = bytearray()
            for chunk in response.iter_raw(chunk_size=65536):
                data.extend(chunk)
                if len(data) > MAX_BODY_BYTES:
                    return _receipt("SOURCE_BODY_TOO_LARGE", prefix, http_status=status)
            remaining_raw = response.headers.get("x-ratelimit-requests-remaining")
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            return _receipt("SOURCE_INVALID_JSON", prefix, http_status=status)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return _receipt("SOURCE_OUTPUT_INVALID", prefix, http_status=status)
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in payload["data"][:MAX_RECORDS]:
            if not isinstance(row, dict):
                continue
            identifier = str(row.get("id", ""))
            if not _ID_RE.fullmatch(identifier) or identifier in seen:
                continue
            seen.add(identifier)
            result.append({
                "source_match_id": identifier, "source_sport_prefix": prefix,
                "official_event_identity_verified": False,
                "model_feature_eligible": False, "prediction_authority": False,
                "can_execute": False,
            })
        try:
            remaining = int(remaining_raw) if remaining_raw is not None else None
        except ValueError:
            remaining = None
        plan = payload.get("plan")
        tier = plan.get("tier") if isinstance(plan, dict) else None
        return _receipt(
            "SOURCE_FETCH_OK" if result else "SOURCE_NO_VALID_MATCHES", prefix,
            http_status=status, records=result, quota_remaining=remaining,
            plan_tier=tier[:40] if isinstance(tier, str) else None,
        )
    except httpx.TimeoutException:
        return _receipt("SOURCE_TIMEOUT", prefix)
    except httpx.HTTPError:
        return _receipt("SOURCE_HTTP_ERROR", prefix)
    finally:
        if owns_client:
            http.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--prefix", choices=SPORT_PREFIXES)
    parser.add_argument("--date")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    prefix = args.prefix or select_prefix(now)
    result = discover_matches(prefix=prefix, event_date=args.date or now.date().isoformat())
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "source_status", "source_code", "sport_prefix", "match_count",
        "request_count", "quota_remaining", "can_execute")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
