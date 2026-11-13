"""Fail-closed NCAAB SportsDataverse two-row game identity validation.

Pure source preflight: cannot authorize fitted probabilities or publication.
"""
from __future__ import annotations
from collections.abc import Mapping, Sequence
from typing import Any


class NCAABSourcePairError(ValueError):
    """A single game ID cannot be resolved to one trustworthy source pair."""


def validated_team_box_pair(
    game_id: str, rows: Sequence[Mapping[str, Any]]
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """Return home/away only for a unique, settled, source-consistent pair.

    Incomplete games are excluded; contradictory source rows raise a typed HOLD
    to prevent silently selecting the first record and corrupting chronology.
    """
    if not rows:
        return None
    if any(str(row.get("game_id") or "").strip() != game_id for row in rows):
        raise NCAABSourcePairError("NCAAB_SOURCE_GAME_ID_CONFLICT")
    if len(rows) == 1:
        return None
    if len(rows) != 2:
        raise NCAABSourcePairError("NCAAB_SOURCE_GAME_PAIR_AMBIGUOUS")
    sides = {str(row.get("team_home_away") or "").strip().lower(): row for row in rows}
    if set(sides) != {"home", "away"}:
        raise NCAABSourcePairError("NCAAB_SOURCE_GAME_PAIR_AMBIGUOUS")
    home, away = sides["home"], sides["away"]
    if any(
        not home.get(key) or not away.get(key) or home.get(key) != away.get(key)
        for key in ("_source_sha256", "_source_url", "season")
    ):
        raise NCAABSourcePairError("NCAAB_SOURCE_PAIR_PROVENANCE_MISMATCH")
    home_id = str(home.get("team_id") or home.get("team_display_name") or "").strip()
    away_id = str(away.get("team_id") or away.get("team_display_name") or "").strip()
    if not home_id or not away_id or home_id == away_id:
        raise NCAABSourcePairError("NCAAB_SOURCE_PARTICIPANT_COLLISION")
    if home.get("team_score") in (None, "") or away.get("team_score") in (None, ""):
        return None
    return home, away
