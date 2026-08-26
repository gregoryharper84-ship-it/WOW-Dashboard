"""
validation/baselines/l10_empirical.py

Baseline B — Pitcher L10 empirical distribution.

Strategy: use only the 10 most recent starts (chronologically) to estimate
P(hit).  Matches what savant_1ip_ledger already reports as l10_hit_rate.

This baseline is more responsive to recent form than Baseline A.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

BASELINE_ID      = "l10_empirical"
BASELINE_VERSION = "1.0"
L10_WINDOW       = 10


def predict_single(
    ledger_rows: list,
    line: float,
    direction: str,
    *,
    window: int = L10_WINDOW,
) -> dict:
    """
    Predict for a single pitcher using the last *window* starts.

    Parameters
    ----------
    ledger_rows  Ledger rows from savant_1ip_ledger (sorted ascending by date).
    line         Pitch-count line.
    direction    "LESS" | "MORE".
    window       Number of most-recent starts to use (default 10).

    Returns
    -------
    dict with keys: probability, n_starts_used, baseline_id, baseline_version
    """
    direction = direction.upper()

    # Sort ascending by game_date; take the last *window*
    try:
        sorted_rows = sorted(ledger_rows, key=lambda r: r.get("game_date", ""))
    except Exception:
        sorted_rows = list(ledger_rows)
    recent = sorted_rows[-window:]

    labeled = []
    for r in recent:
        pitches = r.get("first_inning_pitches")
        if pitches is None:
            continue
        if direction == "LESS":
            labeled.append(int(pitches < line))
        else:
            labeled.append(int(pitches > line))

    prob = round(sum(labeled) / len(labeled), 4) if labeled else None
    return {
        "probability":      prob,
        "n_starts_used":    len(labeled),
        "window":           window,
        "baseline_id":      BASELINE_ID,
        "baseline_version": BASELINE_VERSION,
        "note":             f"Empirical hit rate over last {window} starts",
    }


def predict_batch(
    rows: List[dict[str, Any]],
    *,
    window: int = L10_WINDOW,
) -> List[Tuple[Optional[float], str]]:
    """
    Predict for a list of rows.  Each row must contain "ledger_rows",
    "line", "direction".
    """
    results = []
    for row in rows:
        r = predict_single(
            row.get("ledger_rows") or [],
            float(row.get("line", 0)),
            row.get("direction", "LESS"),
            window=window,
        )
        results.append((r["probability"], BASELINE_ID))
    return results
