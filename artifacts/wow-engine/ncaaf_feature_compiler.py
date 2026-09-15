"""Compile governed NCAAF pregame evidence into the exact fitted feature schema.

The compiler is intentionally all-or-nothing. A training feature row is persisted
only when every model-required field is supported by eligible pregame evidence
for the exact event/team identity. Sportsbook/market evidence is never accepted
as a fitted-model feature and no default/imputation is invented here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Iterable, Mapping

FEATURE_SCHEMA_VERSION = "NCAAF_FEATURES_V1"
COMPILER_VERSION = "NCAAF_EVIDENCE_FEATURE_COMPILER_V1"
CAN_EXECUTE = False

TEAM_VALUE_KIND_TO_COLUMN = {
    "TEAM_POWER": "power_rating",
    "OFF_EPA": "off_epa",
    "DEF_EPA": "def_epa",
    "SUCCESS_RATE": "success_rate",
    "EXPLOSIVENESS": "explosiveness",
    "QB_VALUE": "qb_value",
    "QB_CERTAINTY": "qb_certainty",
    "OL_HEALTH": "ol_health",
    "DEF_FRONT_HEALTH": "def_front_health",
    "SKILL_AVAILABILITY": "skill_availability",
    "TEMPO": "tempo",
    "TURNOVER_VOLATILITY": "turnover_volatility",
    "SPECIAL_TEAMS": "special_teams_rating",
}
REQUIRED_TEAM_VALUE_KINDS = tuple(TEAM_VALUE_KIND_TO_COLUMN)
REQUIRED_SCOPED_KEYS = tuple(
    (kind, scope)
    for kind in REQUIRED_TEAM_VALUE_KINDS
    for scope in ("HOME", "AWAY")
) + (("REST_TRAVEL", "HOME"), ("REST_TRAVEL", "AWAY"), ("WEATHER", "EVENT"))
ELIGIBLE_PROVENANCE_GRADES = frozenset({"A", "B", "C"})


class NCAAFFeatureCompileBlocked(RuntimeError):
    def __init__(self, code: str, blockers: Iterable[str]):
        self.code = code
        self.blockers = tuple(sorted(set(str(v) for v in blockers)))
        super().__init__(f"{code}:{','.join(self.blockers)}")


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field}:boolean_not_numeric")
    parsed = float(value)
    if not isfinite(parsed):
        raise ValueError(f"{field}:nonfinite")
    return parsed


def _blockers_empty(value: Any) -> bool:
    return value in (None, [], (), {})


def _eligible_row(
    row: Mapping[str, Any],
    *,
    game: Mapping[str, Any],
    event_start: datetime,
) -> tuple[bool, str | None]:
    if str(row.get("official_event_id") or "") != str(game.get("official_event_id") or ""):
        return False, "EVENT_ID_MISMATCH"
    if row.get("can_execute") is not False:
        return False, "CAN_EXECUTE_INVALID"
    if str(row.get("provenance_grade") or "").upper() not in ELIGIBLE_PROVENANCE_GRADES:
        return False, "PROVENANCE_GRADE_INELIGIBLE"
    if not _blockers_empty(row.get("blocker_codes")):
        return False, "EVIDENCE_BLOCKED"
    if not str(row.get("source_provider") or "").strip():
        return False, "SOURCE_PROVIDER_MISSING"
    if not str(row.get("payload_sha256") or "").strip():
        return False, "PAYLOAD_HASH_MISSING"
    try:
        evidence_at = _aware(row.get("evidence_timestamp"))
    except Exception:
        return False, "EVIDENCE_TIMESTAMP_INVALID"
    if evidence_at >= event_start:
        return False, "EVIDENCE_NOT_PREGAME"

    scope = str(row.get("scope") or "").upper()
    expected_team = None
    if scope == "HOME":
        expected_team = str(game.get("home_team") or "").strip()
    elif scope == "AWAY":
        expected_team = str(game.get("away_team") or "").strip()
    if expected_team is not None:
        actual_team = str(row.get("team") or "").strip()
        if not actual_team or actual_team.casefold() != expected_team.casefold():
            return False, "TEAM_IDENTITY_MISMATCH"
    return True, None


def _latest_evidence(
    evidence_rows: Iterable[Mapping[str, Any]],
    *,
    game: Mapping[str, Any],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    event_start = _aware(game.get("event_start_time"))
    chosen: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in evidence_rows:
        eligible, _reason = _eligible_row(row, game=game, event_start=event_start)
        if not eligible:
            continue
        key = (str(row.get("evidence_kind") or "").upper(), str(row.get("scope") or "").upper())
        current = chosen.get(key)
        if current is None or _aware(row.get("evidence_timestamp")) > _aware(current.get("evidence_timestamp")):
            chosen[key] = row
    return chosen


def _payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("payload")
    if not isinstance(value, Mapping):
        raise ValueError("payload_not_object")
    return value


def _manifest_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "evidence_id": str(row.get("evidence_id") or ""),
        "evidence_kind": str(row.get("evidence_kind") or "").upper(),
        "scope": str(row.get("scope") or "").upper(),
        "team": row.get("team"),
        "source_provider": str(row.get("source_provider") or ""),
        "evidence_timestamp": str(row.get("evidence_timestamp") or ""),
        "payload_sha256": str(row.get("payload_sha256") or ""),
        "provenance_grade": str(row.get("provenance_grade") or "").upper(),
    }


def compile_training_feature_row(
    game: Mapping[str, Any],
    evidence_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    event_id = str(game.get("official_event_id") or "").strip()
    home_team = str(game.get("home_team") or "").strip()
    away_team = str(game.get("away_team") or "").strip()
    if not event_id or not home_team or not away_team or not game.get("training_game_id"):
        raise NCAAFFeatureCompileBlocked("NCAAF_FEATURE_IDENTITY_INCOMPLETE", [event_id or "EVENT_ID_MISSING"])

    try:
        event_start = _aware(game.get("event_start_time"))
    except Exception as exc:
        raise NCAAFFeatureCompileBlocked("NCAAF_EVENT_START_INVALID", [type(exc).__name__]) from exc

    selected = _latest_evidence(evidence_rows, game=game)
    missing = [f"{kind}:{scope}" for kind, scope in REQUIRED_SCOPED_KEYS if (kind, scope) not in selected]
    if missing:
        raise NCAAFFeatureCompileBlocked("NCAAF_MODEL_FEATURE_EVIDENCE_INCOMPLETE", missing)

    out: dict[str, Any] = {
        "training_game_id": str(game["training_game_id"]),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "market_home_no_vig": None,
        "market_away_no_vig": None,
        "market_timestamp": None,
        "can_execute": False,
    }
    used: list[Mapping[str, Any]] = []
    blockers: list[str] = []

    for kind, suffix in TEAM_VALUE_KIND_TO_COLUMN.items():
        for scope, prefix in (("HOME", "home"), ("AWAY", "away")):
            row = selected[(kind, scope)]
            used.append(row)
            payload = _payload(row)
            try:
                value = _number(payload.get("value"), field=f"{kind}:{scope}:value")
            except Exception:
                blockers.append(f"{kind}:{scope}:VALUE_INVALID")
                continue
            if kind == "QB_CERTAINTY" and not 0.0 <= value <= 1.0:
                blockers.append(f"{kind}:{scope}:OUT_OF_RANGE")
            out[f"{prefix}_{suffix}"] = value

    home_rest = selected[("REST_TRAVEL", "HOME")]
    away_rest = selected[("REST_TRAVEL", "AWAY")]
    used.extend((home_rest, away_rest))
    try:
        out["home_rest_days"] = _number(_payload(home_rest).get("rest_days"), field="REST_TRAVEL:HOME:rest_days")
        away_payload = _payload(away_rest)
        out["away_rest_days"] = _number(away_payload.get("rest_days"), field="REST_TRAVEL:AWAY:rest_days")
        out["travel_distance_miles"] = _number(
            away_payload.get("travel_distance_miles"), field="REST_TRAVEL:AWAY:travel_distance_miles"
        )
    except Exception:
        blockers.append("REST_TRAVEL_PAYLOAD_INVALID")

    weather = selected[("WEATHER", "EVENT")]
    used.append(weather)
    weather_payload = _payload(weather)
    try:
        out["weather_wind_mph"] = _number(weather_payload.get("wind_mph"), field="WEATHER:wind_mph")
        out["weather_precip_probability"] = _number(
            weather_payload.get("precip_probability"), field="WEATHER:precip_probability"
        )
        if not 0.0 <= out["weather_precip_probability"] <= 1.0:
            blockers.append("WEATHER_PRECIP_PROBABILITY_OUT_OF_RANGE")
        temperature = weather_payload.get("temperature_f")
        out["weather_temperature_f"] = None if temperature in (None, "") else _number(
            temperature, field="WEATHER:temperature_f"
        )
    except Exception:
        blockers.append("WEATHER_PAYLOAD_INVALID")

    if blockers:
        raise NCAAFFeatureCompileBlocked("NCAAF_MODEL_FEATURE_PAYLOAD_INVALID", blockers)

    feature_as_of = max(_aware(row.get("evidence_timestamp")) for row in used)
    if feature_as_of >= event_start:
        raise NCAAFFeatureCompileBlocked("NCAAF_FEATURE_TEMPORAL_LEAKAGE", ["FEATURE_AS_OF_NOT_PREGAME"])
    out["feature_as_of"] = feature_as_of.isoformat()
    out["feature_source_manifest"] = {
        "compiler_version": COMPILER_VERSION,
        "official_event_id": event_id,
        "event_start_time": event_start.isoformat(),
        "home_team": home_team,
        "away_team": away_team,
        "market_features_used": False,
        "evidence": [_manifest_entry(row) for row in sorted(
            used,
            key=lambda r: (str(r.get("evidence_kind")), str(r.get("scope")), str(r.get("evidence_timestamp"))),
        )],
        "can_execute": False,
    }
    return out


def _paged(db: Any, table: str, columns: str, *, page_size: int = 1000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        result = db.table(table).select(columns).range(start, start + page_size - 1).execute()
        batch = list(result.data or [])
        rows.extend(dict(row) for row in batch)
        if len(batch) < page_size:
            return rows
        start += page_size


def materialize_complete_training_features(db: Any) -> dict[str, Any]:
    games = _paged(
        db,
        "wow_ncaaf_training_games",
        "training_game_id,official_event_id,season,event_start_time,neutral_site,home_team,away_team,home_won,can_execute",
    )
    evidence = _paged(
        db,
        "wow_ncaaf_pregame_evidence",
        "evidence_id,official_event_id,event_start_time,evidence_kind,scope,team,source_provider,evidence_timestamp,payload,payload_sha256,provenance_grade,blocker_codes,can_execute",
    )
    existing = _paged(
        db,
        "wow_ncaaf_training_features",
        "training_game_id,feature_schema_version,feature_as_of",
    )
    existing_ids = {
        str(row.get("training_game_id"))
        for row in existing
        if str(row.get("feature_schema_version")) == FEATURE_SCHEMA_VERSION
    }
    by_event: dict[str, list[dict[str, Any]]] = {}
    for row in evidence:
        by_event.setdefault(str(row.get("official_event_id") or ""), []).append(row)

    persisted = 0
    blocked = 0
    skipped_existing = 0
    blocker_counts: dict[str, int] = {}
    blocker_samples: list[dict[str, Any]] = []

    for game in games:
        game_id = str(game.get("training_game_id") or "")
        if not game_id or game.get("home_won") is None or game.get("can_execute") is not False:
            continue
        if game_id in existing_ids:
            skipped_existing += 1
            continue
        try:
            feature = compile_training_feature_row(
                game,
                by_event.get(str(game.get("official_event_id") or ""), []),
            )
        except NCAAFFeatureCompileBlocked as exc:
            blocked += 1
            blocker_counts[exc.code] = blocker_counts.get(exc.code, 0) + 1
            if len(blocker_samples) < 20:
                blocker_samples.append({
                    "official_event_id": game.get("official_event_id"),
                    "code": exc.code,
                    "blockers": list(exc.blockers)[:20],
                })
            continue
        db.table("wow_ncaaf_training_features").insert(feature).execute()
        persisted += 1

    complete_rows = len(existing_ids) + persisted
    return {
        "status": "COMPLETE" if blocked == 0 else ("PARTIAL" if complete_rows else "BLOCKED"),
        "games_seen": len(games),
        "evidence_rows_seen": len(evidence),
        "features_persisted": persisted,
        "features_existing": skipped_existing,
        "complete_feature_rows": complete_rows,
        "blocked_games": blocked,
        "blocker_counts": blocker_counts,
        "blocker_samples": blocker_samples,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "market_features_used": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "COMPILER_VERSION",
    "FEATURE_SCHEMA_VERSION",
    "NCAAFFeatureCompileBlocked",
    "compile_training_feature_row",
    "materialize_complete_training_features",
]
