"""
gate_engine/universal_agent/lanes/tennis_props/validation.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B6

Input validation for Tennis Props Lane adapter rows.

can_execute = False
"""
from __future__ import annotations

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# ── Supported stat_key values ─────────────────────────────────────────────────
#
# total_games          — full-match total games; requires Markov chain routing
# set_games            — per-set total games; requires Markov chain routing
# match_total_sets     — over/under on total sets played (best of 3 or 5)
# first_set_winner     — which player wins the first set (structural gate)
# first_set_games      — total games in the first set (Markov, first set only)
# match_winner         — outright winner (handled via LLP moneyline route
#                        when available; accepted here for UAC evidence packaging)
#
SUPPORTED_STAT_KEYS: frozenset[str] = frozenset({
    "total_games",
    "set_games",
    "match_total_sets",
    "first_set_winner",
    "first_set_games",
    "match_winner",
    # common aliases
    "games_total",
    "total_sets",
    "winner",
})

# Accepted sport identifiers (case-insensitive).
_TENNIS_SPORT_KEYS: frozenset[str] = frozenset({
    "tennis", "atp", "wta", "itf", "atp_tennis", "wta_tennis",
    "grand_slam", "tennis_props",
})

# Accepted market identifiers (case-insensitive).
_TENNIS_MARKET_KEYS: frozenset[str] = frozenset({
    "props", "player_props", "tennis_props", "prop", "totals",
    "match_props", "game_totals", "moneyline", "winner", "h2h",
})


class AdapterInputError(Exception):
    """
    Raised by validate_tennis_props_row when the row is structurally
    incompatible with the Tennis Props lane.

    Attributes
    ----------
    code    Machine-readable failure tag (e.g. "SPORT_MISMATCH").
    message Human-readable description.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code    = code
        self.message = message


def validate_tennis_props_row(row: object) -> None:
    """
    Validate that *row* is a tennis props evidence row compatible with
    the Tennis Props lane.

    Raises AdapterInputError on:
    - row is not a dict
    - sport is absent or not tennis
    - market is present but clearly non-tennis
    - stat_key / prop_type is present but not in SUPPORTED_STAT_KEYS
    - event_id is absent or empty

    Does NOT raise on missing optional fields (they degrade gracefully).
    """
    if not isinstance(row, dict):
        raise AdapterInputError(
            "NOT_A_DICT",
            f"Tennis Props lane requires a dict row; got {type(row).__name__}",
        )

    sport = str(row.get("sport") or "").strip().lower()
    if not sport:
        raise AdapterInputError(
            "MISSING_SPORT",
            "Row is missing required 'sport' field",
        )
    if sport not in _TENNIS_SPORT_KEYS:
        raise AdapterInputError(
            "SPORT_MISMATCH",
            f"Tennis Props lane requires a tennis sport; got sport={sport!r}",
        )

    market_raw = row.get("market")
    if market_raw is not None:
        market = str(market_raw).strip().lower()
        if market and market not in _TENNIS_MARKET_KEYS:
            raise AdapterInputError(
                "MARKET_MISMATCH",
                f"Tennis Props lane got unexpected market={market!r}",
            )

    stat_key_raw = row.get("stat_key") or row.get("prop_type")
    if stat_key_raw is not None:
        sk = str(stat_key_raw).strip().lower()
        if sk and sk not in SUPPORTED_STAT_KEYS:
            raise AdapterInputError(
                "UNSUPPORTED_STAT_KEY",
                f"Tennis Props lane does not support stat_key={sk!r}; "
                f"supported: {sorted(SUPPORTED_STAT_KEYS)}",
            )

    event_id_raw = row.get("event_id") or row.get("canonical_event_id")
    if not event_id_raw or not str(event_id_raw).strip():
        raise AdapterInputError(
            "MISSING_EVENT_ID",
            "Row must have a non-empty 'event_id' or 'canonical_event_id'",
        )
