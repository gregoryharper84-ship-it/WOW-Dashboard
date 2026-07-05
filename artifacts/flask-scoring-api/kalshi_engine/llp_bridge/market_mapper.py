"""
market_mapper.py  —  KalshiMarketMapper
WOW-PATCH-2026-07-05-LLP-KALSHI-SPORTS-BRIDGE v2, Step 2

Maps an LLP game (teams, sport, date) to a Kalshi winner-market ticker.

HARD RULE (per Greg's approved amendment #3 + #5):
  Only an EXACT match may ever be treated as a real mapping. Any fuzzy /
  partial / heuristic match must be capped at LLP_SCOUT and can never
  contribute to LLP_PLAYABLE or LLP_APPROVED. Ambiguous or multi-candidate
  matches are always fuzzy, never exact, regardless of similarity score.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Common team-name aliasing so "LA Lakers" vs "Los Angeles Lakers" can still
# be an EXACT match after normalization (not a fuzzy heuristic — this is a
# fixed alias table, not a similarity score).
_ALIASES: dict[str, str] = {
    "LA": "LOS ANGELES",
    "NY": "NEW YORK",
    "SF": "SAN FRANCISCO",
    "GS": "GOLDEN STATE",
}


def _normalize_team(name: str) -> str:
    up = re.sub(r"[^A-Z0-9 ]", "", (name or "").upper()).strip()
    up = re.sub(r"\s+", " ", up)
    for short, full in _ALIASES.items():
        if up.startswith(short + " "):
            up = full + up[len(short):]
    return up


class KalshiMarketMapper:
    """LLP game -> Kalshi ticker mapper. Exact match only for approval-track use."""

    def map_game_to_ticker(
        self,
        llp_home_team:  str,
        llp_away_team:  str,
        llp_sport:      str,
        candidate_markets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Attempt to map an LLP game to one of `candidate_markets` (as returned
        by KalshiInventoryAdapter / kalshi_client.search_markets).

        Returns:
          {
            match_type:        "EXACT" | "FUZZY" | "NONE"
            ticker:             str | None
            event_ticker:       str | None
            market_title:       str | None
            label_ceiling:      str | None   "LLP_SCOUT" when match_type != EXACT
            reasoning:          str
            can_approve_bets:   False
            dry_run_only:       True
            can_execute:        False
          }
        """
        home_norm = _normalize_team(llp_home_team)
        away_norm = _normalize_team(llp_away_team)

        exact_matches: list[dict[str, Any]] = []
        fuzzy_matches: list[dict[str, Any]] = []

        for market in candidate_markets:
            title = str(market.get("title") or market.get("subtitle") or "")
            title_norm = _normalize_team(title)

            if home_norm and away_norm and home_norm in title_norm and away_norm in title_norm:
                exact_matches.append(market)
            elif (home_norm and home_norm in title_norm) or (away_norm and away_norm in title_norm):
                fuzzy_matches.append(market)

        if len(exact_matches) == 1:
            m = exact_matches[0]
            return self._result(
                match_type   = "EXACT",
                market       = m,
                label_ceiling= None,
                reasoning    = (
                    f"Exact team-name match on '{m.get('title')}' — "
                    f"both {llp_home_team!r} and {llp_away_team!r} normalized-matched."
                ),
            )

        if len(exact_matches) > 1:
            # Multiple exact-looking matches means the mapping is ambiguous —
            # treat as fuzzy, never approval-eligible.
            m = exact_matches[0]
            return self._result(
                match_type   = "FUZZY",
                market       = m,
                label_ceiling= "LLP_SCOUT",
                reasoning    = (
                    f"{len(exact_matches)} candidate markets matched both team "
                    f"names — ambiguous, capped at LLP_SCOUT until disambiguated."
                ),
            )

        if fuzzy_matches:
            m = fuzzy_matches[0]
            return self._result(
                match_type   = "FUZZY",
                market       = m,
                label_ceiling= "LLP_SCOUT",
                reasoning    = (
                    f"Only one team name matched '{m.get('title')}' — "
                    f"partial/fuzzy match, capped at LLP_SCOUT, never approval."
                ),
            )

        return {
            "match_type":       "NONE",
            "ticker":            None,
            "event_ticker":      None,
            "market_title":      None,
            "label_ceiling":     "LLP_SCOUT",
            "reasoning":         (
                f"No Kalshi candidate market matched {llp_away_team!r} @ "
                f"{llp_home_team!r} ({llp_sport})."
            ),
            "can_approve_bets":  False,
            "dry_run_only":      True,
            "can_execute":       False,
        }

    @staticmethod
    def _result(
        match_type:    str,
        market:        dict[str, Any],
        label_ceiling: Optional[str],
        reasoning:     str,
    ) -> dict[str, Any]:
        return {
            "match_type":       match_type,
            "ticker":            market.get("ticker"),
            "event_ticker":      market.get("event_ticker"),
            "market_title":      market.get("title") or market.get("subtitle"),
            "yes_sub_title":     market.get("yes_sub_title"),
            "label_ceiling":     label_ceiling,
            "reasoning":         reasoning,
            "can_approve_bets":  False,
            "dry_run_only":      True,
            "can_execute":       False,
        }
