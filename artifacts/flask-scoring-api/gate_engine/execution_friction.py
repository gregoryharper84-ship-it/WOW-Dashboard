"""
execution_friction.py  —  Patch: Execution Friction / Liquidity Gate
WOW v16 / Patch 2026-06-27

Final-lock execution check for PrizePicks (primary) and sportsbooks.

For PrizePicks the issue is line/payout drift and slip EV decay, not
traditional market liquidity. This gate verifies the execution context
is still valid at the moment of lock decision.

Required final-lock checks:
  - current_pp_line     matches analysis_pp_line within tolerance
  - current_payout      matches analysis_payout within tolerance
  - slip_type           has not changed (Power ↔ Flex)
  - pick_count          has not changed
  - line_timestamp_age  is under MAX_LINE_AGE_SECONDS
  - player_status       confirmed (not updated to Out/GTD since analysis)
  - no payout downgrade (payout did not decrease from analysis)
  - no line move against side since analysis timestamp

Labels (stored in gates["execution_friction"] and blockers):
  EXECUTION_OK                    — all checks pass
  REJECT_EXECUTION_STALE          — line/status data too old
  REJECT_PAYOUT_CHANGED           — payout multiplier changed against us
  REJECT_LINE_MOVED_AGAINST_SIDE  — line moved unfavorably for our side
  REJECT_LOW_LIQUIDITY            — sportsbook market too thin (future use)
  CAUTION_LINE_AGE                — approaching staleness threshold but not yet reject

Use check_standalone() for the Final Lock Dashboard endpoint.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Maximum acceptable line timestamp age (seconds) — 30 seconds for live final lock
MAX_LINE_AGE_SECONDS = 30

# Payout tolerance — any decrease > this fraction is a reject
PAYOUT_DECREASE_TOLERANCE = 0.05   # 5% payout drop triggers reject

# Line tolerance — line moves > this against our side trigger reject
LINE_MOVE_TOLERANCE = 0.25         # quarter-unit line move against side

# Caution threshold — warn at 60% of max age
LINE_AGE_CAUTION_FRACTION = 0.60


def check_standalone(
    *,
    analysis_pp_line:       float | None,
    current_pp_line:        float | None,
    analysis_payout:        float | None,
    current_payout:         float | None,
    analysis_slip_type:     str | None,
    current_slip_type:      str | None,
    analysis_pick_count:    int | None,
    current_pick_count:     int | None,
    line_timestamp_utc:     str | None,    # ISO-8601 UTC string of when line was last verified
    player_status:          str | None,    # current player status (Healthy/Out/GTD/...)
    side:                   str | None,    # MORE/OVER or LESS/UNDER
    platform:               str = "prizepicks",
    max_line_age_seconds:   int = MAX_LINE_AGE_SECONDS,
) -> dict[str, Any]:
    """
    Standalone execution friction check for the Final Lock Dashboard.

    All parameters are keyword-only to prevent positional mistakes.
    """
    side_upper   = (side or "MORE").upper().strip()
    side_is_over = side_upper in ("MORE", "OVER")
    platform_lc  = (platform or "prizepicks").lower()

    checks:  list[dict[str, Any]] = []
    rejects: list[str]             = []
    cautions: list[str]            = []

    # ------------------------------------------------------------------
    # Check 1: Line timestamp age
    # ------------------------------------------------------------------
    line_age_seconds: float | None = None
    if line_timestamp_utc:
        try:
            ts = datetime.fromisoformat(line_timestamp_utc.replace("Z", "+00:00"))
            now_utc = datetime.now(tz=timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            line_age_seconds = (now_utc - ts).total_seconds()

            if line_age_seconds > max_line_age_seconds:
                rejects.append(PropLabel.REJECT_EXECUTION_STALE.value)
                checks.append({
                    "check": "line_age",
                    "result": "FAIL",
                    "age_seconds": round(line_age_seconds, 1),
                    "max_allowed": max_line_age_seconds,
                    "detail": f"Line data is {line_age_seconds:.0f}s old (max {max_line_age_seconds}s).",
                })
            elif line_age_seconds > max_line_age_seconds * LINE_AGE_CAUTION_FRACTION:
                cautions.append("CAUTION_LINE_AGE")
                checks.append({
                    "check": "line_age",
                    "result": "CAUTION",
                    "age_seconds": round(line_age_seconds, 1),
                    "detail": f"Line data is {line_age_seconds:.0f}s old — approaching staleness.",
                })
            else:
                checks.append({
                    "check": "line_age",
                    "result": "PASS",
                    "age_seconds": round(line_age_seconds, 1),
                })
        except (ValueError, TypeError) as exc:
            cautions.append("CAUTION_TIMESTAMP_PARSE_ERROR")
            checks.append({"check": "line_age", "result": "UNKNOWN", "detail": str(exc)})
    else:
        cautions.append("CAUTION_NO_TIMESTAMP")
        checks.append({"check": "line_age", "result": "NO_DATA", "detail": "No line timestamp provided."})

    # ------------------------------------------------------------------
    # Check 2: Payout changed?
    # ------------------------------------------------------------------
    if analysis_payout is not None and current_payout is not None:
        payout_delta = current_payout - analysis_payout
        if payout_delta < -PAYOUT_DECREASE_TOLERANCE * analysis_payout:
            rejects.append(PropLabel.REJECT_PAYOUT_CHANGED.value)
            checks.append({
                "check": "payout",
                "result": "FAIL",
                "analysis_payout": analysis_payout,
                "current_payout": current_payout,
                "delta": round(payout_delta, 4),
                "detail": f"Payout dropped from {analysis_payout}× to {current_payout}× → REJECT_PAYOUT_CHANGED.",
            })
        else:
            checks.append({
                "check": "payout",
                "result": "PASS",
                "analysis_payout": analysis_payout,
                "current_payout": current_payout,
                "delta": round(payout_delta, 4) if current_payout and analysis_payout else None,
            })

    # ------------------------------------------------------------------
    # Check 3: Line moved against side?
    # ------------------------------------------------------------------
    if analysis_pp_line is not None and current_pp_line is not None:
        line_delta = current_pp_line - analysis_pp_line
        # For MORE/OVER: line increasing = worse (must clear higher bar)
        # For LESS/UNDER: line decreasing = worse (must go under lower bar)
        move_against = (side_is_over and line_delta > LINE_MOVE_TOLERANCE) or \
                       (not side_is_over and line_delta < -LINE_MOVE_TOLERANCE)

        if move_against:
            rejects.append(PropLabel.REJECT_LINE_MOVED_AGAINST_SIDE.value)
            checks.append({
                "check": "line_move",
                "result": "FAIL",
                "analysis_line": analysis_pp_line,
                "current_line": current_pp_line,
                "delta": round(line_delta, 3),
                "side": side_upper,
                "detail": (
                    f"Line moved {'+' if line_delta > 0 else ''}{line_delta:.2f} "
                    f"against {side_upper} → REJECT_LINE_MOVED_AGAINST_SIDE."
                ),
            })
        else:
            checks.append({
                "check": "line_move",
                "result": "PASS",
                "analysis_line": analysis_pp_line,
                "current_line": current_pp_line,
                "delta": round(line_delta, 3),
            })

    # ------------------------------------------------------------------
    # Check 4: Slip type / pick count changed?
    # ------------------------------------------------------------------
    if analysis_slip_type and current_slip_type:
        if analysis_slip_type != current_slip_type:
            cautions.append("CAUTION_SLIP_TYPE_CHANGED")
            checks.append({
                "check": "slip_type",
                "result": "CAUTION",
                "analysis": analysis_slip_type,
                "current":  current_slip_type,
                "detail": f"Slip type changed from {analysis_slip_type} to {current_slip_type}.",
            })
        else:
            checks.append({"check": "slip_type", "result": "PASS"})

    if analysis_pick_count and current_pick_count:
        if analysis_pick_count != current_pick_count:
            cautions.append("CAUTION_PICK_COUNT_CHANGED")
            checks.append({
                "check": "pick_count",
                "result": "CAUTION",
                "analysis": analysis_pick_count,
                "current":  current_pick_count,
            })
        else:
            checks.append({"check": "pick_count", "result": "PASS"})

    # ------------------------------------------------------------------
    # Check 5: Player status (not Out/Scratched since analysis)
    # ------------------------------------------------------------------
    if player_status is not None:
        status_upper = player_status.upper().strip()
        if status_upper in ("OUT", "SCRATCH", "SCRATCHED", "DNP", "INACTIVE"):
            rejects.append(PropLabel.REJECT_EXECUTION_STALE.value)
            checks.append({
                "check": "player_status",
                "result": "FAIL",
                "status": player_status,
                "detail": f"Player status updated to {player_status} — execution blocked.",
            })
        elif status_upper in ("GTD", "QUESTIONABLE", "DOUBTFUL"):
            cautions.append("CAUTION_STATUS_GTD")
            checks.append({
                "check": "player_status",
                "result": "CAUTION",
                "status": player_status,
                "detail": f"Player status is {player_status} — proceed with caution.",
            })
        else:
            checks.append({"check": "player_status", "result": "PASS", "status": player_status})

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------
    passed = len(rejects) == 0
    if rejects:
        # Most severe reject label wins
        primary_reject = rejects[0]
    else:
        primary_reject = None

    code = primary_reject or ("CAUTION" if cautions else "EXECUTION_OK")

    return {
        "passed":           passed,
        "code":             code,
        "rejects":          list(set(rejects)),
        "cautions":         list(set(cautions)),
        "checks":           checks,
        "line_age_seconds": line_age_seconds,
        "platform":         platform_lc,
        "can_approve_bets": False,
    }
