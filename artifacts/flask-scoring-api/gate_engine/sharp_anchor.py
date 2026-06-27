"""
sharp_anchor.py  —  Patch: Sharp Market Anchor (Directional)
WOW v16 / Patch 2026-06-27

PrizePicks/DFS lines are the TARGET market — never the confirmation market.
Sharp sportsbook lines are the REFERENCE anchor.

Key rule: Reject only when the SELECTED SIDE is opposed to sharp market
direction, or when sharp movement has already erased the edge.

Stale PP line that is FAVORABLE for the selected side is NOT a reject —
it may be exactly the stale value we are looking for.

New labels:
  SHARP_ANCHOR_CONFIRMED        — sharp probability confirms our side
  SHARP_ANCHOR_CONFLICT         — sharp probability opposes our side (soft warning)
  REJECT_SHARP_CONFLICT         — terminal: sharp probability firmly against us
  REJECT_FALLING_KNIFE          — terminal: line moved against our side since entry
  MARKET_VERIFIED_HOLD_STALE    — stale PP line is favorable; keep as hold
  FLIP_CANDIDATE                — edge exists on the OTHER side; potential flip
  NO_SHARP_DATA                 — no sharp data available; gate skipped

Examples:
  Rhyne Howard rebounds MORE 3.5:
    Sharp sits 3.5, juiced OVER (62%) → SHARP_ANCHOR_CONFIRMED
    Sharp sits 3.5, juiced UNDER (38% over) → REJECT_SHARP_CONFLICT
    Sharp moved down to 3.0, we still have PP at 3.5 (worse for MORE) → REJECT_FALLING_KNIFE
    Sharp at 4.5, PP still at 3.5 (easier for MORE) → MARKET_VERIFIED_HOLD_STALE
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Sharp must give ≥ this probability to our side to confirm anchor
SHARP_CONFIRM_THRESHOLD = 0.52

# Sharp giving ≤ this to our side = firm conflict
SHARP_CONFLICT_THRESHOLD = 0.48

# Line delta (PP vs sharp) considered significant (half-unit)
SIGNIFICANT_LINE_DELTA = 0.5

# ---------------------------------------------------------------------------
# Status codes (sub-labels stored in gate result, not terminal PropLabel)
# ---------------------------------------------------------------------------

CONFIRMED    = "SHARP_ANCHOR_CONFIRMED"
CONFLICT     = "SHARP_ANCHOR_CONFLICT"
NEUTRAL      = "SHARP_NEUTRAL"
NO_DATA      = "NO_SHARP_DATA"
FAVORABLE_STALE = "MARKET_VERIFIED_HOLD_STALE"
UNFAVORABLE_STALE = "STALE_LINE_AGAINST_SIDE"


def run(
    row: dict[str, Any],
    sharp_no_vig_prob: float | None = None,
    sharp_fair_line:   float | None = None,
) -> dict[str, Any]:
    """
    Run the directional sharp anchor check for a single row.

    Parameters
    ----------
    row               — gate row with at least row["line"] and row["direction"]
    sharp_no_vig_prob — no-vig probability that the outcome lands OVER/MORE
                        (from sharp sportsbook, 0–1 range)
    sharp_fair_line   — sharp fair-value line (same unit as pp_line)

    Writes result to row["gates"]["sharp_anchor"].
    Sets row["terminal_label"] to REJECT_SHARP_CONFLICT or REJECT_FALLING_KNIFE
    when a terminal reject fires.
    """
    # Skip if terminal label already set
    if row.get("terminal_label") is not None:
        return row

    pp_line   = row.get("line")
    side_raw  = (row.get("direction") or row.get("side") or "MORE").upper().strip()
    side_is_over = side_raw in ("MORE", "OVER")

    result = _check(
        pp_line=pp_line,
        sharp_fair_line=sharp_fair_line,
        sharp_no_vig_prob=sharp_no_vig_prob,
        side_is_over=side_is_over,
        side_label=side_raw,
    )

    row.setdefault("gates", {})["sharp_anchor"] = result

    if result["reject"]:
        terminal = result["terminal_label"]
        if terminal and not row.get("terminal_label"):
            row["terminal_label"] = terminal
            row.setdefault("blockers", []).append(
                f"SHARP_ANCHOR:{terminal}:{result['anchor_status']}"
            )

    return row


def check_standalone(
    pp_line:           float | None,
    sharp_fair_line:   float | None,
    sharp_no_vig_prob: float | None,
    side:              str,
) -> dict[str, Any]:
    """
    Standalone check (no row mutation) — used by the Final Lock endpoint and tests.

    ``side`` should be "MORE"/"OVER" or "LESS"/"UNDER".
    """
    side_upper   = (side or "MORE").upper().strip()
    side_is_over = side_upper in ("MORE", "OVER")
    return _check(pp_line, sharp_fair_line, sharp_no_vig_prob, side_is_over, side_upper)


# ---------------------------------------------------------------------------
# Internal logic
# ---------------------------------------------------------------------------

def _check(
    pp_line:           float | None,
    sharp_fair_line:   float | None,
    sharp_no_vig_prob: float | None,
    side_is_over:      bool,
    side_label:        str,
) -> dict[str, Any]:
    anchor_status: str  = NO_DATA
    line_status:   str | None = None
    reject:        bool = False
    terminal_label: str | None = None
    our_side_prob: float | None = None
    details: list[str] = []

    # ------------------------------------------------------------------
    # Check 1: Directional probability (sharp no-vig prob for our side)
    # ------------------------------------------------------------------
    if sharp_no_vig_prob is not None:
        our_side_prob = sharp_no_vig_prob if side_is_over else (1.0 - sharp_no_vig_prob)

        if our_side_prob >= SHARP_CONFIRM_THRESHOLD:
            anchor_status = CONFIRMED
            details.append(
                f"Sharp p({side_label})={our_side_prob:.3f} ≥ {SHARP_CONFIRM_THRESHOLD} "
                f"— anchor confirmed."
            )
        elif our_side_prob <= SHARP_CONFLICT_THRESHOLD:
            anchor_status = CONFLICT
            reject = True
            terminal_label = PropLabel.REJECT_SHARP_CONFLICT.value
            details.append(
                f"Sharp p({side_label})={our_side_prob:.3f} ≤ {SHARP_CONFLICT_THRESHOLD} "
                f"— sharp firmly opposes selected side → REJECT_SHARP_CONFLICT."
            )
        else:
            anchor_status = NEUTRAL
            details.append(
                f"Sharp p({side_label})={our_side_prob:.3f} — no strong lean (neutral zone)."
            )

    # ------------------------------------------------------------------
    # Check 2: Line movement / stale value check
    # ------------------------------------------------------------------
    if pp_line is not None and sharp_fair_line is not None:
        delta = round(pp_line - sharp_fair_line, 3)
        # For MORE/OVER: lower PP line = easier = better stale value
        #                higher PP line = harder = worse number → falling knife
        # For LESS/UNDER: higher PP line = easier = better stale value
        #                 lower PP line = harder → falling knife
        if side_is_over:
            if delta > SIGNIFICANT_LINE_DELTA:
                # PP line higher → worse for MORE
                line_status = UNFAVORABLE_STALE
                if not reject:   # probability check takes priority if already fired
                    reject = True
                    anchor_status = PropLabel.REJECT_FALLING_KNIFE.value
                    terminal_label = PropLabel.REJECT_FALLING_KNIFE.value
                details.append(
                    f"PP line {pp_line} > sharp {sharp_fair_line} (+{delta}) "
                    f"for {side_label} — worse number → REJECT_FALLING_KNIFE."
                )
            elif delta < -SIGNIFICANT_LINE_DELTA:
                # PP line lower → easier for MORE = favorable stale
                line_status = FAVORABLE_STALE
                if anchor_status == NO_DATA:
                    anchor_status = FAVORABLE_STALE
                details.append(
                    f"PP line {pp_line} < sharp {sharp_fair_line} ({delta}) "
                    f"for {side_label} — favorable stale value."
                )
        else:
            if delta < -SIGNIFICANT_LINE_DELTA:
                # PP line lower → worse for LESS
                line_status = UNFAVORABLE_STALE
                if not reject:
                    reject = True
                    anchor_status = PropLabel.REJECT_FALLING_KNIFE.value
                    terminal_label = PropLabel.REJECT_FALLING_KNIFE.value
                details.append(
                    f"PP line {pp_line} < sharp {sharp_fair_line} ({delta}) "
                    f"for {side_label} — worse number → REJECT_FALLING_KNIFE."
                )
            elif delta > SIGNIFICANT_LINE_DELTA:
                # PP line higher → easier for LESS = favorable stale
                line_status = FAVORABLE_STALE
                if anchor_status == NO_DATA:
                    anchor_status = FAVORABLE_STALE
                details.append(
                    f"PP line {pp_line} > sharp {sharp_fair_line} (+{delta}) "
                    f"for {side_label} — favorable stale value."
                )

    detail_str = " | ".join(details) if details else "No sharp data — gate skipped."

    return {
        "passed":           not reject,
        "reject":           reject,
        "anchor_status":    anchor_status,
        "line_status":      line_status,
        "terminal_label":   terminal_label,
        "side":             side_label,
        "our_side_prob":    round(our_side_prob, 4) if our_side_prob is not None else None,
        "sharp_no_vig_prob": round(sharp_no_vig_prob, 4) if sharp_no_vig_prob is not None else None,
        "pp_line":          pp_line,
        "sharp_fair_line":  sharp_fair_line,
        "sharp_confirm_threshold":  SHARP_CONFIRM_THRESHOLD,
        "sharp_conflict_threshold": SHARP_CONFLICT_THRESHOLD,
        "detail":           detail_str,
        "can_approve_bets": False,
    }
