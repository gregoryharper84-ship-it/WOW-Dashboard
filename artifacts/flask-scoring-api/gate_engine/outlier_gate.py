"""
outlier_gate.py
Detect outlier-inflated averages and volatility flags.
Based on WOW v16 June 6 patch rules.
"""
from __future__ import annotations

import statistics
from typing import Any

GAP_THRESHOLD        = 0.20
ASSIST_VOL_THRESHOLD = 0.40
WHOLE_NUMBER_PUSH_TOLS = 0.0


def run(row: dict[str, Any]) -> dict[str, Any]:
    """
    Reads l5_l10_ledger gate results and adds outlier flags.
    Never blocks on its own — only adds flags downstream gates use.

    Flags:
      avg_inflated_by_outlier   bool
      l5_l10_gap_pct            float
      l5_l10_gap_flagged        bool
      season_high_outlier       bool
      assist_volatile           bool
      median_disagrees_avg      bool
      whole_number_push_risk    bool
      small_sample_warning      bool (passthrough)
    """
    ledger = row.get("gates", {}).get("l5_l10_ledger", {})

    if not ledger.get("passed"):
        result = {
            "passed": True,
            "skipped": True,
            "reason": "L5L10_NOT_AVAILABLE",
            "flags": {},
        }
        row["gates"]["outlier_gate"] = result
        return row

    l5_avg  = ledger.get("l5_avg")
    l10_avg = ledger.get("l10_avg")
    l5_med  = ledger.get("l5_median")
    l10_med = ledger.get("l10_median")
    l10_games = ledger.get("l10_games", [])
    prop_type = (row.get("prop_type") or "").lower()

    flags: dict[str, Any] = {}

    gap_pct = None
    if l5_avg and l10_avg and l10_avg > 0:
        gap_pct = abs(l5_avg - l10_avg) / l10_avg
        flags["l5_l10_gap_pct"]   = round(gap_pct, 3)
        flags["l5_l10_gap_flagged"] = gap_pct > GAP_THRESHOLD
    else:
        flags["l5_l10_gap_pct"]     = None
        flags["l5_l10_gap_flagged"] = False

    if l10_games and len(l10_games) >= 3:
        season_max = max(l10_games)
        avg_without_max = statistics.mean([g for g in l10_games if g != season_max] or l10_games)
        inflated = (l10_avg or 0) > avg_without_max * 1.15
        flags["avg_inflated_by_outlier"] = inflated
        flags["season_high_outlier"]     = season_max > (l10_avg or 0) * 1.5
    else:
        flags["avg_inflated_by_outlier"] = False
        flags["season_high_outlier"]     = False

    if "assist" in prop_type:
        if l10_games and len(l10_games) >= 3:
            try:
                stdev = statistics.stdev(l10_games)
                flags["assist_volatile"] = stdev > (l10_avg or 1) * ASSIST_VOL_THRESHOLD
            except statistics.StatisticsError:
                flags["assist_volatile"] = False
        else:
            flags["assist_volatile"] = False
    else:
        flags["assist_volatile"] = False

    if l5_avg is not None and l5_med is not None:
        flags["median_disagrees_avg"] = abs(l5_avg - l5_med) > 1.5
    else:
        flags["median_disagrees_avg"] = False

    line = row.get("line")
    if line is not None:
        flags["whole_number_push_risk"] = float(line) == round(float(line))
    else:
        flags["whole_number_push_risk"] = False

    flags["small_sample_warning"] = ledger.get("small_sample_warning", False)

    any_flag = any([
        flags.get("l5_l10_gap_flagged"),
        flags.get("avg_inflated_by_outlier"),
        flags.get("season_high_outlier"),
        flags.get("assist_volatile"),
        flags.get("median_disagrees_avg"),
    ])

    if any_flag:
        row["blockers"].append("OUTLIER_FLAG:REVIEW_REQUIRED")

    result = {
        "passed":    True,
        "skipped":   False,
        "any_flag":  any_flag,
        "flags":     flags,
    }
    row["gates"]["outlier_gate"] = result
    return row
