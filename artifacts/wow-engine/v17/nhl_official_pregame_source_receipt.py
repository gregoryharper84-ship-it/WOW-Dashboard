"""Read-only official NHL FUT-game source receipt for the LLP team-event lane.

A first-party gamecenter payload can establish event identity and freshness; it
cannot establish projected/confirmed starters, injury completeness, Elo, a
calibrator, or a model probability. This adapter never fills those gaps from
odds, historical goalieComparison leaders, or guessed values.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Callable, Mapping

import requests

from v17.nhl_event_identity import team_abbreviation

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
SOURCE_PROVIDER = "NHL_PUBLIC_WEB_API"
GAMECENTER_URL = "https://api-web.nhle.com/v1/gamecenter/{game_id}/landing"
MAX_RESPONSE_BYTES = 2_000_000
UNHYDRATED_INPUTS = (
    "goalie_status",
    "injury_report",
    "rest_travel",
    "home_elo",
    "away_elo",
    "home_goalie_sv_pct",
    "away_goalie_sv_pct",
    "home_pp_pct",
    "away_pk_pct",
    "calibration_artifact",
)


class NHLPregameSourceError(ValueError):
    """Typed non-publishable source failure, never an odds/model fallback."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _utc(value: Any) -> datetime:
    try:
        parsed = (
            value if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (ValueError, TypeError, OverflowError) as exc:
        raise NHLPregameSourceError("NHL_SOURCE_TIME_INVALID") from exc
    if parsed.utcoffset() is None:
        raise NHLPregameSourceError("NHL_SOURCE_TIME_INVALID")
    return parsed.astimezone(timezone.utc)


def capture_nhl_official_pregame(
    *,
    official_event_id: str,
    event_start_time_utc: str,
    home_team: str,
    away_team: str,
    session: Any = requests,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Fetch and pin a real official pregame payload, without model promotion.

    The caller must supply an already-reconciled NHL event identity. The payload
    is rechecked rather than trusting it to hydrate arbitrary scorer inputs.
    Clock is injectable for deterministic tests, not for trusted production
    attestation. A returned receipt is evidence-only until independently signed.
    """
    game_id = str(official_event_id).strip()
    if not re.fullmatch(r"\d{10}", game_id):
        raise NHLPregameSourceError("NHL_SOURCE_EVENT_ID_INVALID")
    home = team_abbreviation(home_team)
    away = team_abbreviation(away_team)
    if not home or not away or home == away:
        raise NHLPregameSourceError("NHL_SOURCE_TEAMS_INVALID")
    start = _utc(event_start_time_utc)
    if _utc(clock()) >= start:
        raise NHLPregameSourceError("NHL_SOURCE_NOT_PREGAME")

    url = GAMECENTER_URL.format(game_id=game_id)
    try:
        response = session.get(
            url, timeout=12, headers={"User-Agent": "WOW-V17-NHL-Research/1.0"}
        )
    except Exception as exc:  # noqa: BLE001 - transport failures fail closed
        raise NHLPregameSourceError("NHL_SOURCE_FETCH_FAILED") from exc
    status = getattr(response, "status_code", None)
    if status != 200:
        raise NHLPregameSourceError(
            "NHL_SOURCE_RATE_LIMITED" if status == 429 else "NHL_SOURCE_HTTP_FAILED"
        )
    raw = getattr(response, "content", None)
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise NHLPregameSourceError("NHL_SOURCE_RAW_BYTES_MISSING_OR_OVERSIZE")
    try:
        payload = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise NHLPregameSourceError("NHL_SOURCE_JSON_INVALID") from exc
    if not isinstance(payload, Mapping):
        raise NHLPregameSourceError("NHL_SOURCE_SCHEMA_INVALID")
    try:
        actual_home = str(payload["homeTeam"]["abbrev"]).strip().upper()
        actual_away = str(payload["awayTeam"]["abbrev"]).strip().upper()
        actual_start = _utc(payload["startTimeUTC"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise NHLPregameSourceError("NHL_SOURCE_SCHEMA_INVALID") from exc

    if (
        str(payload.get("id")) != game_id
        or actual_home != home or actual_away != away
        or actual_start != start
    ):
        raise NHLPregameSourceError("NHL_SOURCE_EVENT_IDENTITY_MISMATCH")
    # Never treat settled/live games as original pregame captures. Also reject
    # unknown game-state strings instead of assuming they mean scheduled.
    if payload.get("gameState") not in ("FUT", "PRE"):
        raise NHLPregameSourceError("NHL_SOURCE_EVENT_NOT_FUTURE")
    if payload.get("gameType") not in (2, 3):
        raise NHLPregameSourceError("NHL_SOURCE_GAME_TYPE_INVALID")

    retrieved_at = _utc(clock())
    if retrieved_at >= start:
        raise NHLPregameSourceError("NHL_SOURCE_CAPTURE_AFTER_START")
    payload_hash = sha256(raw).hexdigest()
    snapshot_id = sha256(f"{game_id}:{payload_hash}".encode("ascii")).hexdigest()
    return {
        "status": "NHL_OFFICIAL_SOURCE_CAPTURED_NOT_MODEL_READY",
        "sport": "NHL",
        "official_event_id": game_id,
        "event_start_time_utc": start.isoformat(),
        "home_team": home,
        "away_team": away,
        "game_state": payload["gameState"],
        "source_id": SOURCE_PROVIDER,
        "source_uri": url,
        "source_snapshot_id": snapshot_id,
        "source_payload_sha256": payload_hash,
        "as_of": retrieved_at.isoformat(),
        "raw_payload_bytes": len(raw),
        "raw_payload_archived": False,
        "source_signature_independently_verified": False,
        "goalies_confirmed": False,
        "starting_goalie_status": "UNCONFIRMED",
        "injury_report_status": "NOT_CAPTURED",
        "missing_fields": list(UNHYDRATED_INPUTS),
        "model_invoked": False,
        "calibration_artifact_certified": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "PROBABILITY_PUBLISHABLE",
    "NHLPregameSourceError",
    "capture_nhl_official_pregame",
]
