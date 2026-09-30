"""Audited WNBA Stats -> ESPN team identity aliases for V17.

Provider-specific abbreviation differences were independently observed by exact
team-name equality in the pinned 2026 WNBA Stats + ESPN corpora used by
``scripts/probe_wnba_2026_training_join.py``. ESPN numeric team IDs were
verified against the public ESPN WNBA teams endpoint on 2026-09-30.

This module translates identity only. It grants no probability, calibration,
publication, ranking, promotion, or execution authority.
"""
from __future__ import annotations

CAN_EXECUTE = False
ESPN_WNBA_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams"

WNBA_STATS_TO_ESPN_ABBREVIATION = {
    "ATL": "ATL",
    "CHI": "CHI",
    "CON": "CON",
    "DAL": "DAL",
    "GSV": "GS",
    "IND": "IND",
    "LVA": "LV",
    "LAS": "LA",
    "MIN": "MIN",
    "NYL": "NY",
    "PHX": "PHX",
    "PDX": "POR",
    "SEA": "SEA",
    "TOR": "TOR",
    "WAS": "WSH",
}

ESPN_WNBA_TEAM_ID_BY_ABBREVIATION = {
    "ATL": "20",
    "CHI": "19",
    "CON": "18",
    "DAL": "3",
    "GS": "129689",
    "IND": "5",
    "LV": "17",
    "LA": "6",
    "MIN": "8",
    "NY": "9",
    "PHX": "11",
    "POR": "132052",
    "SEA": "14",
    "TOR": "131935",
    "WSH": "16",
}


def espn_team_id_for_wnba_stats_tricode(value: object) -> str:
    tricode = str(value or "").strip().upper()
    abbreviation = WNBA_STATS_TO_ESPN_ABBREVIATION.get(tricode)
    if not abbreviation:
        raise ValueError(f"WNBA_STATS_TRICODE_UNMAPPED:{tricode or 'EMPTY'}")
    team_id = ESPN_WNBA_TEAM_ID_BY_ABBREVIATION.get(abbreviation)
    if not team_id:
        raise ValueError(f"WNBA_ESPN_TEAM_ID_UNMAPPED:{abbreviation}")
    return f"espn-{team_id}"


__all__ = [
    "CAN_EXECUTE",
    "ESPN_WNBA_TEAM_ID_BY_ABBREVIATION",
    "ESPN_WNBA_TEAMS_URL",
    "WNBA_STATS_TO_ESPN_ABBREVIATION",
    "espn_team_id_for_wnba_stats_tricode",
]
