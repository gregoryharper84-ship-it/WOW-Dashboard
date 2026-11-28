"""Freshness and cross-source verification. Unverified or conflicting evidence fails closed."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Sequence

from .extract import ResultRow, team_key

# A past-dated match older than this with no recorded result makes a source stale.
RESULT_GRACE = timedelta(days=2)
# Longest normal gap between league rounds (international breaks included).
MAX_ROUND_GAP = timedelta(days=16)
MIN_OVERLAP = 10


def results_freshness(rows: Sequence[ResultRow], now: datetime) -> dict[str, Any]:
    today = now.date()
    scored = [r for r in rows if r.scored]
    unscored_past = [r for r in rows if not r.scored and r.day < today - RESULT_GRACE]
    latest = max((r.day for r in scored), default=None)
    if not rows:
        status = "UNKNOWN"
    elif unscored_past:
        status = "STALE"
    elif latest is None or latest < today - MAX_ROUND_GAP:
        status = "STALE"
    else:
        status = "FRESH"
    return {"status": status, "rows": len(rows), "scored": len(scored),
            "unscored_past_dated": len(unscored_past),
            "latest_result_date": latest.isoformat() if latest else None,
            "as_of_date": today.isoformat()}


def _similar(a: str, b: str) -> float:
    ta, tb = set(team_key(a).split()), set(team_key(b).split())
    if not ta or not tb:
        return 0.0
    if team_key(a) == team_key(b):
        return 1.0
    # Prefix-aware token overlap ("wolverhampton" ~ "wolverhampton wanderers").
    hits = sum(1 for x in ta if any(y.startswith(x) or x.startswith(y) for y in tb))
    return hits / max(len(ta), len(tb))


def cross_check(candidate: Sequence[ResultRow], reference: Sequence[ResultRow],
                *, min_overlap: int = MIN_OVERLAP) -> dict[str, Any]:
    """Compare scores on matches both sources record. Any confident disagreement is a conflict."""
    by_day: dict[date, list[ResultRow]] = {}
    for row in reference:
        if row.scored:
            by_day.setdefault(row.day, []).append(row)
    overlap = 0
    conflicts = []
    for row in candidate:
        if not row.scored:
            continue
        options = []
        for ref in by_day.get(row.day, []):
            score = min(_similar(row.home, ref.home), _similar(row.away, ref.away))
            if score >= 0.5:
                options.append((score, ref))
        if len(options) != 1:
            continue  # unmatched or ambiguous pairing: not evidence either way
        ref = options[0][1]
        overlap += 1
        if (row.home_goals, row.away_goals) != (ref.home_goals, ref.away_goals):
            conflicts.append({"date": row.day.isoformat(), "candidate": [row.home, row.away, row.home_goals,
                              row.away_goals], "reference": [ref.home, ref.away, ref.home_goals, ref.away_goals]})
    if conflicts:
        status = "CONFLICT"
    elif overlap < min_overlap:
        status = "INSUFFICIENT_OVERLAP"
    else:
        status = "CONSISTENT"
    return {"status": status, "overlap": overlap, "conflicts": conflicts[:20], "conflict_count": len(conflicts)}
