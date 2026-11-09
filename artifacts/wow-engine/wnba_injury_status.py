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
    opponent_name: str,
    matchup: str,
    game_date: str,
) -> dict[str, Any]:
    """Parse one team's bounded section of the official WNBA injury report.

    The section runs from the target team header to the opposing team header on
    the same matchup page. A player absence is only read as "no reported
    designation" once that bounded section is shown to contain parsed status
    rows; a missing opponent header or a section with no status rows raises
    instead of clearing. An explicit ``OUT`` is returned as a ruling so the
    terminal reducer can preserve ``PLAYER_RULED_OUT``.
    """
    normalized = _key(" ".join(str(text).replace("\u00a0", " ").split()))
    matchup_key = _key(matchup)
    team_key = _key(team_name)
    opponent_key = _key(opponent_name)
    if matchup_key not in normalized or team_key not in normalized:
        raise WNBAInjuryStatusError(
            "WNBA_INJURY_REPORT_EVENT_UNRESOLVED",
            "fresh official injury report did not contain the target matchup/team",
            detail={"matchup": matchup, "team": team_name, "game_date": game_date},
        )

    team_pos = normalized.find(team_key)
    if "not yet submitted" in normalized[team_pos : team_pos + 250]:
        raise WNBAInjuryStatusError(
            "WNBA_INJURY_REPORT_NOT_SUBMITTED",
            "target team has not submitted its official WNBA injury report",
            detail={"team": team_name, "game_date": game_date},
        )

    opponent_pos = normalized.find(opponent_key) if opponent_key else -1
    if not opponent_key or opponent_pos < 0:
        raise WNBAInjuryStatusError(
            "WNBA_INJURY_REPORT_SECTION_INCOMPLETE",
            "opponent section header was not parsed, so the target section cannot be bounded",
            detail={"team": team_name, "opponent": opponent_name, "game_date": game_date},
        )
    if opponent_pos > team_pos:
        section = normalized[team_pos:opponent_pos]
    else:
        # The opponent is listed first; otherwise a slice to EOF can consume
        # subsequent games and falsely certify this player's absence.
        report_lines = str(text).replace("\u00a0", " ").splitlines()
        target_headers = [
            idx for idx, line in enumerate(report_lines)
            if _key(line) == team_key
        ]
        if len(target_headers) != 1:
            raise WNBAInjuryStatusError(
                "WNBA_INJURY_REPORT_SECTION_INCOMPLETE",
                "second-listed team header is not uniquely identifiable",
                detail={"team": team_name, "game_date": game_date},
            )
        following = report_lines[target_headers[0]:]
        next_matchup = next(
            (
                idx for idx, line in enumerate(following[1:], start=1)
                if re.match(r"^\s*[A-Z0-9]{2,4}\s*@\s*[A-Z0-9]{2,4}(?:\s|$)", line)
            ),
            len(following),
        )
        section = _key(" ".join(following[:next_matchup]))
    if not section.strip():
        raise WNBAInjuryStatusError(
            "WNBA_INJURY_REPORT_SECTION_INCOMPLETE",
            "target team section was empty",
            detail={"team": team_name, "game_date": game_date},
        )
    if "no injuries" not in section and not re.search(
        r"\b(available|out|doubtful|questionable|probable)\b", section
    ):
        raise WNBAInjuryStatusError(
            "WNBA_INJURY_REPORT_SECTION_INCOMPLETE",
            "target team section contained no parsed participation designations",
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
    if designation == "OUT":
        # A rule-out is a settled participation fact, not an uncertified model
        # adjustment. Return it so hydration terminates the row with
        # PLAYER_RULED_OUT instead of a model/hydration failure label.
        return {"availability": "UNAVAILABLE", "designation": "OUT", "injury_reason": None}
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
