"""Server-owned NBA pregame *raw* source capture for a shadow-only evidence lane.

This is deliberately not a fitted-model feature hydrator: the captured JSON has
no independently certified clock/lineup/rotation/feature provenance, so this
module MUST NOT register a public scoring route, imply timestamp attestation,
publish a probability or promote a fitted artifact. Caller-supplied content is
never accepted as ESPN evidence. Only a fixed provider origin is contacted.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import uuid
from typing import Any, Callable, Mapping

import httpx

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
MODEL_READY = False
SOURCE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
SOURCE_PROVIDER = "ESPN_NBA_GAME_SUMMARY"
SOURCE_REPLAY_STATUS = "RAW_CAPTURE_RESEARCH_ONLY_UNATTESTED"
HTTP_TIMEOUT_SECONDS = 8.0
_EVENT_ID = re.compile(r"(?:espn-)?[0-9]{6,12}\Z", re.ASCII)
_TEAM_ID = re.compile(r"(?:espn-)?[0-9]{1,6}\Z", re.ASCII)


class NBAShadowSourceError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise NBAShadowSourceError("NBA_SHADOW_SOURCE_EVENT_TIME_INVALID") from exc
    if result.utcoffset() is None:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_EVENT_TIME_INVALID")
    return result.astimezone(timezone.utc)


def _team_id(value: Any) -> str:
    token = str(value or "").strip()
    if not _TEAM_ID.fullmatch(token):
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_IDENTITY_INVALID")
    return token.removeprefix("espn-")


def capture_nba_pregame_shadow(
    *,
    official_event_id: str,
    home_team_id: str,
    away_team_id: str,
    event_start_time_utc: str,
    http_get: Callable[..., Any] = httpx.get,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    db: Any | None = None,
) -> dict[str, Any]:
    """Obtain one official pregame JSON response and return an untrusted shadow receipt.

    The runtime supplies the HTTP client and clock; callers never supply ESPN
    payloads, result labels, market odds, model features or attestation flags.
    This function does not persist, score or certify anything.
    """
    event = str(official_event_id or "").strip()
    if not _EVENT_ID.fullmatch(event):
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_IDENTITY_INVALID")
    event = event.removeprefix("espn-")
    home, away = _team_id(home_team_id), _team_id(away_team_id)
    if home == away:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_IDENTITY_INVALID")
    tipoff = _aware(event_start_time_utc)
    try:
        before = _aware(clock())
    except NBAShadowSourceError as exc:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_CLOCK_INVALID") from exc
    if before >= tipoff:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_CAPTURE_AFTER_KICKOFF")

    try:
        response = http_get(
            SOURCE_URL, params={"event": event},
            headers={"Accept": "application/json", "User-Agent": "WOW-NBA-Pregame-Shadow/1.0"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        if getattr(response, "status_code", None) != 200:
            raise NBAShadowSourceError("NBA_SHADOW_SOURCE_PROVIDER_UNAVAILABLE")
        payload = response.json()
    except NBAShadowSourceError:
        raise
    except Exception as exc:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_PROVIDER_UNAVAILABLE") from exc
    if not isinstance(payload, Mapping):
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_PROVIDER_SCHEMA_INVALID")
    try:
        observed = _aware(clock())
    except NBAShadowSourceError as exc:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_CLOCK_INVALID") from exc
    if observed < before or observed >= tipoff:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_CAPTURE_AFTER_KICKOFF")

    header = payload.get("header")
    competitions = header.get("competitions") if isinstance(header, Mapping) else None
    if not isinstance(competitions, list) or len(competitions) != 1 or not isinstance(competitions[0], Mapping):
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_PROVIDER_SCHEMA_INVALID")
    match = competitions[0]
    if str(match.get("id") or "") != event:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_EVENT_IDENTITY_MISMATCH")
    provider_tipoff = _aware(match.get("date"))
    if provider_tipoff != tipoff:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_EVENT_TIME_MISMATCH")
    status = match.get("status")
    status_type = status.get("type") if isinstance(status, Mapping) else None
    if not isinstance(status_type, Mapping):
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_PROVIDER_SCHEMA_INVALID")
    if str(status_type.get("state") or "").casefold() != "pre" or status_type.get("completed") is not False:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_EVENT_NOT_PREGAME")

    competitors = match.get("competitors")
    if not isinstance(competitors, list) or len(competitors) != 2:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_PROVIDER_SCHEMA_INVALID")
    roles: dict[str, str] = {}
    for row in competitors:
        if not isinstance(row, Mapping):
            raise NBAShadowSourceError("NBA_SHADOW_SOURCE_PROVIDER_SCHEMA_INVALID")
        role = row.get("homeAway")
        team = row.get("team")
        if role not in ("home", "away") or role in roles or not isinstance(team, Mapping):
            raise NBAShadowSourceError("NBA_SHADOW_SOURCE_PROVIDER_SCHEMA_INVALID")
        roles[role] = _team_id(team.get("id"))
    if roles != {"home": home, "away": away}:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_EVENT_IDENTITY_MISMATCH")

    try:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_PROVIDER_SCHEMA_INVALID") from exc
    raw_digest = hashlib.sha256(raw).hexdigest()
    capture_digest = hashlib.sha256(
        (event + "|" + observed.isoformat() + "|" + raw_digest).encode("utf-8")
    ).hexdigest()
    receipt = {
        "status": "CAPTURED_SHADOW_RESEARCH_ONLY",
        "sport": "NBA",
        "official_event_id": "espn-" + event,
        "home_team_id": "espn-" + home,
        "away_team_id": "espn-" + away,
        "event_start_time_utc": tipoff.isoformat(),
        "source_provider": SOURCE_PROVIDER,
        "source_uri": SOURCE_URL,
        "source_retrieved_at": observed.isoformat(),
        "raw_source_sha256": raw_digest,
        "source_hash_semantics": "CANONICAL_JSON_NOT_HTTP_WIRE_BYTES",
        "research_capture_fingerprint": capture_digest,
        "source_replay_status": SOURCE_REPLAY_STATUS,
        "timestamp_attestation": "UNVERIFIED",
        "lineup_evidence_status": "NOT_ACQUIRED",
        "feature_hydration_status": "NOT_MATERIALIZED",
        "immutable_persistence_status": "NOT_PERSISTED",
        "model_ready": False,
        "model_invoked": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
        "next_stage": "INDEPENDENT_PREGAME_CAPTURE_ATTESTATION_AND_IMMUTABLE_PERSISTENCE_REQUIRED",
    }
    if db is None:
        return receipt
    return _persist_nba_shadow_source(receipt, db=db, clock=clock, observed=observed, tipoff=tipoff)


def _persist_nba_shadow_source(
    receipt: dict[str, Any],
    *,
    db: Any,
    clock: Callable[[], datetime],
    observed: datetime,
    tipoff: datetime,
) -> dict[str, Any]:
    """Append a source-only snapshot to the existing immutable live-state ledger.

    No separate schema, scoring route, feature attestation, or permission grant.
    The DB client must be supplied by a trusted backend worker, never a caller.
    The stored projection intentionally excludes the full third-party payload.
    """
    try:
        append_at = _aware(clock())
    except NBAShadowSourceError as exc:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_CLOCK_INVALID") from exc
    if append_at < observed or append_at >= tipoff:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_CAPTURE_AFTER_KICKOFF")

    event_id = receipt["official_event_id"]
    schema = "NBA_PREGAME_ESPN_PROJECTION_SHADOW_V1"
    family = "NBA_SOURCE_PROJECTION_RESEARCH_ONLY"
    projection = {
        "source_projection_schema_version": schema,
        "official_event_id": event_id,
        "home_team_id": receipt["home_team_id"],
        "away_team_id": receipt["away_team_id"],
        "event_start_time_utc": receipt["event_start_time_utc"],
        "source_payload_sha256": receipt["raw_source_sha256"],
        "source_hash_semantics": "CANONICAL_JSON_NOT_HTTP_WIRE_BYTES",
        "source_replay_status": "SOURCE_BODY_NOT_RETAINED_NO_EVENT_REPLAY_ATTESTATION",
        "source_retrieved_at": receipt["source_retrieved_at"],
        "lineup_evidence_status": "NOT_ACQUIRED",
        "timestamp_attestation": "UNVERIFIED",
        "feature_hydration_status": "NOT_MATERIALIZED",
        "model_ready": False,
        "can_execute": False,
    }
    payload = json.dumps(projection, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    state_hash = hashlib.sha256(payload).hexdigest()
    source_id = str(uuid.uuid4())
    row = {
        "source_snapshot_id": source_id,
        "official_event_id": event_id,
        "sport": "NBA",
        "league": "NBA",
        "event_status": "SCHEDULED",
        "home_team": receipt["home_team_id"],
        "away_team": receipt["away_team_id"],
        "snapshot_timestamp": observed.isoformat(),
        "latest_material_update_at": observed.isoformat(),
        "source_provider": SOURCE_PROVIDER,
        "source_uri": SOURCE_URL + "?event=" + event_id.removeprefix("espn-"),
        "source_retrieved_at": observed.isoformat(),
        "state_schema_version": schema,
        "state_hash": state_hash,
        "state_json": projection,
        "feature_model_family": family,
        "feature_model_artifact_version": "1",
        "feature_schema_version": schema,
        "feature_artifact_checksum": hashlib.sha256((family + ":" + schema).encode("utf-8")).hexdigest(),
        "can_execute": False,
    }
    try:
        saved = db.table("wow_live_state_snapshots").insert(row).execute()
    except Exception as exc:
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_PERSISTENCE_FAILED") from exc

    persisted = getattr(saved, "data", None)
    if (
        not isinstance(persisted, list) or len(persisted) != 1
        or not isinstance(persisted[0], Mapping)
        or str(persisted[0].get("source_snapshot_id") or "") != source_id
        or persisted[0].get("state_hash") != state_hash
    ):
        raise NBAShadowSourceError("NBA_SHADOW_SOURCE_PERSISTENCE_UNVERIFIED")
    return {
        **receipt,
        "raw_source_snapshot_id": source_id,
        "immutable_persistence_status": "PERSISTED_SOURCE_PROJECTION_ONLY",
        "next_stage": "INDEPENDENT_CAPTURE_ATTESTATION_SOURCE_REPLAY_AND_FEATURE_HYDRATION_REQUIRED",
    }


__all__ = ["CAN_EXECUTE", "MODEL_READY", "NBAShadowSourceError", "capture_nba_pregame_shadow"]
