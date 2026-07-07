"""
pp_thresholds.py — PrizePicks Cash / Push / Loss Threshold Conversion

PrizePicks has a push/reboot rule for whole-number lines:
  MORE 5 assists  → cash = 6+, push = 5, loss = 4 or less
  LESS 5 assists  → cash = 4 or less, push = 5, loss = 6+
  MORE 5.5 points → cash = 6+, no push, loss = 5 or less

Implication: a sportsbook market at 4.5 OVER does NOT validate
"MORE 5" — you need 6+ to cash on PrizePicks, not 5+.

Any comparison of sportsbook/projection data against a PrizePicks line
MUST use cash_threshold, not displayed_line.
"""
from __future__ import annotations


def compute_pp_thresholds(line: float, side: str) -> dict:
    """
    Compute PrizePicks cash / push / loss thresholds for a single prop.

    Returns:
        displayed_line     — the number shown on the PrizePicks card
        side               — MORE or LESS
        cash_threshold     — minimum result needed to cash (>= for MORE, <= for LESS)
        push_threshold     — exact result that triggers push/reboot (None if half-line)
        loss_threshold     — result that guarantees a loss
        whole_number_line  — True when displayed_line is an integer
        push_possible      — True when a push outcome exists
        sportsbook_comp_note — human-readable warning for adjacent-market comparisons
    """
    side = (side or "MORE").upper()
    is_whole = (line == int(line))

    if side == "MORE":
        if is_whole:
            cash_threshold = line + 1        # must exceed, not just meet
            push_threshold: float | None = line
            loss_threshold = line - 1
            note = (
                f"MORE {line:.4g}: cash needs {cash_threshold:.4g}+. "
                f"Sportsbook {line - 0.5:.4g} OVER does NOT validate this line."
            )
        else:
            cash_threshold = line + 0.5      # effectively > line
            push_threshold = None
            loss_threshold = line - 0.5
            note = None
    else:  # LESS
        if is_whole:
            cash_threshold = line - 1        # must be below
            push_threshold = line
            loss_threshold = line + 1
            note = (
                f"LESS {line:.4g}: cash needs {cash_threshold:.4g} or fewer. "
                f"Sportsbook {line + 0.5:.4g} UNDER does NOT validate this line."
            )
        else:
            cash_threshold = line - 0.5
            push_threshold = None
            loss_threshold = line + 0.5
            note = None

    return {
        "displayed_line":       line,
        "side":                 side,
        "cash_threshold":       cash_threshold,
        "push_threshold":       push_threshold,
        "loss_threshold":       loss_threshold,
        "whole_number_line":    is_whole,
        "push_possible":        is_whole,
        "sportsbook_comp_note": note,
    }


def run(row: dict) -> None:
    """
    Attach pp_thresholds to a pipeline row in-place.
    Called once per row, early in the pipeline (after board_intake).
    The thresholds are used by ev_gate and market_gate for comparison.
    """
    line = row.get("line")
    side = row.get("direction") or row.get("side") or "MORE"
    if line is None:
        row["pp_thresholds"] = {
            "displayed_line":    None,
            "side":              side,
            "cash_threshold":    None,
            "push_threshold":    None,
            "loss_threshold":    None,
            "whole_number_line": None,
            "push_possible":     None,
            "sportsbook_comp_note": "LINE_MISSING",
        }
        return
    try:
        row["pp_thresholds"] = compute_pp_thresholds(float(line), str(side))
    except Exception as exc:
        row["pp_thresholds"] = {
            "displayed_line":    line,
            "side":              side,
            "cash_threshold":    None,
            "push_threshold":    None,
            "loss_threshold":    None,
            "whole_number_line": None,
            "push_possible":     None,
            "sportsbook_comp_note": f"THRESHOLD_ERROR:{exc}",
        }


def run_batch(rows: list[dict]) -> list[dict]:
    """
    Attach pp_thresholds to every row. Returns a summary ledger.
    """
    ledger = []
    for row in rows:
        run(row)
        t = row.get("pp_thresholds", {})
        ledger.append({
            "row_id":          row.get("row_id"),
            "player":          row.get("player"),
            "prop_type":       row.get("prop_type"),
            "displayed_line":  t.get("displayed_line"),
            "side":            t.get("side"),
            "cash_threshold":  t.get("cash_threshold"),
            "push_threshold":  t.get("push_threshold"),
            "loss_threshold":  t.get("loss_threshold"),
            "whole_number_line": t.get("whole_number_line"),
            "push_possible":   t.get("push_possible"),
            "sportsbook_comp_note": t.get("sportsbook_comp_note"),
        })
    return ledger
