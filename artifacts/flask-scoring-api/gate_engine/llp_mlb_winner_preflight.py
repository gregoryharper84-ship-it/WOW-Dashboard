"""
llp_mlb_winner_preflight.py
LLP MLB Winner Preflight Gate — PATCH-LEVEL REQUIRED

Mandatory three-gate pre-upgrade check for ALL MLB Kalshi winner /
moneyline candidate rows.  A row may not reach FINAL_APPROVED or
MONEY_QUALIFIED unless all three gates pass.

Gate 1 — Starter / Lineup confirmation
  starter_status must be CONFIRMED or PROBABLE_STRONG.
  lineup_status  must be CONFIRMED or PROJECTED_ACCEPTABLE.
  PROBABLE_ONLY  → WATCH cap (MARKET_VERIFIED_HOLD), not a hard block.
  Missing data   → watch cap (DATA_UNOBTAINABLE for that check).

Gate 2 — Weather / Event status
  event_status must be SCHEDULED or ACTIVE_PREGAME_VALID.
  POSTPONED / CANCELLED / SUSPENDED → SLATE_PURGE (the pick dies; a
    doubleheader restart requires a full fresh preflight on the new event).
  weather_status MATERIAL_RISK / DELAY_RISK / RAINOUT_RISK → WATCH cap.

Gate 3 — No-vig / Model above Kalshi breakeven
  kalshi_breakeven_probability = 1 / kalshi_multiplier.
  sportsbook_no_vig_probability  >= kalshi_breakeven_probability.
  calibrated_probability_lower_bound >= kalshi_breakeven_probability
                                        + safety_buffer.
  Safety buffer: 0.02 when multiplier < 1.60x; else 0.015.
  Any Gate 3 failure → MLB_WINNER_PREFLIGHT_BLOCK (hard reject).
  Missing Gate 3 fields → MLB_WINNER_PREFLIGHT_BLOCK (fail-closed;
    the math cannot be performed without price data).

Enforcement summary
  hard_blockers (Gate 3 / POSTPONED) → MLB_WINNER_PREFLIGHT_BLOCK or
                                         SLATE_PURGE (highest severity)
  watch_blockers (Gate 1 / 2 non-fatal) → MARKET_VERIFIED_HOLD cap
  PASS → terminal_label unchanged; classifier proceeds normally

can_execute = False: this module classifies and enforces ceilings.
It never places an order, mutates a sportsbook, or executes a wager.
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
ENGINE_MODULE  = "llp_mlb_winner_preflight"
ENGINE_VERSION = "v1.0"

# ---------------------------------------------------------------------------
# Gate threshold values
# ---------------------------------------------------------------------------
_STARTER_PASS    = frozenset({"CONFIRMED", "PROBABLE_STRONG"})
_LINEUP_PASS     = frozenset({"CONFIRMED", "PROJECTED_ACCEPTABLE"})
_LINEUP_WATCH    = frozenset({"PROBABLE_ONLY"})
_EVENT_KILL      = frozenset({"POSTPONED", "CANCELLED", "SUSPENDED"})
_WEATHER_FAIL    = frozenset({"MATERIAL_RISK", "DELAY_RISK", "RAINOUT_RISK"})

# Multiplier threshold below which the tighter buffer applies
_SHORT_FAVORITE_MULTIPLIER = 1.60
_BUFFER_SHORT = 0.020   # multiplier < 1.60x
_BUFFER_STD   = 0.015   # multiplier >= 1.60x

# MLB winner-market keywords — mirrors validate_series_state in llp_governance.py
_WINNER_KEYWORDS = ("winner", "moneyline", "ml", "game winner")

# Labels that are already terminally blocked upstream — skip the preflight
# so we do not overwrite a more specific block code with a generic one.
_SKIP_IF_TERMINAL = frozenset({
    PropLabel.SLATE_PURGE.value,
    PropLabel.REJECT_NO_EDGE.value,
    PropLabel.REJECT_BAD_STRUCTURE.value,
    PropLabel.REJECT_DATA_QUALITY.value,
    PropLabel.SOURCE_CONFLICT.value,
    PropLabel.DATA_CONTRACT_FAIL.value,
    PropLabel.REJECT_HOUSE_RULES_VULNERABILITY.value,
    PropLabel.REJECT_SHARP_CONFLICT.value,
    PropLabel.REJECT_POWER_CORRELATED.value,
    PropLabel.SETTLEMENT_SOURCE_CONFLICT.value,
    PropLabel.DUPLICATE_EXPOSURE_BLOCK.value,
    PropLabel.DIRECTIONAL_EXPOSURE_BLOCK.value,
    PropLabel.SESSION_DIRECTIONAL_EXPOSURE_BLOCK.value,
    PropLabel.MLB_WINNER_PREFLIGHT_BLOCK.value,
    PropLabel.HARD_REJECT_COMBO_MULTIPLICATION.value,
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_mlb_winner_row(row: dict[str, Any]) -> bool:
    """Return True iff the row is an MLB winner / moneyline market candidate."""
    sport  = (row.get("sport") or "").strip().upper()
    market = (row.get("market") or row.get("prop_type") or "").strip().lower()
    return sport == "MLB" and any(kw in market for kw in _WINNER_KEYWORDS)


def _breakeven_and_buffer(multiplier: float) -> tuple[float, float]:
    be  = round(1.0 / multiplier, 6)
    buf = _BUFFER_SHORT if multiplier < _SHORT_FAVORITE_MULTIPLIER else _BUFFER_STD
    return be, buf


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(row: dict[str, Any]) -> None:
    """
    Run the three-gate preflight check on `row` **in-place**.

    Non-MLB-winner rows and rows already terminally rejected receive only
    ``preflight_checked=False`` and are otherwise untouched.

    Fields written to the row on activation:
      preflight_checked                 bool
      preflight_status                  "PASS" | "WATCH" | "FAIL" |
                                        "FAIL_POSTPONEMENT"
      upgrade_allowed                   bool
      preflight_blockers                list[str]  — per-gate codes
      kalshi_breakeven_probability      float | None
      breakeven_gap                     float | None
      gates["mlb_winner_preflight"]     dict  — full gate record
    """
    # ------------------------------------------------------------------
    # Scope check — only MLB winner/moneyline rows
    # ------------------------------------------------------------------
    if not _is_mlb_winner_row(row):
        row["preflight_checked"] = False
        return

    existing = row.get("terminal_label")
    if existing in _SKIP_IF_TERMINAL:
        row["preflight_checked"] = False
        return

    hard_blockers: list[str] = []   # → MLB_WINNER_PREFLIGHT_BLOCK / SLATE_PURGE
    watch_blockers: list[str] = []  # → MARKET_VERIFIED_HOLD cap only
    kill_flag = False               # POSTPONED/CANCELLED/SUSPENDED → SLATE_PURGE

    # ------------------------------------------------------------------
    # Gate 1 — Starter / Lineup confirmation
    # ------------------------------------------------------------------
    starter = row.get("starter_status")
    if starter is None:
        watch_blockers.append("NO_DATA_QUALITY:STARTER_STATUS_MISSING")
    elif starter not in _STARTER_PASS:
        watch_blockers.append("NO_STARTER_CONFIRMATION")

    lineup = row.get("lineup_status")
    if lineup is None:
        watch_blockers.append("NO_DATA_QUALITY:LINEUP_STATUS_MISSING")
    elif lineup in _LINEUP_WATCH:
        watch_blockers.append("PROBABLE_ONLY")
    elif lineup not in _LINEUP_PASS:
        watch_blockers.append("NO_LINEUP_CONFIRMATION")

    # ------------------------------------------------------------------
    # Gate 2 — Weather / Event status
    # ------------------------------------------------------------------
    event_status = row.get("event_status")
    if event_status in _EVENT_KILL:
        kill_flag = True
        hard_blockers.append("EVENT_RESET_REQUIRED_POSTPONEMENT")
    elif event_status is None:
        watch_blockers.append("NO_EVENT_VERIFICATION")
    elif event_status not in ("SCHEDULED", "ACTIVE_PREGAME_VALID"):
        watch_blockers.append("EVENT_STATUS_FAILURE")

    weather = row.get("weather_status")
    if weather in _WEATHER_FAIL:
        watch_blockers.append("WEATHER_RISK_CUT")
    elif weather is None:
        watch_blockers.append("NO_DATA_QUALITY:WEATHER_STATUS_MISSING")

    # ------------------------------------------------------------------
    # Gate 3 — No-vig / Model above Kalshi breakeven
    # ------------------------------------------------------------------
    multiplier = row.get("kalshi_multiplier")
    model_lb   = row.get("calibrated_probability_lower_bound")
    no_vig     = row.get("sportsbook_no_vig_probability")
    breakeven: float | None = None

    if multiplier is None:
        hard_blockers.append("KALSHI_DATA_UNOBTAINABLE:MISSING_MULTIPLIER")
    else:
        try:
            m = float(multiplier)
            if m <= 0:
                raise ValueError("non-positive multiplier")
            breakeven, buf = _breakeven_and_buffer(m)
            row["kalshi_breakeven_probability"] = breakeven

            # No-vig check
            if no_vig is None:
                hard_blockers.append("KALSHI_DATA_UNOBTAINABLE:MISSING_NO_VIG")
                fail_market = False
            else:
                fail_market = float(no_vig) < breakeven
                if fail_market:
                    hard_blockers.append("NO_VIG_BELOW_BREAKEVEN")
                row["breakeven_gap"] = round(float(no_vig) - breakeven, 6)

            # Model lower-bound check
            if model_lb is None:
                hard_blockers.append("KALSHI_DATA_UNOBTAINABLE:MISSING_MODEL_LB")
                fail_model = False
            else:
                fail_model = float(model_lb) < breakeven + buf
                if fail_model:
                    hard_blockers.append("MODEL_LOWER_BOUND_BELOW_BREAKEVEN")

            # Summary label for Gate 3 (in addition to specific blockers above)
            if fail_model and fail_market:
                hard_blockers.append("KALSHI_REJECT_NO_EDGE")
            elif fail_model:
                hard_blockers.append("LLP_PRICE_FIREWALL_FAIL")
            # FAIL_MARKET alone: NO_VIG_BELOW_BREAKEVEN is already the summary

        except (TypeError, ValueError, ZeroDivisionError) as exc:
            hard_blockers.append(
                f"KALSHI_DATA_UNOBTAINABLE:INVALID_MULTIPLIER:{str(exc)[:60]}"
            )

    # ------------------------------------------------------------------
    # Enforce result — priority: KILL > HARD > WATCH > PASS
    # ------------------------------------------------------------------
    all_blockers = hard_blockers + watch_blockers

    if kill_flag:
        # Game cancelled / postponed — the pick is dead.
        # A doubleheader restart is a *new* event and requires a fresh run.
        preflight_status = "FAIL_POSTPONEMENT"
        upgrade_allowed  = False
        row["terminal_label"]       = PropLabel.SLATE_PURGE.value
        row["preflight_kill_reason"] = "EVENT_RESET_REQUIRED_POSTPONEMENT"

    elif hard_blockers:
        # Gate 3 or missing price data — math cannot be completed or fails.
        preflight_status = "FAIL"
        upgrade_allowed  = False
        row["terminal_label"] = PropLabel.MLB_WINNER_PREFLIGHT_BLOCK.value

    elif watch_blockers:
        # Gate 1/2 non-fatal issues — hold at WATCH, do not fully reject.
        # The confirmation may arrive later (starters post closer to game time).
        preflight_status = "WATCH"
        upgrade_allowed  = False
        row["terminal_label"] = PropLabel.MARKET_VERIFIED_HOLD.value

    else:
        preflight_status = "PASS"
        upgrade_allowed  = True
        # terminal_label unchanged — classifier proceeds normally

    # Stamp output fields required by the spec
    row["preflight_checked"]  = True
    row["preflight_status"]   = preflight_status
    row["upgrade_allowed"]    = upgrade_allowed
    row["preflight_blockers"] = all_blockers

    # Required output fields (populated from row or set to None if absent)
    for _field in (
        "starter_status", "starter_source",
        "lineup_status",  "lineup_source",
        "event_status",   "weather_status", "weather_source",
        "kalshi_multiplier",
        "sportsbook_no_vig_probability",
        "model_probability",
        "calibrated_probability_lower_bound",
    ):
        row.setdefault(_field, None)
    row.setdefault("kalshi_breakeven_probability", None)
    row.setdefault("breakeven_gap", None)

    # Full gate record — surfaced in gates dict for observability
    row.setdefault("gates", {})["mlb_winner_preflight"] = {
        "module":            ENGINE_MODULE,
        "version":           ENGINE_VERSION,
        "preflight_status":  preflight_status,
        "upgrade_allowed":   upgrade_allowed,
        "hard_blockers":     hard_blockers,
        "watch_blockers":    watch_blockers,
        "gate1_starter":     row.get("starter_status"),
        "gate1_lineup":      row.get("lineup_status"),
        "gate2_event":       row.get("event_status"),
        "gate2_weather":     row.get("weather_status"),
        "gate3_multiplier":  multiplier,
        "gate3_breakeven":   breakeven,
        "gate3_buffer":      (
            _BUFFER_SHORT if (multiplier or 0) < _SHORT_FAVORITE_MULTIPLIER
            else _BUFFER_STD
        ),
        "gate3_no_vig":      no_vig,
        "gate3_model_lb":    model_lb,
        "can_execute":       can_execute,
    }
