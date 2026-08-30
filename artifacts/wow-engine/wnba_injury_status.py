"""Exact parser for official WNBA injury-report player designations.

The PDF text extractor flattens tabular rows.  This parser deliberately accepts
only a status token immediately following the matched target player name.  It
never searches arbitrarily farther into the team section, preventing a later
player's status from being attributed to the target player.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any


STATUSES = ("available", "out", "doubtful", "questionable", "probable")


class WNBAInjuryStatusError(RuntimeError):
    def __init__(self, code: str, message: str, *, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().casefold()
    return " ".join(text.split())


def availability_from_report(
    text: str,
    *,
    player_name: str,
    team_name: str,
    matchup: str,
    game_date: str,
) -> dict[str, Any]:
    normalized = _key(" ".join(str(text).replace("\u00a0", " ").split()))
    matchup_key = _key(matchup)
    team_key = _key(team_name)
    if matchup_key not in normalized or team_key not in normalized:
        raise WNBAInjuryStatusError(
            "WNBA_INJURY_REPORT_EVENT_UNRESOLVED",
            "fresh official injury report did not contain the target matchup/team",
            detail={"matchup": matchup, "team": team_name, "game_date": game_date},
        )

    team_pos = normalized.find(team_key)
    section = normalized[team_pos : team_pos + 1800]
    if "not yet submitted" in section[:250]:
        raise WNBAInjuryStatusError(
            "WNBA_INJURY_REPORT_NOT_SUBMITTED",
            "target team has not submitted its official WNBA injury report",
            detail={"team": team_name, "game_date": game_date},
        )

    parts = _key(player_name).split()
    if not parts:
        raise WNBAInjuryStatusError(
            "WNBA_INJURY_REPORT_PLAYER_STATUS_UNRESOLVED",
            "target player name was empty after normalization",
            detail={"player": player_name, "team": team_name},
        )
    forms = [" ".join(parts)]
    if len(parts) >= 2:
        reversed_form = " ".join(reversed(parts))
        if reversed_form not in forms:
            forms.append(reversed_form)

    matches: list[tuple[int, str]] = []
    for form in forms:
        pos = section.find(form)
        if pos >= 0:
            matches.append((pos, form))
    if not matches:
        return {
            "availability": "NOT_LISTED_ON_FRESH_OFFICIAL_INJURY_REPORT",
            "designation": None,
            "injury_reason": None,
        }

    pos, matched_form = min(matches, key=lambda item: item[0])
    tail = section[pos + len(matched_form) :].lstrip()
    status_match = re.match(r"(available|out|doubtful|questionable|probable)\b", tail)
    if status_match is None:
        raise WNBAInjuryStatusError(
            "WNBA_INJURY_REPORT_PLAYER_STATUS_UNRESOLVED",
            "player appeared on the official injury report but no immediate designation token was parsed",
            detail={"player": player_name, "team": team_name},
        )

    designation = status_match.group(1).upper()
    if designation != "AVAILABLE":
        raise WNBAInjuryStatusError(
            "WNBA_PLAYER_AVAILABILITY_NOT_CLEAR",
            "player has an explicit current official injury/availability designation; unconditional model adjustment is not certified",
            detail={"player": player_name, "team": team_name, "designation": designation},
        )
    return {
        "availability": "AVAILABLE",
        "designation": "AVAILABLE",
        "injury_reason": None,
    }
