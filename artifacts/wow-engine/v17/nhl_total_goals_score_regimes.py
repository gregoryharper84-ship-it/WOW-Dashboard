"""Research-only NHL final-score scoring-regime reconciliation.

Uses official NHL gamecenter landing scoring summary (not sportsbook data).
Separates 60-minute regulation goals, actual OT goals, and the *awarded*
single shootout goal in the official final team score. All stats in this
module are RETROSPECTIVE settled outcomes, never pregame model features.
Only valid NHL regular-season full-game final scores are accepted; schema
drift or non-reconciling goal totals fails closed.

This provides source accounting only; it is NOT a fitted model or calibrated
V17 probability package.
"""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False

class NHLScoringRegimeError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class HistoricalScoreRegime:
    event_id: str
    season: int
    final_period_type: str
    regulation_home_goals: int
    regulation_away_goals: int
    actual_overtime_goals: int
    shootout_awarded_goal: int
    official_home_score: int
    official_away_score: int
    empty_net_goals: int
    source_payload_sha256: str
    historical_reconstruction: bool = True
    probability_publishable: bool = False
    can_execute: bool = False

    @property
    def official_final_total(self) -> int:
        return self.official_home_score + self.official_away_score

    @property
    def reconciled_total(self) -> int:
        return (self.regulation_home_goals + self.regulation_away_goals
                + self.actual_overtime_goals + self.shootout_awarded_goal)


def reconcile_official_gamecenter_landing(payload: Mapping[str, Any]) -> HistoricalScoreRegime:
    if not isinstance(payload, Mapping):
        raise NHLScoringRegimeError("NHL_TOTALS_LANDING_SOURCE_INVALID", "expected mapping")
    try:
        event_id = str(payload["id"]).strip()
        season = int(payload["season"])
        if not event_id or len(str(season)) != 8:
            raise ValueError("event id or season")
        if payload["gameState"] not in {"OFF", "FINAL"} or int(payload["gameType"]) != 2:
            raise ValueError("not completed regular season")
        period = str(payload["periodDescriptor"]["periodType"]).upper()
        if period not in {"REG", "OT", "SO"}:
            raise ValueError("unsupported final period type")
        home = payload["homeTeam"]
        away = payload["awayTeam"]
        home_abbrev = str(home["abbrev"]).upper()
        away_abbrev = str(away["abbrev"]).upper()
        if not home_abbrev or not away_abbrev or home_abbrev == away_abbrev:
            raise ValueError("team identity")
        hs, aas = home["score"], away["score"]
        if type(hs) is not int or type(aas) is not int or min(hs, aas) < 0 or hs == aas:
            raise ValueError("official final scores")
        scoring = payload["summary"]["scoring"]
        if not isinstance(scoring, list):
            raise ValueError("scoring periods missing")
        reg_home = reg_away = ot_home = ot_away = empty_net = 0
        saw_ot = False
        for period_row in scoring:
            pdesc = period_row["periodDescriptor"]
            period_type = pdesc["periodType"]
            number = pdesc["number"]
            if type(number) is not int:
                raise ValueError("period number invalid")
            goals = period_row["goals"]
            if not isinstance(goals, list):
                raise ValueError("period goals invalid")
            if period_type == "REG":
                if number not in (1, 2, 3):
                    raise ValueError("regulation period invalid")
            elif period_type == "OT":
                if number != 4 or saw_ot:
                    raise ValueError("overtime period invalid")
                saw_ot = True
            elif period_type == "SO":
                # Shootout attempts are not counted as scored skater goals.
                if goals:
                    raise ValueError("shootout summary requires separate adapter")
                continue
            else:
                raise ValueError("unsupported scoring period")
            for goal in goals:
                if type(goal.get("isHome")) is not bool:
                    raise ValueError("scoring-side identity missing")
                is_home = goal["isHome"]
                expected = home_abbrev if is_home else away_abbrev
                if str(goal.get("teamAbbrev") or "").upper() != expected:
                    raise ValueError("scoring team conflict")
                if period_type == "REG":
                    if is_home:
                        reg_home += 1
                    else:
                        reg_away += 1
                else:
                    if is_home:
                        ot_home += 1
                    else:
                        ot_away += 1
                if goal.get("goalModifier") == "empty-net":
                    empty_net += 1
        if period == "REG":
            if saw_ot or (hs, aas) != (reg_home, reg_away):
                raise ValueError("regulation final score mismatch")
            shootout_awarded = 0
        elif period == "OT":
            if reg_home != reg_away or (ot_home + ot_away) != 1:
                raise ValueError("overtime must break regulation tie")
            if (hs, aas) != (reg_home + ot_home, reg_away + ot_away):
                raise ValueError("overtime score mismatch")
            shootout_awarded = 0
        else:
            if reg_home != reg_away or ot_home or ot_away:
                raise ValueError("shootout requires game tied through overtime")
            diffs = (hs - reg_home, aas - reg_away)
            if sorted(diffs) != [0, 1]:
                raise ValueError("shootout final must award exactly one goal")
            shootout_awarded = 1
        official_total = hs + aas
        reconstructed = reg_home + reg_away + ot_home + ot_away + shootout_awarded
        if official_total != reconstructed:
            raise ValueError("total goal mismatch")
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise NHLScoringRegimeError("NHL_TOTALS_FINAL_SCORE_REGIME_MISMATCH", str(exc)) from exc
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return HistoricalScoreRegime(
        event_id=event_id, season=season, final_period_type=period,
        regulation_home_goals=reg_home, regulation_away_goals=reg_away,
        actual_overtime_goals=ot_home + ot_away,
        shootout_awarded_goal=shootout_awarded,
        official_home_score=hs, official_away_score=aas, empty_net_goals=empty_net,
        source_payload_sha256=digest,
    )
