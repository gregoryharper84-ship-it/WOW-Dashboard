"""
market_adverse.py — WOW-PATCH-2026-07-15 Section 2: Settlement-Aware Market Delta

Compare ACTUAL winning/push/losing outcomes — not raw line numbers alone.

A PrizePicks line is MARKET_ADVERSE when it:
  - requires a more extreme result to win;
  - converts a sportsbook push into a PrizePicks loss;
  - lacks affirmative sportsbook support; or
  - opposes the consensus/no-vig side.

Gates:
  REJECT_MARKET_ADVERSE_PUSH_LOSS  — SB push converts to PP loss
  REJECT_MARKET_ADVERSE_THRESHOLD  — PP threshold is materially more extreme
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel


# ---------------------------------------------------------------------------
# Settlement normalisation
# ---------------------------------------------------------------------------

def normalize_settlement(
    line: float,
    direction: str,
    platform: str = "prizepicks",
) -> dict[str, Any]:
    """
    Return the win/push/loss outcome sets for a given line and direction.

    For PrizePicks (half-integer lines have no push; whole-number lines
    on PP also have no push — they settle MORE/LESS strictly):
      MORE (OVER): win > line, loss ≤ line, push: none (PP is strict)
      LESS (UNDER): win < line, loss ≥ line, push: none

    For sportsbook (whole-number lines have a push at exactly the line):
      OVER: win > line, push = line (if whole number), loss < line
      UNDER: win < line, push = line (if whole number), loss > line
    """
    direction = direction.upper()
    is_whole = (line == int(line))

    if platform.lower() in ("prizepicks", "pp", "underdog", "parlay_play"):
        # PrizePicks settles strictly — no push at any line
        if direction in ("MORE", "OVER"):
            return {
                "platform":    platform,
                "line":        line,
                "direction":   direction,
                "win_zone":    f"result > {line}",
                "push_zone":   "none",
                "loss_zone":   f"result ≤ {line}",
                "push_values": [],
            }
        else:  # LESS / UNDER
            return {
                "platform":    platform,
                "line":        line,
                "direction":   direction,
                "win_zone":    f"result < {line}",
                "push_zone":   "none",
                "loss_zone":   f"result ≥ {line}",
                "push_values": [],
            }
    else:
        # Sportsbook: whole-number line = push
        push_vals = [line] if is_whole else []
        if direction in ("MORE", "OVER"):
            return {
                "platform":    platform,
                "line":        line,
                "direction":   direction,
                "win_zone":    f"result > {line}",
                "push_zone":   f"result = {line}" if is_whole else "none",
                "loss_zone":   f"result < {line}",
                "push_values": push_vals,
            }
        else:  # LESS / UNDER
            return {
                "platform":    platform,
                "line":        line,
                "direction":   direction,
                "win_zone":    f"result < {line}",
                "push_zone":   f"result = {line}" if is_whole else "none",
                "loss_zone":   f"result > {line}",
                "push_values": push_vals,
            }


# ---------------------------------------------------------------------------
# Core adversity check
# ---------------------------------------------------------------------------

def check_market_adverse(
    pp_line:   float,
    direction: str,
    sb_line:   float | None,
    source:    str = "sportsbook",
) -> tuple[str | None, dict[str, Any]]:
    """
    Determine if the PP line is adverse relative to the sportsbook line.

    Returns (label_or_None, detail_dict).

    Logic:
      MORE (OVER): PP is adverse when pp_line > sb_line
        (PP requires a more extreme result than the sportsbook market)
      LESS (UNDER): PP is adverse when pp_line < sb_line
        (same principle, other direction)

    Adversity type:
      PUSH_LOSS  — diff ≤ 0.5 AND sb_line is a whole number:
                   the SB push value falls in PP's loss zone
      THRESHOLD  — diff > 0.5: PP's winning interval is materially smaller
    """
    if sb_line is None:
        return None, {"reason": "no_sportsbook_line"}

    direction = direction.upper()
    diff = abs(pp_line - sb_line)

    if direction in ("MORE", "OVER"):
        adverse = pp_line > sb_line
    else:  # LESS, UNDER
        adverse = pp_line < sb_line

    if not adverse:
        return None, {
            "adverse": False,
            "pp_line": pp_line,
            "sb_line": sb_line,
            "direction": direction,
            "threshold_delta": round(diff, 2),
        }

    sb_is_whole = (sb_line == int(sb_line))

    # Compute settlement zones for full output
    pp_settlement = normalize_settlement(pp_line, direction, "prizepicks")
    sb_settlement = normalize_settlement(sb_line, direction, "sportsbook")

    base_detail: dict[str, Any] = {
        "adverse":            True,
        "pp_line":            pp_line,
        "sb_line":            sb_line,
        "direction":          direction,
        "threshold_delta":    round(diff, 2),
        "sb_push_values":     sb_settlement["push_values"],
        "pp_settlement":      pp_settlement,
        "sb_settlement":      sb_settlement,
        "market_source":      source,
    }

    # PUSH_LOSS: the gap is exactly 0.5 (adjacent half-point) and the SB line
    # is a whole number — that whole number is a push at the sportsbook but
    # falls in PP's loss zone.
    if diff <= 0.5 and sb_is_whole:
        base_detail["adverse_type"] = "PUSH_LOSS"
        base_detail["push_equity_delta"] = (
            f"SB push at {sb_line:.0f} converts to PP loss at {pp_line}"
        )
        return "REJECT_MARKET_ADVERSE_PUSH_LOSS", base_detail

    # THRESHOLD: the winning zones differ materially (gap > 0.5)
    base_detail["adverse_type"] = "THRESHOLD"
    base_detail["push_equity_delta"] = (
        f"PP requires result {'>' if direction in ('MORE','OVER') else '<'} {pp_line} "
        f"but sportsbook consensus only requires "
        f"result {'>' if direction in ('MORE','OVER') else '<'} {sb_line}"
    )
    return "REJECT_MARKET_ADVERSE_THRESHOLD", base_detail


# ---------------------------------------------------------------------------
# Gate entry point (modifies row in-place)
# ---------------------------------------------------------------------------

def run(
    row: dict[str, Any],
    sportsbook_line:  float | None = None,
    consensus_line:   float | None = None,
    best_available:   float | None = None,
) -> dict[str, Any]:
    """
    Settlement-aware market delta gate.

    Checks the PrizePicks line (row["line"]) against all available market
    references. Returns a gate result and, if adverse, stamps the row with
    the appropriate terminal label.

    Gate result at row["gates"]["market_adverse"].
    """
    pp_line   = row.get("line")
    direction = (row.get("direction") or "MORE").upper()

    if pp_line is None:
        result = {
            "passed":    True,
            "skipped":   True,
            "reason":    "no_pp_line",
            "label":     None,
        }
        row.setdefault("gates", {})["market_adverse"] = result
        return row

    # Check each available reference in priority order
    checks: list[tuple[float, str]] = []
    if sportsbook_line is not None:
        checks.append((sportsbook_line, "sportsbook"))
    if consensus_line is not None:
        checks.append((consensus_line, "consensus"))
    if best_available is not None:
        checks.append((best_available, "best_available"))

    if not checks:
        result = {
            "passed":    True,
            "skipped":   True,
            "reason":    "no_market_reference",
            "label":     None,
        }
        row.setdefault("gates", {})["market_adverse"] = result
        return row

    findings: list[dict[str, Any]] = []
    worst_label: str | None = None

    for ref_line, source in checks:
        label, detail = check_market_adverse(pp_line, direction, ref_line, source)
        findings.append({"source": source, "label": label, **detail})
        if label is not None:
            # Prefer PUSH_LOSS over THRESHOLD when both fire
            if worst_label is None:
                worst_label = label
            elif label == "REJECT_MARKET_ADVERSE_PUSH_LOSS":
                worst_label = label

    passed = worst_label is None

    result: dict[str, Any] = {
        "passed":    passed,
        "label":     worst_label,
        "findings":  findings,
        "pp_line":   pp_line,
        "direction": direction,
    }
    row.setdefault("gates", {})["market_adverse"] = result

    if not passed:
        lbl = (
            PropLabel.REJECT_MARKET_ADVERSE_PUSH_LOSS.value
            if worst_label == "REJECT_MARKET_ADVERSE_PUSH_LOSS"
            else PropLabel.REJECT_MARKET_ADVERSE_THRESHOLD.value
        )
        row["terminal_label"] = lbl
        row["blockers"].append(f"MARKET_ADVERSE:{worst_label}:pp={pp_line}:sb={checks[0][0]}")

    return row
