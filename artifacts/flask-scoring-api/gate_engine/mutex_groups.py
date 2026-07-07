"""
mutex_groups.py — Automatic Mutex Grouping + Best-Candidate Selection

Groups props by same-player / same-stat-family / same-pitcher-script and
selects one best candidate per group. Non-selected candidates get:
  terminal_label  = DUPLICATE_EXPOSURE_BLOCK
  mutex_group_id  = "<player>:<family>"
  rejected_due_to_mutex = True
  mutex_rejection_reason = "<why the selected candidate beat this one>"

Grouping rules
--------------
1. Same player + overlapping stat family
   (e.g. Howard Points vs Howard Pts+Reb — both in "points" family)

2. Same pitcher + pitcher-script stats
   All K / outs / pitches props for the same pitcher in the same game
   are grouped together as "pitcher_script" because outs drove Ks and
   pitch count is a direct function of both.

3. Exact duplicates
   Same player + same prop_type + same line — deduplicate completely.

Selection priority (highest wins)
----------------------------------
1. FINAL_APPROVED
2. MONEY_QUALIFIED
3. MARKET_VERIFIED_HOLD
4. MODEL_QUALIFIED_HOLD
5. RESEARCH_INTEREST
6. (anything else = lower priority)
Within the same label tier: higher edge_score wins; if equal, simpler
stat (shorter prop_type name) wins.
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Stat-family taxonomy
# ---------------------------------------------------------------------------

# Map canonical prop_type (lowercased, stripped) → family key
STAT_FAMILY_MAP: dict[str, str] = {
    # Points family
    "points":                   "points",
    "pts":                      "points",
    "player points":            "points",
    "pts+reb":                  "points",
    "pts+ast":                  "points",
    "pts+ast+reb":              "points",
    "pts+reb+ast":              "points",
    "pra":                      "points",
    "points+rebounds":          "points",
    "points+assists":           "points",
    "points+rebounds+assists":  "points",
    "pts+rebs":                 "points",
    "pts+asts":                 "points",
    # Rebounds family
    "rebounds":                 "rebounds",
    "reb":                      "rebounds",
    "rebs":                     "rebounds",
    "player rebounds":          "rebounds",
    "reb+ast":                  "rebounds",
    "rebounds+assists":         "rebounds",
    # Assists family
    "assists":                  "assists",
    "ast":                      "assists",
    "asts":                     "assists",
    "player assists":           "assists",
    # Pitcher outs family (Ks + outs + pitches are all correlated via pitcher script)
    "pitcher strikeouts":       "pitcher_script",
    "strikeouts":               "pitcher_script",
    "player strikeouts":        "pitcher_script",
    "ks":                       "pitcher_script",
    "pitcher ks":               "pitcher_script",
    "pitching outs":            "pitcher_script",
    "outs recorded":            "pitcher_script",
    "pitcher outs":             "pitcher_script",
    "outs":                     "pitcher_script",
    "pitching outs recorded":   "pitcher_script",
    "pitches thrown":           "pitcher_script",
    "pitch count":              "pitcher_script",
    "pitches":                  "pitcher_script",
    "total bases":              "batter_bases",
    "hits":                     "batter_hits",
    "batter hits":              "batter_hits",
    "home runs":                "batter_hr",
    "batter home runs":         "batter_hr",
}

# Labels that are eligible for selection (approval-path labels)
LABEL_PRIORITY: dict[str, int] = {
    PropLabel.FINAL_APPROVED.value:       100,
    PropLabel.MONEY_QUALIFIED.value:       80,
    PropLabel.MARKET_VERIFIED_HOLD.value:  60,
    PropLabel.MODEL_QUALIFIED_HOLD.value:  40,
    PropLabel.RESEARCH_INTEREST.value:     20,
}


def _stat_family(prop_type: str) -> str | None:
    """Return the family key for a prop type, or None if unrecognised."""
    return STAT_FAMILY_MAP.get((prop_type or "").lower().strip())


def _group_key(row: dict) -> str | None:
    """
    Return the mutex group key for a row.
    Returns None if the row should not be grouped (unique stat or unrecognised family).
    """
    player = (row.get("player") or "").strip().lower()
    prop   = (row.get("prop_type") or "").strip().lower()
    if not player or not prop:
        return None
    family = _stat_family(prop)
    if family is None:
        return None
    return f"{player}:{family}"


def _label_priority(row: dict) -> int:
    label = row.get("terminal_label") or ""
    return LABEL_PRIORITY.get(label, 0)


def _edge_score(row: dict) -> float:
    ev = (row.get("gates") or {}).get("ev_gate") or {}
    try:
        return float(ev.get("edge_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _select_best(group: list[dict]) -> dict:
    """
    From a group of rows, select the best candidate.
    Prefer: higher label priority → higher edge_score → simpler prop_type.
    """
    return max(
        group,
        key=lambda r: (
            _label_priority(r),
            _edge_score(r),
            -len((r.get("prop_type") or "")),   # shorter = simpler stat preferred
        ),
    )


def run(rows: list[dict]) -> list[dict]:
    """
    Annotate rows with mutex_group_id, selected_best_candidate, and
    rejected_due_to_mutex. Non-selected rows within a group get
    terminal_label = DUPLICATE_EXPOSURE_BLOCK.

    Returns a mutex report: list of {group_id, candidates, selected, rejected}.
    """
    groups: dict[str, list[dict]] = {}

    for row in rows:
        # Skip rows already terminated
        label = row.get("terminal_label")
        if label in (
            PropLabel.SLATE_PURGE.value,
            PropLabel.REJECT_DATA_QUALITY.value,
            PropLabel.DATA_CONTRACT_FAIL.value,
        ):
            continue
        key = _group_key(row)
        if key is None:
            continue
        groups.setdefault(key, []).append(row)

    report: list[dict] = []

    for group_id, group in groups.items():
        if len(group) < 2:
            # No conflict — annotate with group_id but no rejection
            for row in group:
                row["mutex_group_id"]        = group_id
                row["preferred_candidate"]   = True
                row["rejected_due_to_mutex"] = False
            continue

        best = _select_best(group)

        for row in group:
            row["mutex_group_id"] = group_id
            if row is best:
                row["preferred_candidate"]   = True
                row["rejected_due_to_mutex"] = False
            else:
                row["preferred_candidate"]   = False
                row["rejected_due_to_mutex"] = True
                best_label = best.get("terminal_label") or "NO_PLAY"
                best_prop  = best.get("prop_type") or "UNKNOWN"
                row["mutex_rejection_reason"] = (
                    f"mutex_group={group_id}; "
                    f"best_candidate={best_prop}({best_label}); "
                    f"edge_best={_edge_score(best):.3f}"
                )
                # Only override terminal_label if not already a harder rejection
                if _label_priority(row) > 0:
                    row["terminal_label"] = PropLabel.DUPLICATE_EXPOSURE_BLOCK.value
                    row["blockers"] = list(row.get("blockers") or []) + [
                        f"MUTEX:{group_id}"
                    ]

        report.append({
            "group_id":           group_id,
            "candidate_count":    len(group),
            "selected_prop":      best.get("prop_type"),
            "selected_player":    best.get("player"),
            "selected_label":     best.get("terminal_label"),
            "selected_edge":      _edge_score(best),
            "rejected":           [
                {
                    "player":    r.get("player"),
                    "prop_type": r.get("prop_type"),
                    "label":     r.get("terminal_label"),
                    "reason":    r.get("mutex_rejection_reason"),
                }
                for r in group if r is not best
            ],
        })

    return report
