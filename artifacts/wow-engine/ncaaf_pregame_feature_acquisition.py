"""Turn archived CFBD schedule and venue receipts into REST_TRAVEL evidence.

A scheduled start is not occurrence. A prior game counts only when a source
receipt retrieved at or after that start, and strictly before the target
kickoff, says the game was completed and was not canceled, postponed, or
rescheduled. Venue distance comes only from a hashed /venues receipt retrieved
before kickoff. This module writes nothing and does not fit a model.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ncaaf_feature_compiler import REQUIRED_SCOPED_KEYS

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROVIDER = "CFBD_SCHEDULE_GEOMETRY_V1"
REDUCED_SCHEMA = "NCAAF_FEATURES_SCHEDULE_GEOMETRY_V1"
REDUCED_MODEL_FEATURES = (
    "home_rest_days",
    "away_rest_days",
    "travel_distance_miles",
    "neutral_site",
)
MINIMUM_REDUCED_ROWS = 300
_EARTH_MILES = 3958.7613
_SHA = re.compile(r"^[0-9a-f]{64}$")
_NOT_PLAYED = frozenset({
    "canceled",
    "cancelled",
    "postponed",
    "delayed",
    "rescheduled",
    "suspended",
})

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
    "SCHEDULED_FUTURE_GAME_AS_OCCURRENCE",
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


def _rows_hash(rows: list[Any]) -> str:
    body = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    chord = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lon / 2) ** 2
    return round(2 * _EARTH_MILES * math.asin(math.sqrt(chord)), 3)


def _coord(row: Mapping[str, Any]) -> tuple[float, float] | None:
    try:
        lat = float(row.get("latitude"))
        lon = float(row.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return None
    return lat, lon


def _neutral(row: Mapping[str, Any]) -> bool | None:
    if "neutral_site" in row:
        value = row.get("neutral_site")
    elif "neutralSite" in row:
        value = row.get("neutralSite")
    else:
        return None
    if value is True or value is False:
        return value
    return None


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
    status = str(row.get("status") or row.get("game_status") or "").strip().casefold()
    return {
        "official_event_id": event_id,
        "event_start_time": start,
        "home_team": home,
        "away_team": away,
        "venue_id": venue_id,
        "neutral_site": _neutral(row),
        "completed": row.get("completed") is True,
        "status": status,
    }


def _parse_receipt(raw: Mapping[str, Any], endpoint: str) -> tuple[dict[str, Any] | None, str]:
    snapshot_id = str(raw.get("source_snapshot_id") or "").strip()
    digest = str(raw.get("payload_sha256") or "").strip()
    rows = raw.get("response_rows")
    if (
        not snapshot_id
        or not _SHA.fullmatch(digest)
        or str(raw.get("provider") or "") != "CFBD"
        or str(raw.get("endpoint") or "") != endpoint
        or not isinstance(rows, list)
    ):
        return None, "NCAAF_SOURCE_RECEIPT_PROVENANCE_MISSING"
    try:
        retrieved = _aware(raw.get("retrieved_at"))
    except (TypeError, ValueError):
        return None, "NCAAF_SOURCE_RECEIPT_PROVENANCE_MISSING"
    if _rows_hash(rows) != digest:
        return None, "NCAAF_SOURCE_RECEIPT_HASH_MISMATCH"
    return {
        "source_snapshot_id": snapshot_id,
        "payload_sha256": digest,
        "retrieved_at": retrieved,
        "endpoint": endpoint,
        "response_rows": rows,
    }, ""


def _load_receipts(raws: Iterable[Mapping[str, Any]] | None, endpoint: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    conflicted: set[str] = set()
    for raw in raws or []:
        if not isinstance(raw, Mapping):
            rejected.append({"endpoint": endpoint, "source_snapshot_id": None, "reason": "NCAAF_SOURCE_RECEIPT_PROVENANCE_MISSING"})
            continue
        parsed, reason = _parse_receipt(raw, endpoint)
        snapshot_id = str(raw.get("source_snapshot_id") or "") or None
        if parsed is None:
            rejected.append({"endpoint": endpoint, "source_snapshot_id": snapshot_id, "reason": reason})
            continue
        if parsed["source_snapshot_id"] in conflicted:
            # A digest collision poisons this snapshot ID for the whole batch,
            # including later replays of the original payload.
            continue
        previous = seen.get(parsed["source_snapshot_id"])
        if previous is not None and previous["payload_sha256"] != parsed["payload_sha256"]:
            conflicted.add(parsed["source_snapshot_id"])
            rejected.append({
                "endpoint": endpoint,
                "source_snapshot_id": parsed["source_snapshot_id"],
                "reason": "NCAAF_SOURCE_RECEIPT_DUPLICATE",
            })
            accepted = [item for item in accepted if item["source_snapshot_id"] != parsed["source_snapshot_id"]]
            seen.pop(parsed["source_snapshot_id"], None)
            continue
        if previous is not None:
            continue
        seen[parsed["source_snapshot_id"]] = parsed
        accepted.append(parsed)
    return accepted, rejected


def _sightings(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = []
    for receipt in receipts:
        for row in receipt["response_rows"]:
            if not isinstance(row, Mapping):
                continue
            game = _schedule_game(row)
            if game is None:
                continue
            game["receipt"] = receipt
            found.append(game)
    return found


def _identity(game: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        game["event_start_time"],
        game["home_team"],
        game["away_team"],
        game["venue_id"],
        game["neutral_site"],
    )


def _coverage(event_id: str, kind: str, scope: str, *, status: str, reason: str, timestamp: str | None, provider: str | None, digest: str | None = None, prior_event_id: str | None = None) -> dict[str, Any]:
    return {
        "official_event_id": event_id,
        "evidence_kind": kind,
        "scope": scope,
        "slot": f"{kind}:{scope}",
        "status": status,
        "reason": reason,
        "blocked_prior_event_id": prior_event_id,
        "provider": provider,
        "evidence_timestamp": timestamp,
        "payload_sha256": digest,
        "can_execute": False,
    }


def _family_gaps(event_id: str) -> list[dict[str, Any]]:
    gaps = []
    for kind, scope in REQUIRED_SCOPED_KEYS:
        if kind == "REST_TRAVEL":
            continue
        gaps.append(_coverage(
            event_id, kind, scope,
            status="MISSING",
            reason=SLOT_POLICY[kind]["missing_reason"],
            timestamp=None,
            provider=None,
        ))
    return gaps


def _occurred(game: Mapping[str, Any], kickoff: datetime) -> bool:
    retrieved = game["receipt"]["retrieved_at"]
    return (
        game["completed"] is True
        and game["status"] not in _NOT_PLAYED
        and game["event_start_time"] <= retrieved < kickoff
    )


def _prior(team: str, kickoff: datetime, sightings: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str, str | None]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for game in sightings:
        if game["event_start_time"] >= kickoff or team not in {game["home_team"], game["away_team"]}:
            continue
        grouped.setdefault(game["official_event_id"], []).append(game)
    if not grouped:
        return None, "NCAAF_PRIOR_GAME_NOT_ON_SCHEDULE", None
    candidates = []
    for event_id, rows in grouped.items():
        if len({_identity(row) for row in rows}) != 1:
            candidates.append((rows[0]["event_start_time"], event_id, None, "NCAAF_PRIOR_GAME_IDENTITY_CONFLICT"))
            continue
        if any(row["status"] in _NOT_PLAYED for row in rows):
            candidates.append((rows[0]["event_start_time"], event_id, None, "NCAAF_PRIOR_GAME_NOT_PLAYED"))
            continue
        proofs = [row for row in rows if _occurred(row, kickoff)]
        if proofs:
            candidates.append((rows[0]["event_start_time"], event_id, max(proofs, key=lambda row: row["receipt"]["retrieved_at"]), ""))
            continue
        if all(row["receipt"]["retrieved_at"] < row["event_start_time"] for row in rows):
            reason = "NCAAF_PRIOR_GAME_NOT_OCCURRED_AT_CUTOFF"
        elif any(row["completed"] is True and row["receipt"]["retrieved_at"] < row["event_start_time"] for row in rows):
            reason = "NCAAF_PRIOR_OCCURRENCE_BEFORE_START"
        else:
            reason = "NCAAF_PRIOR_OCCURRENCE_NOT_VERIFIED"
        candidates.append((rows[0]["event_start_time"], event_id, None, reason))
    # Do not skip an unverified recent game merely because an older game
    # was completed. Only an affirmative cancellation/postponement can be
    # excluded from possible prior occurrences.
    possible = [item for item in candidates if item[3] != "NCAAF_PRIOR_GAME_NOT_PLAYED"]
    pool = possible or candidates
    latest = max(item[0] for item in pool)
    tied = [item for item in pool if item[0] == latest]
    if len(tied) != 1:
        return None, "NCAAF_PRIOR_GAME_AMBIGUOUS", tied[0][1]
    _start, event_id, proof, reason = tied[0]
    if proof is None:
        return None, reason, event_id
    return proof, "", event_id


def _catalog(receipts: list[dict[str, Any]], kickoff: datetime) -> tuple[dict[str, tuple[float, float]] | None, str, dict[str, Any] | None]:
    usable = [receipt for receipt in receipts if receipt["retrieved_at"] < kickoff]
    if not usable:
        return None, ("NCAAF_VENUE_SNAPSHOT_NOT_PREGAME" if receipts else "NCAAF_VENUE_SNAPSHOT_NOT_ARCHIVED"), None
    chosen = max(usable, key=lambda receipt: receipt["retrieved_at"])
    catalog: dict[str, tuple[float, float]] = {}
    for row in chosen["response_rows"]:
        if not isinstance(row, Mapping):
            continue
        venue_id = str(row.get("id") or row.get("venue_id") or "").strip()
        point = _coord(row)
        if not venue_id or point is None:
            continue
        if venue_id in catalog and catalog[venue_id] != point:
            return None, "NCAAF_VENUE_COORDINATE_CONFLICT", chosen
        catalog[venue_id] = point
    return catalog, "", chosen


def _rest_row(game: Mapping[str, Any], *, scope: str, team: str, prior: Mapping[str, Any], evidence_at: datetime, payload: dict[str, Any]) -> dict[str, Any]:
    stamped = dict(payload)
    stamped["scores_used"] = False
    stamped["market_features_used"] = False
    schedule = game["receipt"]
    occurrence = prior["receipt"]
    return {
        "official_event_id": game["official_event_id"],
        "event_start_time": game["event_start_time"].isoformat(),
        "evidence_kind": "REST_TRAVEL",
        "scope": scope,
        "team": team,
        "source_provider": PROVIDER,
        "source_record_id": f"{schedule['source_snapshot_id']}:{prior['official_event_id']}:{scope}",
        "source_uri": f"cfbd:/games#{schedule['source_snapshot_id']}",
        "evidence_timestamp": evidence_at.isoformat(),
        "provenance_grade": "B",
        "payload": stamped,
        "payload_sha256": _hash(stamped),
        "blocker_codes": [],
        "can_execute": False,
        "schedule_source_snapshot_id": schedule["source_snapshot_id"],
        "schedule_payload_sha256": schedule["payload_sha256"],
        "schedule_retrieved_at": schedule["retrieved_at"].isoformat(),
        "occurrence_source_snapshot_id": occurrence["source_snapshot_id"],
        "occurrence_payload_sha256": occurrence["payload_sha256"],
        "occurrence_retrieved_at": occurrence["retrieved_at"].isoformat(),
    }


def acquire_schedule_geometry(
    schedule_receipts: Iterable[Mapping[str, Any]],
    venue_receipts: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Adapt stored receipts into REST_TRAVEL rows. Writes nothing."""
    schedules, schedule_rejected = _load_receipts(schedule_receipts, "/games")
    venues, venue_rejected = _load_receipts(venue_receipts, "/venues")
    sightings = _sightings(schedules)
    by_event: dict[str, list[dict[str, Any]]] = {}
    for game in sightings:
        by_event.setdefault(game["official_event_id"], []).append(game)
    evidence: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for event_id, rows in by_event.items():
        coverage.extend(_family_gaps(event_id))
        if len({_identity(row) for row in rows}) != 1:
            for scope in ("HOME", "AWAY"):
                coverage.append(_coverage(
                    event_id, "REST_TRAVEL", scope,
                    status="MISSING", reason="NCAAF_EVENT_IDENTITY_CONFLICT",
                    timestamp=None, provider=PROVIDER,
                ))
            continue
        kickoff = rows[0]["event_start_time"]
        anchors = [row for row in rows if row["receipt"]["retrieved_at"] < kickoff]
        if not anchors:
            retrieved = max(row["receipt"]["retrieved_at"] for row in rows)
            for scope in ("HOME", "AWAY"):
                coverage.append(_coverage(
                    event_id, "REST_TRAVEL", scope,
                    status="MISSING", reason="NCAAF_SOURCE_RETRIEVED_AFTER_KICKOFF",
                    timestamp=retrieved.isoformat(), provider=PROVIDER,
                ))
            continue
        game = max(anchors, key=lambda row: row["receipt"]["retrieved_at"])
        for scope, team in (("HOME", game["home_team"]), ("AWAY", game["away_team"])):
            prior, reason, prior_id = _prior(team, kickoff, sightings)
            if prior is None:
                coverage.append(_coverage(
                    event_id, "REST_TRAVEL", scope,
                    status="MISSING", reason=reason,
                    timestamp=game["receipt"]["retrieved_at"].isoformat(),
                    provider=PROVIDER, prior_event_id=prior_id,
                ))
                continue
            payload: dict[str, Any] = {
                "rest_days": round((kickoff - prior["event_start_time"]).total_seconds() / 86400.0, 6),
                "prior_event_id": prior["official_event_id"],
                "target_event_id": event_id,
                "neutral_site": game["neutral_site"],
                "reconstruction": "ARCHIVED_SCHEDULE",
                "schedule_source_snapshot_id": game["receipt"]["source_snapshot_id"],
                "schedule_payload_sha256": game["receipt"]["payload_sha256"],
                "schedule_retrieved_at": game["receipt"]["retrieved_at"].isoformat(),
                "occurrence_source_snapshot_id": prior["receipt"]["source_snapshot_id"],
                "occurrence_payload_sha256": prior["receipt"]["payload_sha256"],
                "occurrence_retrieved_at": prior["receipt"]["retrieved_at"].isoformat(),
            }
            evidence_at = max(game["receipt"]["retrieved_at"], prior["receipt"]["retrieved_at"])
            # At neutral venues both teams travel; HOME is not at its
            # ordinary home field. Refuse a complete REST_TRAVEL slot unless
            # verified venue geometry exists for that team as well.
            if scope == "AWAY" or game["neutral_site"] is True:
                catalog, venue_reason, venue_receipt = _catalog(venues, kickoff)
                if catalog is None or venue_receipt is None:
                    coverage.append(_coverage(
                        event_id, "REST_TRAVEL", scope,
                        status="MISSING", reason=venue_reason,
                        timestamp=None if not venues else max(item["retrieved_at"] for item in venues).isoformat(),
                        provider=PROVIDER, prior_event_id=prior["official_event_id"],
                    ))
                    continue
                origin = catalog.get(prior["venue_id"])
                destination = catalog.get(game["venue_id"])
                if origin is None or destination is None:
                    coverage.append(_coverage(
                        event_id, "REST_TRAVEL", scope,
                        status="MISSING", reason="NCAAF_VENUE_COORDINATES_MISSING",
                        timestamp=venue_receipt["retrieved_at"].isoformat(),
                        provider=PROVIDER, prior_event_id=prior["official_event_id"],
                    ))
                    continue
                payload["travel_distance_miles"] = _miles(*origin, *destination)
                payload["venue_source_snapshot_id"] = venue_receipt["source_snapshot_id"]
                payload["venue_payload_sha256"] = venue_receipt["payload_sha256"]
                payload["venue_retrieved_at"] = venue_receipt["retrieved_at"].isoformat()
                evidence_at = max(evidence_at, venue_receipt["retrieved_at"])
            if evidence_at >= kickoff:
                coverage.append(_coverage(
                    event_id, "REST_TRAVEL", scope,
                    status="MISSING", reason="NCAAF_EVIDENCE_TIMESTAMP_NOT_PREGAME",
                    timestamp=evidence_at.isoformat(), provider=PROVIDER,
                    prior_event_id=prior["official_event_id"],
                ))
                continue
            row = _rest_row(game, scope=scope, team=team, prior=prior, evidence_at=evidence_at, payload=payload)
            evidence.append(row)
            coverage.append(_coverage(
                event_id, "REST_TRAVEL", scope,
                status="EVIDENCED", reason="",
                timestamp=row["evidence_timestamp"], provider=PROVIDER,
                digest=row["payload_sha256"], prior_event_id=prior["official_event_id"],
            ))
    complete = []
    scopes_by_event: dict[str, set[str]] = {}
    for row in evidence:
        scopes_by_event.setdefault(row["official_event_id"], set()).add(row["scope"])
    for event_id, scopes in scopes_by_event.items():
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
        "rejected_receipts": schedule_rejected + venue_rejected,
        "schedule_receipts_read": len(schedules),
        "venue_receipts_read": len(venues),
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


