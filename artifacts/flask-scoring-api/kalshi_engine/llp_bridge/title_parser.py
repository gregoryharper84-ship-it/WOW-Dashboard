"""
title_parser.py — helpers for deriving structured facts from Kalshi's own
market title text on the MLB/WNBA winner-market series.

WOW-PATCH-2026-07-05-KALSHI-WNBA-MLB-ML-EDGE-RULE (live-board step).

Root-cause context: Kalshi's `no_sub_title` field on this winner-market
series duplicates `yes_sub_title` on every ticker observed live on
2026-07-05 (it does NOT name the opposing team, despite the field name
suggesting otherwise). The only reliable source for the opposing team is
the market's own `title` text, which follows a fixed
"TeamA vs TeamB Winner?" wording for this series.
"""
from __future__ import annotations

import re
from typing import Optional

_OPPONENT_TITLE_RE = re.compile(r"^(.+?)\s+vs\s+(.+?)\s+Winner\?$", re.IGNORECASE)


def parse_opponent_team(title: Optional[str], yes_team: Optional[str]) -> Optional[str]:
    """
    Derive the opposing team from a Kalshi winner-market title, e.g.
    "Toronto vs San Francisco Winner?" -> given yes_team="Toronto",
    returns "San Francisco".

    Returns None (never a guess) when:
      - title or yes_team is missing,
      - title doesn't match the fixed "TeamA vs TeamB Winner?" pattern, or
      - yes_team doesn't exactly equal one of the two parsed teams.

    Callers must treat a None result as "opponent team could not be
    determined" and fall through to a NOT_CALLED consensus lookup rather
    than querying with a wrong or self-paired team.
    """
    if not title or not yes_team:
        return None
    m = _OPPONENT_TITLE_RE.match(title.strip())
    if not m:
        return None
    team_a, team_b = m.group(1).strip(), m.group(2).strip()
    yes_team_stripped = yes_team.strip()
    if yes_team_stripped == team_a:
        return team_b
    if yes_team_stripped == team_b:
        return team_a
    return None
