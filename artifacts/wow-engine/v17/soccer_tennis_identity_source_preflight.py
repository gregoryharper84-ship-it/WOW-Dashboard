"""Read-only soccer/tennis fixture source preflight; never a canonicalizer.

The Odds API and TheRundown IDs are market aliases. A provider fixture can
corroborate a participant crosswalk, but it cannot authorize an official WOW
identity, model certification, market prediction or publication by itself.

Source permission, API access and independent identity authority remain separate
external proofs. This module deliberately does not fetch or score anything.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

CAN_EXECUTE = False
MATCH_WINDOW_SECONDS = 30 * 60
SOURCES = {"SOCCER": "FOOTBALL_DATA_ORG_V4", "TENNIS": "TENNIS_API_V2"}


def _utc(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else None


def _id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    token = str(value or "").strip()
    return token if token.isascii() and token.isdecimal() and 0 < len(token) <= 16 and int(token) > 0 else None


def _hold(code: str, *, source_id: str | None = None, match_id: str | None = None) -> dict[str, Any]:
    return {
        "status": "HOLD" if match_id is None else "SOURCE_MATCH_CANDIDATE_ONLY",
        "code": code,
        "source_id": source_id,
        "candidate_source_match_id": match_id,
        "canonical_identity_status": "ALIAS_ONLY_UNRESOLVED",
        "official_event_id": None,
        "canonicalization_permitted": False,
        "source_rights_independently_attested": False,
        "source_access_live_verified": False,
        "market_features_used": False,
        "prediction_authority": False,
        "research_only": True,
        "model_probability": None,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def preflight_fixture_source(
    *, sport: str, source_id: str, source_payload: Mapping[str, Any],
    crosswalk: Mapping[str, Any], as_of_utc: str,
) -> dict[str, Any]:
    """Require exactly one source fixture with stable IDs and an eligible start.

    Crosswalk IDs must come from independent participant reconciliation, never
    guessed from a sportsbook name. A positive result remains research-only.
    """
    sport = str(sport or "").upper()
    if source_id != SOURCES.get(sport):
        return _hold("SOURCE_EVIDENCE_UNVERIFIED", source_id=source_id)
    if not isinstance(source_payload, Mapping) or not isinstance(crosswalk, Mapping):
        return _hold("SOURCE_FIXTURE_SCHEMA_INVALID", source_id=source_id)

    start = _utc(crosswalk.get("start_time_utc"))
    as_of = _utc(as_of_utc)
    if start is None or as_of is None:
        return _hold("SOURCE_FIXTURE_SCHEMA_INVALID", source_id=source_id)
    if start <= as_of:
        return _hold("SOURCE_FIXTURE_INELIGIBLE", source_id=source_id)
    if sport == "SOCCER":
        rows = source_payload.get("matches")
        result_set = source_payload.get("resultSet")
        if not isinstance(result_set, Mapping) or (
            not isinstance(result_set.get("count"), int)
            or isinstance(result_set.get("count"), bool)
            or not isinstance(rows, list)
            or result_set["count"] != len(rows)
        ):
            return _hold("SOURCE_FIXTURE_PARTIAL", source_id=source_id)
        competition = _id(crosswalk.get("competition_id"))
        home = _id(crosswalk.get("home_team_id"))
        away = _id(crosswalk.get("away_team_id"))
        if not all((competition, home, away)) or home == away:
            return _hold("SOURCE_EVIDENCE_UNVERIFIED", source_id=source_id)
    else:
        rows = source_payload.get("data")
        if not isinstance(rows, list) or source_payload.get("hasNextPage") is not False:
            return _hold("SOURCE_FIXTURE_PARTIAL", source_id=source_id)
        tour = str(crosswalk.get("tour") or "").upper()
        tournament = _id(crosswalk.get("tournament_id"))
        player1 = _id(crosswalk.get("player1_id"))
        player2 = _id(crosswalk.get("player2_id"))
        if tour not in ("ATP", "WTA") or not all((tournament, player1, player2)) or player1 == player2:
            return _hold("SOURCE_EVIDENCE_UNVERIFIED", source_id=source_id)
        if crosswalk.get("singles") is not True:
            return _hold("SOURCE_FIXTURE_INELIGIBLE", source_id=source_id)

    matches: list[str] = []
    for fixture in rows:
        if not isinstance(fixture, Mapping):
            return _hold("SOURCE_FIXTURE_SCHEMA_INVALID", source_id=source_id)
        if sport == "SOCCER":
            comp, h, a = fixture.get("competition"), fixture.get("homeTeam"), fixture.get("awayTeam")
            if not all(isinstance(x, Mapping) for x in (comp, h, a)):
                return _hold("SOURCE_FIXTURE_SCHEMA_INVALID", source_id=source_id)
            comparable = (
                _id(comp.get("id")) == competition
                and _id(h.get("id")) == home
                and _id(a.get("id")) == away
            )
            fixture_start = _utc(fixture.get("utcDate"))
            fixture_id = _id(fixture.get("id"))
            status = str(fixture.get("status") or "").upper()
            eligible = status in ("SCHEDULED", "TIMED")
        else:
            p1, p2, event = fixture.get("player1"), fixture.get("player2"), fixture.get("tournament")
            if not all(isinstance(x, Mapping) for x in (p1, p2, event)):
                return _hold("SOURCE_FIXTURE_SCHEMA_INVALID", source_id=source_id)
            ids = {_id(p1.get("id")), _id(p2.get("id"))}
            comparable = (
                _id(event.get("id")) == tournament
                and ids == {player1, player2} and None not in ids
            )
            fixture_start = _utc(fixture.get("startTime") or fixture.get("date"))
            fixture_id = _id(fixture.get("matchId"))
            status = str(fixture.get("status") or "").upper()
            eligible = status in ("SCHEDULED", "UPCOMING", "NOT_STARTED")
            if fixture.get("retirement") or fixture.get("withdrawal") or fixture.get("walkover"):
                eligible = False
        if not comparable:
            continue
        if fixture_start is None or fixture_id is None:
            return _hold("SOURCE_FIXTURE_SCHEMA_INVALID", source_id=source_id)
        if abs((fixture_start - start).total_seconds()) > MATCH_WINDOW_SECONDS:
            continue
        if not eligible:
            return _hold("SOURCE_FIXTURE_INELIGIBLE", source_id=source_id)
        matches.append(fixture_id)
    if len(matches) > 1:
        return _hold("SOURCE_FIXTURE_AMBIGUOUS", source_id=source_id)
    if not matches:
        return _hold("SOURCE_FIXTURE_NOT_FOUND", source_id=source_id)
    return _hold("IDENTITY_UNRESOLVED", source_id=source_id, match_id=matches[0])