def schedule_geometry_feature_row(receipt: Mapping[str, Any], official_event_id: str) -> dict[str, Any]:
    """Build one reduced row only when its fields match the proposed schema."""
    rows = [
        row for row in receipt.get("evidence_rows") or []
        if row.get("official_event_id") == official_event_id and row.get("evidence_kind") == "REST_TRAVEL"
    ]
    home = next((row for row in rows if row.get("scope") == "HOME"), None)
    away = next((row for row in rows if row.get("scope") == "AWAY"), None)
    held = {
        "status": "HELD",
        "feature_schema_version": REDUCED_SCHEMA,
        "official_event_id": official_event_id,
        "model_features": None,
        "v1_compatible": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    if home is None or away is None:
        return {**held, "code": "NCAAF_SCHEDULE_GEOMETRY_INCOMPLETE"}
    neutral_values = {home["payload"].get("neutral_site"), away["payload"].get("neutral_site")}
    if neutral_values != {home["payload"].get("neutral_site")} or not (neutral_values <= {True, False}) or None in neutral_values:
        return {**held, "code": "NCAAF_NEUTRAL_SITE_NOT_ON_SOURCE"}
    neutral = home["payload"]["neutral_site"]
    if neutral is True and (
        not isinstance(home["payload"].get("travel_distance_miles"), (int, float))
        or not isinstance(away["payload"].get("travel_distance_miles"), (int, float))
    ):
        return {**held, "code": "NCAAF_NEUTRAL_HOME_TRAVEL_UNVERIFIED"}
    features = {
        "home_rest_days": home["payload"]["rest_days"],
        "away_rest_days": away["payload"]["rest_days"],
        "travel_distance_miles": away["payload"]["travel_distance_miles"],
        "neutral_site": neutral,
    }
    if tuple(features) != REDUCED_MODEL_FEATURES:
        return {**held, "code": "NCAAF_REDUCED_SCHEMA_MISMATCH"}
    return {
        "status": "REDUCED_ROW_HELD",
        "code": "NCAAF_SCHEDULE_GEOMETRY_ROWS_INSUFFICIENT",
        "feature_schema_version": REDUCED_SCHEMA,
        "official_event_id": official_event_id,
        "model_features": features,
        "home_prior_event_id": home["payload"]["prior_event_id"],
        "away_prior_event_id": away["payload"]["prior_event_id"],
        "schedule_source_snapshot_id": home["schedule_source_snapshot_id"],
        "schedule_payload_sha256": home["schedule_payload_sha256"],
        "venue_source_snapshot_id": away["payload"].get("venue_source_snapshot_id"),
        "venue_payload_sha256": away["payload"].get("venue_payload_sha256"),
        # HOME travel is provenance, not a silently added fitted-model input.
        # The reduced 4-feature contract is unchanged and remains unpromoted.
        "neutral_home_travel_distance_miles": (
            home["payload"].get("travel_distance_miles") if neutral else None
        ),
        "evidence_timestamps": {"HOME": home["evidence_timestamp"], "AWAY": away["evidence_timestamp"]},
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
        "model_features": list(REDUCED_MODEL_FEATURES),
        "minimum_complete_rows": MINIMUM_REDUCED_ROWS,
        "split": "CHRONOLOGICAL_60_20_20",
        "validation_requirements": [
            "prior occurrence receipt retrieved at or after prior start and before target kickoff",
            "canceled, postponed, and rescheduled priors rejected",
            "venue coordinates from a hashed /venues receipt retrieved before kickoff",
            "neutral_site copied from the target schedule receipt, with no default",
            "neutral-site HOME and AWAY travel both require pregame venue geometry; HOME distance is held as separate provenance, not an extra model input",
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
    "REDUCED_MODEL_FEATURES",
    "REDUCED_SCHEMA",
    "SLOT_POLICY",
    "acquire_schedule_geometry",
    "reduced_challenger_contract",
    "schedule_geometry_feature_row",
]
