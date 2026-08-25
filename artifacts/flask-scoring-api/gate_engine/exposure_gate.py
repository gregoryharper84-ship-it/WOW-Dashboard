"""
exposure_gate.py
Block duplicate exposure: same-player, same-game, same archetype across a slip.
Maintains an exposure ledger across the run session.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .labels import PropLabel


class ExposureLedger:
    """
    Tracks approved candidates for duplicate-exposure enforcement.
    One ledger per analysis session (instantiated in pipeline).
    """

    def __init__(self,
                 max_player: int = 1,
                 max_game: int   = 2,
                 max_archetype: int = 3):
        self.max_player    = max_player
        self.max_game      = max_game
        self.max_archetype = max_archetype
        self._players:    dict[str, int] = defaultdict(int)
        self._games:      dict[str, int] = defaultdict(int)
        self._archetypes: dict[str, int] = defaultdict(int)
        self._ledger_rows: list[dict] = []

    def check_and_register(self, row: dict[str, Any]) -> dict[str, Any]:
        """
        Check exposure limits for this row.
        If it passes, register it.
        If it fails, mark DUPLICATE_EXPOSURE_BLOCK.

        Gate result at row["gates"]["exposure_gate"].

        WOW-PATCH-2026-08-16: DATA_CONTRACT_FAIL rows must not consume exposure
        slots — an incomplete-data row never produced a valid card, so a repair
        retry should not hit duplicate/concentration blockers.  The gate still
        populates gates["exposure_gate"] (with registered=False + reason) so the
        gates dict is complete and test assertions about the gate running are met.
        """
        # DATA_CONTRACT_FAIL: record that the gate ran but do not register.
        if row.get("terminal_label") == PropLabel.DATA_CONTRACT_FAIL.value:
            row.setdefault("gates", {})["exposure_gate"] = {
                "passed":     False,
                "blocks":     [],
                "registered": False,
                "skipped_reason": "DATA_CONTRACT_FAIL:not_eligible_for_exposure",
            }
            return row

        player    = (row.get("player") or "UNKNOWN").lower()
        game      = (row.get("game") or "UNKNOWN").lower()
        archetype = _archetype(row.get("prop_type") or "")

        blocks: list[str] = []

        if self._players[player] >= self.max_player:
            blocks.append(f"PLAYER_EXPOSURE:{player}:{self._players[player]+1}x")
        if self._games[game] >= self.max_game:
            blocks.append(f"GAME_EXPOSURE:{game}:{self._games[game]+1}x")
        if self._archetypes[archetype] >= self.max_archetype:
            blocks.append(f"ARCHETYPE_EXPOSURE:{archetype}:{self._archetypes[archetype]+1}x")

        if blocks:
            row["blockers"].extend(blocks)
            row["gates"]["exposure_gate"] = {
                "passed":  False,
                "blocks":  blocks,
                "registered": False,
            }
        else:
            self._players[player]       += 1
            self._games[game]           += 1
            self._archetypes[archetype] += 1
            self._ledger_rows.append({
                "row_id":    row["row_id"],
                "player":    player,
                "game":      game,
                "archetype": archetype,
            })
            row["gates"]["exposure_gate"] = {
                "passed":     True,
                "blocks":     [],
                "registered": True,
            }

        return row

    def snapshot(self) -> dict[str, Any]:
        return {
            "player_counts":    dict(self._players),
            "game_counts":      dict(self._games),
            "archetype_counts": dict(self._archetypes),
            "registered_rows":  self._ledger_rows,
        }


def _archetype(prop_type: str) -> str:
    pt = prop_type.lower()
    if "point" in pt:   return "scoring"
    if "rebound" in pt: return "rebound"
    if "assist" in pt:  return "assist"
    if "hit" in pt or "rbi" in pt or "home" in pt: return "mlb_batting"
    if "strikeout" in pt or "pitch" in pt:         return "mlb_pitching"
    if "shot" in pt or "goal" in pt:               return "soccer"
    return "other"
