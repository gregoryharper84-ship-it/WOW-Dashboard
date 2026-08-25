"""
gate_engine/universal_agent/lanes/mlb_props/validation.py
WOW-PATCH-2026-08-16-UNIVERSAL-AGENT-CORE-V1-B5

Input validation for MLB Props Lane adapter rows.

can_execute = False
"""
from __future__ import annotations

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# ── Supported stat_key values ─────────────────────────────────────────────────
#
# Pitcher props
#   pitcher_strikeouts — binomial ACTIVE; unconditional failure-path prob required
#   pitcher_outs       — innings notation conversion required
#   pitcher_1ip_pitches — event-tree ONLY; blocked from generic models
#   pitcher_hits_allowed, pitcher_earned_runs, pitcher_walks — PROVISIONAL
#
# Batter props
#   batter_hits — binomial ACTIVE
#   batter_total_bases, batter_runs, batter_rbis,
#   batter_home_runs, batter_strikeouts — PROVISIONAL
#
SUPPORTED_STAT_KEYS: frozenset[str] = frozenset({
    # pitcher
    "pitcher_strikeouts",
    "pitcher_outs",
    "pitcher_1ip_pitches",
    "pitcher_hits_allowed",
    "pitcher_earned_runs",
    "pitcher_walks",
    # batter
    "batter_hits",
    "batter_total_bases",
    "batter_runs",
    "batter_rbis",
    "batter_home_runs",
    "batter_strikeouts",
})

# Accepted sport identifiers (case-insensitive match after strip).
_MLB_SPORT_KEYS: frozenset[str] = frozenset({"mlb", "baseball", "mlb_batter", "mlb_pitcher"})

# Accepted market identifiers (case-insensitive).
_PROPS_MARKET_KEYS: frozenset[str] = frozenset({
    "props", "player_props", "batter_props", "pitcher_props",
    "player_prop", "prop",
})


class AdapterInputError(Exception):
    """
    Raised by validate_mlb_props_row when the row is structurally incompatible
    with the MLB Props lane.

    Attributes
    ----------
    code    Machine-readable failure tag (e.g. "SPORT_MISMATCH").
    message Human-readable description.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code    = code
        self.message = message


def validate_mlb_props_row(row: object) -> None:
    """
    Validate that *row* is an MLB props evidence row compatible with this lane.

    Raises AdapterInputError on:
    - row is not a dict
    - sport is absent or not MLB
    - market is present but not a props market
    - stat_key / prop_type is present but not in SUPPORTED_STAT_KEYS
    - event_id is absent or empty

    Does NOT raise on missing optional fields (they degrade gracefully).
    """
    if not isinstance(row, dict):
        raise AdapterInputError(
            "NOT_A_DICT",
            f"MLB Props lane requires a dict row; got {type(row).__name__}",
        )

    sport = str(row.get("sport") or "").strip().lower()
    if not sport:
        raise AdapterInputError(
            "MISSING_SPORT",
            "Row is missing required 'sport' field",
        )
    if sport not in _MLB_SPORT_KEYS:
        raise AdapterInputError(
            "SPORT_MISMATCH",
            f"MLB Props lane requires an MLB sport; got sport={sport!r}",
        )

    market_raw = row.get("market")
    if market_raw is not None:
        market = str(market_raw).strip().lower()
        if market and market not in _PROPS_MARKET_KEYS:
            raise AdapterInputError(
                "MARKET_MISMATCH",
                f"MLB Props lane requires a props market; got market={market!r}",
            )

    stat_key_raw = row.get("stat_key") or row.get("prop_type")
    if stat_key_raw is not None:
        sk = str(stat_key_raw).strip().lower()
        if sk and sk not in SUPPORTED_STAT_KEYS:
            raise AdapterInputError(
                "UNSUPPORTED_STAT_KEY",
                f"MLB Props lane does not support stat_key={sk!r}; "
                f"supported: {sorted(SUPPORTED_STAT_KEYS)}",
            )

    event_id_raw = row.get("event_id") or row.get("canonical_event_id")
    if not event_id_raw or not str(event_id_raw).strip():
        raise AdapterInputError(
            "MISSING_EVENT_ID",
            "Row must have a non-empty 'event_id' or 'canonical_event_id'",
        )
