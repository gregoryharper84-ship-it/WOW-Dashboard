"""Read-only NHL gamecenter pregame source capture (not goalie certification).

A pregame boxscore does not prove who will start in goal. This adapter
verifies an official event payload and preserves that uncertainty instead of
promoting a projected/roster goalie to CONFIRMED. A successful capture is not a
fitted-model input bundle, durable source snapshot, calibration, or reducer pass.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Callable, Mapping

import requests

CAN_EXECUTE = False
BASE_URL = "https://api-web.nhle.com/v1/gamecenter/{event_id}/boxscore"
_SCHEDULE_URL = re.compile(
    r"https://api-web\.nhle\.com/v1/club-schedule-season/[A-Z]{3}/\d{8}\Z"
)
_MAX_BYTES = 2 * 1024 * 1024


def _time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _held(code: str, *, event_id: str | None = None, **evidence: Any) -> dict[str, Any]:
    return {
        "status": "HELD",
        "code": code,
        "blockers": [code],
        "sport": "NHL",
        "market_scope": "FULL_GAME_WIN_INCLUDING_OT_SO",
        "official_event_id": event_id,
        "source_provider": "NHL_PUBLIC_WEB_API",
        "source_snapshot_id": None,  # A hash is not a persisted snapshot receipt.
        "source_persistence_status": "NOT_PERSISTED",
        "goalie_status": "UNVERIFIED",
        "goalie_confirmation_status": "NOT_ESTABLISHED",
        "fitted_specialist_invoked": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
        **evidence,
    }


def capture_nhl_pregame_gamecenter(
    event_id: str,
    *,
    resolved_identity: Mapping[str, Any] | None,
    as_of_utc: datetime,
    get: Callable[..., Any] = requests.get,
    timeout_seconds: float = 8.0,
) -> dict[str, Any]:
    """Capture official gamecenter bytes with a strict pregame and identity gate.

    This function must be called by server-owned orchestration. Its identity
    input is only a crosscheck; the caller must not turn this result into a
    trusted resolver receipt or label projected goalies as confirmed.
    """
    if as_of_utc.tzinfo is None:
        raise ValueError("as_of_utc must be timezone-aware")
    now = as_of_utc.astimezone(timezone.utc)
    if not isinstance(event_id, str) or not re.fullmatch(r"\d{10}", event_id, re.ASCII):
        return _held("NHL_GAMECENTER_EVENT_ID_INVALID")
    if not isinstance(resolved_identity, Mapping):
        return _held("NHL_CANONICAL_IDENTITY_RECEIPT_MISSING", event_id=event_id)
    source = resolved_identity.get("identity_source")
    if (
        str(resolved_identity.get("event_id") or "") != event_id
        or resolved_identity.get("identity_provider") != "NHL_PUBLIC_WEB_API"
        or not isinstance(source, str)
        or _SCHEDULE_URL.fullmatch(source) is None
        or resolved_identity.get("can_execute") is not False
        or resolved_identity.get("prediction_authority") is not False
        or resolved_identity.get("market_features_used") is not False
    ):
        return _held("NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH", event_id=event_id)
    expected_home = resolved_identity.get("home_team")
    expected_away = resolved_identity.get("away_team")
    expected_start = _time(resolved_identity.get("event_start_time"))
    verified_at = _time(resolved_identity.get("identity_verified_at"))
    if (
        not isinstance(expected_home, str)
        or not isinstance(expected_away, str)
        or not re.fullmatch(r"[A-Z]{3}", expected_home)
        or not re.fullmatch(r"[A-Z]{3}", expected_away)
        or expected_home == expected_away
        or expected_start is None
        or verified_at is None
        or verified_at > now  # No future receipt.
        or (now - verified_at).total_seconds() > 24 * 3600
    ):
        return _held("NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH", event_id=event_id)
    if expected_start <= now:
        return _held("NHL_EVENT_NOT_PREGAME", event_id=event_id)
    url = BASE_URL.format(event_id=event_id)
    try:
        response = get(
            url,
            timeout=timeout_seconds,
            allow_redirects=False,
            headers={"User-Agent": "WOW-V17-Research/1.0"},
        )
    except Exception:
        return _held("NHL_OFFICIAL_GAMECENTER_SOURCE_UNOBTAINABLE", event_id=event_id)
    status = getattr(response, "status_code", None)
    if status == 429:
        return _held("NHL_OFFICIAL_GAMECENTER_RATE_LIMITED", event_id=event_id)
    if isinstance(status, int) and 300 <= status < 400:
        return _held("NHL_OFFICIAL_GAMECENTER_REDIRECT_REJECTED", event_id=event_id)
    if status != 200 or str(getattr(response, "url", url)) != url:
        return _held("NHL_OFFICIAL_GAMECENTER_SOURCE_UNOBTAINABLE", event_id=event_id)
    raw = getattr(response, "content", None)
    if not isinstance(raw, bytes) or not 0 < len(raw) <= _MAX_BYTES:
        return _held("NHL_OFFICIAL_GAMECENTER_PAYLOAD_INVALID", event_id=event_id)
    sha = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        return _held("NHL_OFFICIAL_GAMECENTER_PAYLOAD_INVALID", event_id=event_id)
    if not isinstance(payload, Mapping):
        return _held("NHL_OFFICIAL_GAMECENTER_SCHEMA_DRIFT", event_id=event_id)
    home = payload.get("homeTeam")
    away = payload.get("awayTeam")
    if (
        str(payload.get("id") or "") != event_id
        or not isinstance(home, Mapping)
        or not isinstance(away, Mapping)
        or home.get("abbrev") != expected_home
        or away.get("abbrev") != expected_away
    ):
        return _held("NHL_GAMECENTER_CANONICAL_IDENTITY_MISMATCH", event_id=event_id)
    source_start = _time(payload.get("startTimeUTC"))
    if source_start is None or abs((source_start - expected_start).total_seconds()) > 30 * 60:
        return _held("NHL_GAMECENTER_START_TIME_MISMATCH", event_id=event_id)
    if source_start <= now or payload.get("gameState") != "FUT":
        return _held("NHL_EVENT_NOT_PREGAME", event_id=event_id)
    return _held(
        "NHL_GOALIE_STARTER_CONFIRMATION_REQUIRED",
        event_id=event_id,
        source_url=url,
        source_content_sha256=sha,
        source_byte_count=len(raw),
        retrieved_at_utc=now.isoformat(),
        timestamp_attestation="RUNNER_CLOCK_ONLY_NOT_INDEPENDENT",
        official_game_state="FUT",
        canonical_home_team=expected_home,
        canonical_away_team=expected_away,
        official_start_time_utc=source_start.isoformat(),
        gamecenter_source_status="OFFICIAL_PREGAME_CAPTURED_RESEARCH_ONLY",
    )
