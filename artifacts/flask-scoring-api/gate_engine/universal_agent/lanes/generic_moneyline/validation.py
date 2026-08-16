"""
gate_engine/universal_agent/lanes/generic_moneyline/validation.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B7

Input validation for Generic Moneyline Lane adapter rows.

Dedicated-lane exclusion
────────────────────────
Sports already served by dedicated UAC lanes are rejected here — they must
be routed to their own adapters (MLB_MONEYLINE, MLB_PROPS, WNBA_PROPS,
TENNIS_PROPS). This prevents double-routing and ensures sport-specialist
models are correctly applied.

can_execute = False
"""
from __future__ import annotations

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# ── Sports with dedicated UAC lanes (rejected here) ───────────────────────────
# These sport identifiers route to their own adapters — not this generic lane.
DEDICATED_LANE_SPORTS: frozenset[str] = frozenset({
    # MLB — handled by MLB_MONEYLINE or MLB_PROPS lanes
    "mlb", "baseball", "mlb_batter", "mlb_pitcher",
    # WNBA/NBA props — handled by WNBA_PROPS lane
    "wnba", "nba",
    # Tennis — handled by TENNIS_PROPS lane
    "tennis", "atp", "wta", "itf", "atp_tennis", "wta_tennis",
    "grand_slam", "tennis_props",
})

# ── Accepted market identifiers (case-insensitive) ────────────────────────────
# Moneyline / winner / h2h markets only — props/totals go to sport-specific lanes.
MONEYLINE_MARKET_KEYS: frozenset[str] = frozenset({
    "moneyline", "ml", "winner", "h2h", "match_winner", "game_winner",
    "outright", "outright_winner", "1x2", "spread",
    "to_win", "win",
})

# ── Sports explicitly supported in this lane ──────────────────────────────────
# Open-ended: any sport NOT in DEDICATED_LANE_SPORTS is accepted, but we list
# the primary expected sports here for documentation purposes.
_PRIMARY_SPORTS: frozenset[str] = frozenset({
    "nfl", "nhl", "nba_moneyline", "ncaaf", "ncaab",
    "soccer", "mls", "epl", "la_liga", "bundesliga", "serie_a", "ligue_1",
    "cfb", "cbb", "boxing", "mma", "ufc",
    "nascar", "f1", "formula_1",
    "pga", "golf",
    "rugby", "cricket",
})


class AdapterInputError(Exception):
    """
    Raised by validate_generic_moneyline_row when the row is structurally
    incompatible with the Generic Moneyline lane.

    Attributes
    ----------
    code    Machine-readable failure tag.
    message Human-readable description.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code    = code
        self.message = message


def validate_generic_moneyline_row(row: object) -> None:
    """
    Validate that *row* is a generic moneyline evidence row.

    Raises AdapterInputError on:
    - row is not a dict
    - sport is absent
    - sport is in DEDICATED_LANE_SPORTS (has a dedicated UAC lane)
    - market is present but not a moneyline/winner type
    - event_id is absent or empty

    Does NOT raise on missing optional fields (they degrade gracefully).
    """
    if not isinstance(row, dict):
        raise AdapterInputError(
            "NOT_A_DICT",
            f"Generic Moneyline lane requires a dict row; got {type(row).__name__}",
        )

    sport = str(row.get("sport") or "").strip().lower()
    if not sport:
        raise AdapterInputError(
            "MISSING_SPORT",
            "Row is missing required 'sport' field",
        )
    if sport in DEDICATED_LANE_SPORTS:
        raise AdapterInputError(
            "SPORT_HAS_DEDICATED_LANE",
            f"sport={sport!r} has a dedicated UAC lane; route to the appropriate "
            f"adapter (MLB_MONEYLINE, MLB_PROPS, WNBA_PROPS, or TENNIS_PROPS)",
        )

    market_raw = row.get("market")
    if market_raw is not None:
        market = str(market_raw).strip().lower()
        if market and market not in MONEYLINE_MARKET_KEYS:
            raise AdapterInputError(
                "MARKET_NOT_MONEYLINE",
                f"Generic Moneyline lane requires a moneyline/winner market; "
                f"got market={market!r}. Props/totals should route to sport-specific lanes.",
            )

    event_id_raw = row.get("event_id") or row.get("canonical_event_id")
    if not event_id_raw or not str(event_id_raw).strip():
        raise AdapterInputError(
            "MISSING_EVENT_ID",
            "Row must have a non-empty 'event_id' or 'canonical_event_id'",
        )
