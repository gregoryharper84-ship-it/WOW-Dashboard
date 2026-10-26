"""Read-only NHL game-ending-regime source for research-only total-goals modeling.

The NHL first-party club-schedule-season API exposes gameOutcome.lastPeriodType
for completed games. This is settled historical outcome metadata, *never*
an archived pregame feature. Use it to construct REG/OT/SO training labels
separate from the probability model and its prospective evidence contract.

No sportsbook odds, future information or market pricing enter any feature.
No serving scorer, model calibration, live execution or auto-promotion.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
SOURCE_ID = "NHL_PUBLIC_WEB_API"
VALID_REGIMES = frozenset({"REG", "OT", "SO"})
GOAL_COUNT_DEFINITION = "NHL_OFFICIAL_FULL_GAME_FINAL_SCORE_OT_SO_INCLUDED"


class NHLTotalsRegimeSourceError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class NHLOfficialRegimeResult:
    official_event_id: str
    season_id: int
    event_start_utc: str
    home_team: str
    away_team: str
    final_home_goals: int
    final_away_goals: int
    final_period_type: str
    source_game_sha256: str
    goal_count_definition: str = GOAL_COUNT_DEFINITION
    historically_reconstructed: bool = True
    archived_pregame_snapshot: bool = False
    probability_publishable: bool = False
    can_execute: bool = False

    @property
    def full_game_total(self) -> int:
        return self.final_home_goals + self.final_away_goals


def _source_hash(raw: Mapping[str, Any]) -> str:
    return sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, default=str).encode()).hexdigest()


def _normalized(raw: Mapping[str, Any]) -> NHLOfficialRegimeResult | None:
    if not isinstance(raw, Mapping):
        raise NHLTotalsRegimeSourceError("NHL_TOTALS_SCHEDULE_ROW_INVALID", "row must be mapping")
    if raw.get("gameType") != 2 or raw.get("gameState") not in {"OFF", "FINAL"}:
        return None
    try:
        event_id = str(raw["id"])
        season = int(raw["season"])
        start = datetime.fromisoformat(str(raw["startTimeUTC"]).replace("Z", "+00:00"))
        home_team = str(raw["homeTeam"]["abbrev"]).strip().upper()
        away_team = str(raw["awayTeam"]["abbrev"]).strip().upper()
        home_score = raw["homeTeam"]["score"]
        away_score = raw["awayTeam"]["score"]
        period = raw["gameOutcome"]["lastPeriodType"]
        if (len(event_id) != 10 or not event_id.isdecimal()
            or event_id[:4] != str(season)[:4]
            or event_id[4:6] != "02"
            or season != int(str(season)[:4]) * 10000 + int(str(season)[:4]) + 1):
            raise ValueError("season or regular-game identifier invalid")
        if start.utcoffset() is None:
            raise ValueError("timezone absent")
        if not home_team or not away_team or home_team == away_team:
            raise ValueError("home/away identity invalid")
        if (type(home_score) is not int or type(away_score) is not int
            or min(home_score, away_score) < 0 or home_score == away_score):
            raise ValueError("final scores invalid")
        if period not in VALID_REGIMES:
            raise ValueError("period outcome not recognized")
        if period in {"OT", "SO"} and abs(home_score - away_score) != 1:
            raise ValueError("one-goal official final margin required after OT/SO")
        if period in {"OT", "SO"} and (home_score + away_score) % 2 != 1:
            raise ValueError("full-game official OT/SO goal parity inconsistent")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise NHLTotalsRegimeSourceError("NHL_TOTALS_SCHEDULE_ROW_INVALID", str(exc)) from exc
    return NHLOfficialRegimeResult(
        official_event_id=event_id, season_id=season,
        event_start_utc=start.astimezone(timezone.utc).isoformat(),
        home_team=home_team, away_team=away_team,
        final_home_goals=home_score, final_away_goals=away_score,
        final_period_type=period, source_game_sha256=_source_hash(raw),
    )


def normalize_official_regime_schedule(
    schedule: Mapping[str, Any], *, expected_season: int, requested_team: str
) -> list[NHLOfficialRegimeResult]:
    """Parse one authoritative club schedule; reject incomplete source coverage."""
    if not isinstance(schedule, Mapping) or not isinstance(schedule.get("games"), list):
        raise NHLTotalsRegimeSourceError("NHL_TOTALS_SCHEDULE_SCHEMA_INVALID", "games array missing")
    team = str(requested_team or "").strip().upper()
    if len(team) not in (3,):
        raise NHLTotalsRegimeSourceError("NHL_TOTALS_SCHEDULE_TEAM_INVALID", team)
    result = []
    for raw in schedule["games"]:
        row = _normalized(raw)
        if row is None:
            continue
        if row.season_id != expected_season or team not in (row.home_team, row.away_team):
            raise NHLTotalsRegimeSourceError("NHL_TOTALS_SCHEDULE_IDENTITY_DRIFT", row.official_event_id)
        result.append(row)
    if len(set(r.official_event_id for r in result)) != len(result):
        raise NHLTotalsRegimeSourceError("NHL_TOTALS_SCHEDULE_DUPLICATE_EVENT", team)
    return result


def reconcile_official_regime_games(
    team_schedules: Sequence[Sequence[NHLOfficialRegimeResult]],
) -> tuple[list[NHLOfficialRegimeResult], dict[str, Any]]:
    """Deduplicate team mirrors with strict event/result agreement, preserving hashes.

    Multiple NHL team schedule views may have different payload hashes while
    reporting the same verified event/result; never manufacture extra games.
    """
    grouped: dict[tuple[int, str], list[NHLOfficialRegimeResult]] = defaultdict(list)
    instances = 0
    for schedule in team_schedules:
        for row in schedule:
            if not isinstance(row, NHLOfficialRegimeResult):
                raise NHLTotalsRegimeSourceError("NHL_TOTALS_REGIME_SOURCE_TYPE_INVALID", "untyped row")
            if row.can_execute or row.probability_publishable or not row.historically_reconstructed:
                raise NHLTotalsRegimeSourceError("NHL_TOTALS_REGIME_GOVERNANCE_INVALID", row.official_event_id)
            grouped[(row.season_id, row.official_event_id)].append(row)
            instances += 1
    if not grouped:
        raise NHLTotalsRegimeSourceError("NHL_TOTALS_REGIME_SOURCE_EMPTY", "no completed NHL games")
    result = []
    ledger = []
    for (season, game_id), versions in sorted(grouped.items()):
        identities = {(
            r.event_start_utc, r.home_team, r.away_team,
            r.final_home_goals, r.final_away_goals, r.final_period_type,
            r.goal_count_definition
        ) for r in versions}
        if len(identities) != 1:
            raise NHLTotalsRegimeSourceError("NHL_TOTALS_REGIME_MIRROR_CONFLICT", game_id)
        hashes = sorted({r.source_game_sha256 for r in versions})
        if len(versions) > 1:
            ledger.append({"season": season, "event_id": game_id,
                           "source_instance_count": len(versions), "source_hashes": hashes})
        result.append(versions[0])
    result.sort(key=lambda x: (x.event_start_utc, x.official_event_id))
    return result, {
        "source_instances": instances, "unique_official_games": len(result),
        "mirrored_event_count": len(ledger), "source_version_ledger": ledger,
        "historical_reconstruction": True,
        "archived_pregame_snapshot": False, "probability_publishable": False,
        "can_execute": False,
    }
