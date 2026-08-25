"""
validation/baselines/season_empirical.py

Baseline A — Pitcher season empirical distribution.

Strategy: for each test sample, use ALL ledger_rows available for that pitcher
in the current season to estimate P(hit).  No weighting by recency.

This is a strong baseline because it uses exactly the same data the model
uses — the difference between Baseline A and WOW_LEAN_1IP shows how much
value the event-tree simulation adds over a naive hit-rate.

Output: list of (predicted_prob_or_None, hit_bool) in the same order as input.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

BASELINE_ID      = "season_empirical"
BASELINE_VERSION = "1.0"


def predict_batch(
    rows: List[dict[str, Any]],
) -> List[Tuple[Optional[float], str]]:
    """
    For each row, compute season empirical P(hit) from all ledger_rows.

    Parameters
    ----------
    rows  List of dicts, each containing:
          - "ledger_rows": list of savant ledger row dicts (each may have "hit")
          - "line": float pitch-count line
          - "direction": "LESS" | "MORE"
          - "hit": bool (actual outcome) — used for evaluation, not prediction

    Returns
    -------
    List of (predicted_prob, BASELINE_ID) tuples.  predicted_prob is None
    when the ledger is empty or contains no "hit" data.
    """
    results = []
    for row in rows:
        ledger_rows = row.get("ledger_rows") or []
        line        = float(row.get("line", 0))
        direction   = (row.get("direction") or "LESS").upper()

        # Filter rows that have a "hit" label (requires line+direction were set)
        labeled = [r for r in ledger_rows if r.get("hit") in {"HIT", "MISS"}]

        if not labeled:
            results.append((None, BASELINE_ID))
            continue

        n_hits = sum(1 for r in labeled if r["hit"] == "HIT")
        prob   = n_hits / len(labeled)
        results.append((round(prob, 4), BASELINE_ID))

    return results


def predict_single(
    ledger_rows: list,
    line: float,
    direction: str,
) -> dict:
    """
    Predict for a single pitcher.  Returns a metadata dict.

    Parameters
    ----------
    ledger_rows  All available ledger rows for the pitcher/season.
    line         Pitch-count line.
    direction    "LESS" | "MORE".

    Returns
    -------
    dict with keys: probability, n_season_starts, baseline_id, baseline_version
    """
    direction = direction.upper()
    labeled   = []
    for r in ledger_rows:
        pitches = r.get("first_inning_pitches")
        if pitches is None:
            continue
        if direction == "LESS":
            labeled.append(int(pitches < line))
        else:
            labeled.append(int(pitches > line))

    prob = round(sum(labeled) / len(labeled), 4) if labeled else None
    return {
        "probability":       prob,
        "n_season_starts":   len(labeled),
        "baseline_id":       BASELINE_ID,
        "baseline_version":  BASELINE_VERSION,
        "note":              "Season empirical hit rate across all available starts",
    }
