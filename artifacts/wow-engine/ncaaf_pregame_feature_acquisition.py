"""Acquire the NCAAF pregame slots that an archived schedule can actually prove.

NCAAF_FEATURES_V1 stays 29 slots. This module fills REST_TRAVEL only when the
schedule snapshot and, for travel, the venue snapshot were both retrieved
before kickoff. It does not backdate a later download, read scores, or call
the logistic trainer. The other families stay missing with a typed reason.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ncaaf_feature_compiler import REQUIRED_SCOPED_KEYS

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROVIDER = "CFBD_SCHEDULE_GEOMETRY_V1"
REDUCED_SCHEMA = "NCAAF_FEATURES_SCHEDULE_GEOMETRY_V1"
MINIMUM_REDUCED_ROWS = 300
_EARTH_MILES = 3958.7613

# class is the lawful disposition. A missing reason means this module can emit
# the slot when its archived inputs are present. None of these are V1 approval
# to invent the other families.
SLOT_POLICY: dict[str, dict[str, str]] = {
    "TEAM_POWER": {"family": "PRIOR_TEAM_CHARTING", "disposition": "RECONSTRUCTIBLE_IF_PRIOR_PLAYS_ARCHIVED", "missing_reason": "NCAAF_PRIOR_PLAY_SNAPSHOT_NOT_ARCHIVED"},
    "OFF_EPA": {"family": "PRIOR_TEAM_CHARTING", "disposition": "RECONSTRUCTIBLE_IF_PRIOR_PLAYS_ARCHIVED", "missing_reason": "NCAAF_PRIOR_PLAY_SNAPSHOT_NOT_ARCHIVED"},
    "DEF_EPA": {"family": "PRIOR_TEAM_CHARTING", "disposition": "RECONSTRUCTIBLE_IF_PRIOR_PLAYS_ARCHIVED", "missing_reason": "NCAAF_PRIOR_PLAY_SNAPSHOT_NOT_ARCHIVED"},
    "SUCCESS_RATE": {"family": "PRIOR_TEAM_CHARTING", "disposition": "RECONSTRUCTIBLE_IF_PRIOR_PLAYS_ARCHIVED", "missing_reason": "NCAAF_PRIOR_PLAY_SNAPSHOT_NOT_ARCHIVED"},
    "EXPLOSIVENESS": {"family": "PRIOR_TEAM_CHARTING", "disposition": "RECONSTRUCTIBLE_IF_PRIOR_PLAYS_ARCHIVED", "missing_reason": "NCAAF_PRIOR_PLAY_SNAPSHOT_NOT_ARCHIVED"},
    "TEMPO": {"family": "PRIOR_TEAM_CHARTING", "disposition": "RECONSTRUCTIBLE_IF_PRIOR_PLAYS_ARCHIVED", "missing_reason": "NCAAF_PRIOR_PLAY_SNAPSHOT_NOT_ARCHIVED"},
    "TURNOVER_VOLATILITY": {"family": "PRIOR_TEAM_CHARTING", "disposition": "RECONSTRUCTIBLE_IF_PRIOR_PLAYS_ARCHIVED", "missing_reason": "NCAAF_PRIOR_PLAY_SNAPSHOT_NOT_ARCHIVED"},
    "SPECIAL_TEAMS": {"family": "PRIOR_TEAM_CHARTING", "disposition": "RECONSTRUCTIBLE_IF_PRIOR_PLAYS_ARCHIVED", "missing_reason": "NCAAF_PRIOR_PLAY_SNAPSHOT_NOT_ARCHIVED"},
    "QB_VALUE": {"family": "PERSONNEL", "disposition": "ARCHIVED_PREGAME_ONLY", "missing_reason": "NCAAF_PREGAME_STARTER_NOT_ARCHIVED"},
    "QB_CERTAINTY": {"family": "PERSONNEL", "disposition": "ARCHIVED_PREGAME_ONLY", "missing_reason": "NCAAF_APPROVED_SOURCE_HAS_NO_ROW"},
    "OL_HEALTH": {"family": "PERSONNEL", "disposition": "ARCHIVED_PREGAME_ONLY", "missing_reason": "NCAAF_PREGAME_AVAILABILITY_NOT_ARCHIVED"},
    "DEF_FRONT_HEALTH": {"family": "PERSONNEL", "disposition": "ARCHIVED_PREGAME_ONLY", "missing_reason": "NCAAF_PREGAME_AVAILABILITY_NOT_ARCHIVED"},
    "SKILL_AVAILABILITY": {"family": "PERSONNEL", "disposition": "ARCHIVED_PREGAME_ONLY", "missing_reason": "NCAAF_APPROVED_SOURCE_HAS_NO_ROW"},
    "REST_TRAVEL": {"family": "SCHEDULE_GEOMETRY", "disposition": "ARCHIVED_SCHEDULE", "missing_reason": ""},
    "WEATHER": {"family": "WEATHER_FORECAST", "disposition": "ARCHIVED_PREGAME_ONLY", "missing_reason": "NCAAF_PREGAME_FORECAST_NOT_ARCHIVED"},
}
_POSTGAME_REFUSALS = (
    "TARGET_GAME_BOX_SCORE",
    "END_OF_SEASON_OR_UNTIMED_RATING",
    "ELO_RECEIPT",
    "OBSERVED_GAME_WEATHER",
    "SPORTSBOOK_PRICE",
)


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def _miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    chord = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lon / 2) ** 2
    return round(2 * _EARTH_MILES * math.asin(math.sqrt(chord)), 3)


def _coord(venues: Mapping[str, Mapping[str, Any]], venue_id: str) -> tuple[float, float] | None:
    row = venues.get(venue_id)
    if not isinstance(row, Mapping):
        return None
    try:
        lat, lon = float(row["latitude"]), float(row["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return None
    return lat, lon


def _schedule_game(row: Mapping[str, Any]) -> dict[str, Any] | None:
    event_id = str(row.get("official_event_id") or row.get("id") or "").strip()
    home = str(row.get("home_team") or row.get("homeTeam") or "").strip()
    away = str(row.get("away_team") or row.get("awayTeam") or "").strip()
    venue_id = str(row.get("venue_id") or row.get("venueId") or "").strip()
    if not event_id or not home or not away:
        return None
    try:
        start = _aware(row.get("event_start_time") or row.get("startDate") or row.get("start_date"))
    except (TypeError, ValueError):
        return None
    return {
        "official_event_id": event_id,
        "event_start_time": start,
        "home_team": home,
        "away_team": away,
        "venue_id": venue_id,
        "neutral_site": row.get("neutral_site") is True or row.get("neutralSite") is True,
    }


def _prior(team: str, kickoff: datetime, games: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    played = [
        game for game in games
        if game["event_start_time"] < kickoff and team in {game["home_team"], game["away_team"]}
    ]
    if not played:
        return None, "NCAAF_PRIOR_GAME_NOT_ON_SCHEDULE"
    latest = max(game["event_start_time"] for game in played)
    tied = [game for game in played if game["event_start_time"] == latest]
    if len(tied) != 1:
        return None, "NCAAF_PRIOR_GAME_AMBIGUOUS"
    return tied[0], ""


def _rest_row(
    game: Mapping[str, Any],
    *,
    scope: str,
    team: str,
    prior: Mapping[str, Any],
    evidence_at: datetime,
    payload: dict[str, Any],
) -> dict[str, Any]:
    stamped = dict(payload)
    stamped["scores_used"] = False
    stamped["market_features_used"] = False
    return {
        "official_event_id": game["official_event_id"],
        "event_start_time": game["event_start_time"].isoformat(),
        "evidence_kind": "REST_TRAVEL",
        "scope": scope,
        "team": team,
        "source_provider": PROVIDER,
        "source_record_id": f"{prior['official_event_id']}:{scope}",
        "source_uri": "cfbd:/games",
        "evidence_timestamp": evidence_at.isoformat(),
        "provenance_grade": "B",
        "payload": stamped,
        "payload_sha256": _hash(stamped),
        "blocker_codes": [],
        "can_execute": False,
    }


def _coverage(event_id: str, kind: str, scope: str, *, status: str, reason: str, timestamp: str | None, provider: str | None, digest: str | None = None) -> dict[str, Any]:
    return {
        "official_event_id": event_id,
        "evidence_kind": kind,
        "scope": scope,
        "slot": f"{kind}:{scope}",
        "status": status,
        "reason": reason,
        "provider": provider,
        "evidence_timestamp": timestamp,
        "payload_sha256": digest,
        "can_execute": False,
    }


def _family_gaps(event_id: str) -> list[dict[str, Any]]:
    gaps = []
    for kind, scope in REQUIRED_SCOPED_KEYS:
        policy = SLOT_POLICY[kind]
        if kind == "REST_TRAVEL":
            continue
        gaps.append(_coverage(
            event_id, kind, scope,
            status="MISSING",
            reason=policy["missing_reason"],
            timestamp=None,
            provider=None,
        ))
    return gaps


def acquire_schedule_geometry(
    games: Iterable[Mapping[str, Any]],
    *,
    schedule_retrieved_at: Any,
    venues: Iterable[Mapping[str, Any]] | None = None,
    venue_retrieved_at: Any | None = None,
) -> dict[str, Any]:
    """Return REST_TRAVEL rows and a 29-slot coverage receipt. Writes nothing."""
    schedule_at = _aware(schedule_retrieved_at)
    venue_at = _aware(venue_retrieved_at) if venue_retrieved_at is not None else None
    catalog: dict[str, Mapping[str, Any]] = {}
    for row in venues or []:
        venue_id = str(row.get("id") or row.get("venue_id") or "").strip()
        if venue_id:
            catalog[venue_id] = row
    prepared = [game for row in games if (game := _schedule_game(row)) is not None]
    evidence: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for game in prepared:
        event_id = game["official_event_id"]
        kickoff = game["event_start_time"]
        coverage.extend(_family_gaps(event_id))
        if schedule_at >= kickoff:
            for scope in ("HOME", "AWAY"):
                coverage.append(_coverage(
                    event_id, "REST_TRAVEL", scope,
                    status="MISSING",
                    reason="NCAAF_SOURCE_RETRIEVED_AFTER_KICKOFF",
                    timestamp=schedule_at.isoformat(),
                    provider=PROVIDER,
                ))
            continue
        for scope, team in (("HOME", game["home_team"]), ("AWAY", game["away_team"])):
            prior, reason = _prior(team, kickoff, prepared)
            if prior is None:
                coverage.append(_coverage(
                    event_id, "REST_TRAVEL", scope,
                    status="MISSING", reason=reason, timestamp=schedule_at.isoformat(), provider=PROVIDER,
                ))
                continue
            rest_days = (kickoff - prior["event_start_time"]).total_seconds() / 86400.0
            payload: dict[str, Any] = {
                "rest_days": round(rest_days, 6),
                "prior_event_id": prior["official_event_id"],
                "reconstruction": "ARCHIVED_SCHEDULE",
            }
            evidence_at = schedule_at
            if scope == "AWAY":
                current_venue = game["venue_id"]
                prior_venue = str(prior["venue_id"])
                if current_venue and current_venue == prior_venue:
                    payload["travel_distance_miles"] = 0.0
                else:
                    if venue_at is None or venue_at >= kickoff:
                        coverage.append(_coverage(
                            event_id, "REST_TRAVEL", scope,
                            status="MISSING",
                            reason="NCAAF_VENUE_SNAPSHOT_NOT_PREGAME",
                            timestamp=None if venue_at is None else venue_at.isoformat(),
                            provider=PROVIDER,
                        ))
                        continue
                    origin = _coord(catalog, prior_venue)
                    destination = _coord(catalog, current_venue)
                    if origin is None or destination is None:
                        coverage.append(_coverage(
                            event_id, "REST_TRAVEL", scope,
                            status="MISSING",
                            reason="NCAAF_VENUE_COORDINATES_MISSING",
                            timestamp=venue_at.isoformat(),
                            provider=PROVIDER,
                        ))
                        continue
                    payload["travel_distance_miles"] = _miles(*origin, *destination)
                    evidence_at = max(schedule_at, venue_at)
            row = _rest_row(game, scope=scope, team=team, prior=prior, evidence_at=evidence_at, payload=payload)
            evidence.append(row)
            coverage.append(_coverage(
                event_id, "REST_TRAVEL", scope,
                status="EVIDENCED",
                reason="",
                timestamp=row["evidence_timestamp"],
                provider=PROVIDER,
                digest=row["payload_sha256"],
            ))
    complete = []
    by_event: dict[str, set[str]] = {}
    for row in evidence:
        by_event.setdefault(row["official_event_id"], set()).add(row["scope"])
    for event_id, scopes in by_event.items():
        if scopes == {"HOME", "AWAY"}:
            complete.append(event_id)
    reduced_block = (
        "NCAAF_SCHEDULE_GEOMETRY_ROWS_INSUFFICIENT"
        if len(complete) < MINIMUM_REDUCED_ROWS
        else "NCAAF_CERTIFICATION_REQUIRED"
    )
    return {
        "provider": PROVIDER,
        "feature_schema_version": "NCAAF_FEATURES_V1",
        "evidence_rows": evidence,
        "coverage": coverage,
        "schedule_geometry_complete_event_ids": sorted(complete),
        "schedule_geometry_complete_rows": len(complete),
        "v1_complete_rows": 0,
        "reduced_feature_schema_version": REDUCED_SCHEMA,
        "reduced_challenger_replaces_v1": False,
        "reduced_challenger_fit_allowed": False,
        "reduced_challenger_fit_block": reduced_block,
        "scores_used": False,
        "market_features_used": False,
        "postgame_inputs_refused": list(_POSTGAME_REFUSALS),
        "validation_db_rows_written": 0,
        "registry_applied": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def schedule_geometry_feature_row(receipt: Mapping[str, Any], official_event_id: str) -> dict[str, Any] | None:
    """Build one reduced feature row. This is not an NCAAF_FEATURES_V1 row."""
    rows = [
        row for row in receipt.get("evidence_rows") or []
        if row.get("official_event_id") == official_event_id and row.get("evidence_kind") == "REST_TRAVEL"
    ]
    home = next((row for row in rows if row.get("scope") == "HOME"), None)
    away = next((row for row in rows if row.get("scope") == "AWAY"), None)
    if home is None or away is None:
        return None
    return {
        "feature_schema_version": REDUCED_SCHEMA,
        "official_event_id": official_event_id,
        "home_rest_days": home["payload"]["rest_days"],
        "away_rest_days": away["payload"]["rest_days"],
        "travel_distance_miles": away["payload"]["travel_distance_miles"],
        "home_prior_event_id": home["payload"]["prior_event_id"],
        "away_prior_event_id": away["payload"]["prior_event_id"],
        "evidence_timestamps": {
            "HOME": home["evidence_timestamp"],
            "AWAY": away["evidence_timestamp"],
        },
        "v1_compatible": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def reduced_challenger_contract() -> dict[str, Any]:
    """Validation requirements for a separate schema. V1 is unchanged."""
    return {
        "feature_schema_version": REDUCED_SCHEMA,
        "parent_contract": "NCAAF_FEATURES_V1",
        "replaces_v1": False,
        "slots": ["REST_TRAVEL:HOME", "REST_TRAVEL:AWAY"],
        "model_features": ["home_rest_days", "away_rest_days", "travel_distance_miles", "neutral_site"],
        "minimum_complete_rows": MINIMUM_REDUCED_ROWS,
        "split": "CHRONOLOGICAL_60_20_20",
        "validation_requirements": [
            "evidence_timestamp strictly before kickoff",
            "prior game start strictly before kickoff",
            "schedule and venue snapshots retrieved before kickoff",
            "scores, ratings, box scores, and market prices unused",
            "independent certification before publication",
            "team-event bridge remains unregistered",
        ],
        "fit_invoked": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "MINIMUM_REDUCED_ROWS",
    "PROBABILITY_PUBLISHABLE",
    "PROVIDER",
    "REDUCED_SCHEMA",
    "SLOT_POLICY",
    "acquire_schedule_geometry",
    "reduced_challenger_contract",
    "schedule_geometry_feature_row",
]
