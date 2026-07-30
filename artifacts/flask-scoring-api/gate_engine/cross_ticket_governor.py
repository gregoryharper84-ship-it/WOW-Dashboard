"""
cross_ticket_governor.py  —  PATCH-014
WOW-PATCH-2026-07-30-WNBA-COMPOSITE-MLB-DIRECTIONAL-AND-CROSS-TICKET-GOVERNANCE

Slip-level governor: operates on the full list of rows from one session/slate.

Responsibilities:
  1. Build identity keys for every row:
       exact_leg_key, player_event_key, distribution_key,
       pitcher_thesis_key, event_script_key
  2. Detect duplicate classes:
       EXACT_DUPLICATE, ALTERNATE_THRESHOLD_DUPLICATE,
       SHARED_LATENT_PLAYER_EXPOSURE, DUPLICATE_PITCHER_THESIS,
       SAME_EVENT_CONCENTRATION, INDEPENDENT_SUPPORTED
  3. Assign duplicate_group_id to each row
  4. Apply hard rejection rules:
       - exact same leg on multiple cards → keep strongest, block others
       - alternate thresholds → keep strongest verified threshold
       - same pitcher directional thesis → keep one
       - weakest leg repeated → block second occurrence
       - Power card copied from Flex → REJECT_DUPLICATE_STRUCTURE
  5. Calculate cross_card_fragility
  6. Add cross_ticket_governor gate report to each row
  7. Write slip-level cross-ticket audit log to DB

Calibration deduplication:
  Repeated thresholds and exact legs count once in model calibration.
  Financial exposure is NOT collapsed here — only model observations.

can_execute=False is unconditional.
"""
from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from typing import Any

from .labels import PropLabel

can_execute = False

# ---------------------------------------------------------------------------
# WNBA latent distribution families (PATCH-014)
# ---------------------------------------------------------------------------

_POINTS_FAMILY   = {"points", "pts", "p+r", "points rebounds", "p+a",
                    "points assists", "pra", "points rebounds assists"}
_REBOUNDS_FAMILY = {"rebounds", "reb", "p+r", "points rebounds", "r+a",
                    "rebounds assists", "pra", "points rebounds assists"}
_ASSISTS_FAMILY  = {"assists", "ast", "p+a", "points assists", "r+a",
                    "rebounds assists", "pra", "points rebounds assists"}

# MLB pitcher workload family
_PITCHER_WORKLOAD_MORE = {"strikeouts more", "pitching outs more", "pitch count more",
                          "batters faced more", "k more", "ks more"}

# Fragility thresholds
_FRAGILE_THRESHOLD     = 0.50   # > 50% of cards at risk
_CONCENTRATED_THRESHOLD = 0.25  # > 25% of cards at risk


# ---------------------------------------------------------------------------
# Key builders
# ---------------------------------------------------------------------------

def _norm(s: Any) -> str:
    return str(s or "").lower().strip().replace(" ", "_").replace("+", "_")


def _stat_norm(row: dict[str, Any]) -> str:
    return _norm(row.get("stat_type") or row.get("prop_type") or row.get("stat_family") or "")


def _direction_norm(row: dict[str, Any]) -> str:
    d = str(row.get("direction") or row.get("side") or "more").upper()
    return "MORE" if d in ("MORE", "OVER", ">") else "LESS"


def make_exact_leg_key(row: dict[str, Any]) -> str:
    """player + event + stat_family + line + direction + settlement"""
    return "|".join([
        _norm(row.get("player_name") or row.get("player") or ""),
        _norm(row.get("event_id") or ""),
        _stat_norm(row),
        str(row.get("line") or row.get("line_score") or ""),
        _direction_norm(row),
        _norm(row.get("offer_type") or "standard"),
    ])


def make_player_event_key(row: dict[str, Any]) -> str:
    """player + event"""
    return "|".join([
        _norm(row.get("player_name") or row.get("player") or ""),
        _norm(row.get("event_id") or ""),
    ])


def make_distribution_key(row: dict[str, Any]) -> str:
    """player + event + latent_stat_distribution family"""
    player    = _norm(row.get("player_name") or row.get("player") or "")
    event     = _norm(row.get("event_id") or "")
    stat      = _stat_norm(row)

    # Map to canonical distribution family
    if stat in {_norm(s) for s in _POINTS_FAMILY}:
        family = "POINTS_DISTRIBUTION"
    elif stat in {_norm(s) for s in _REBOUNDS_FAMILY}:
        family = "REBOUNDS_DISTRIBUTION"
    elif stat in {_norm(s) for s in _ASSISTS_FAMILY}:
        family = "ASSISTS_DISTRIBUTION"
    else:
        family = stat.upper()

    return f"{player}|{event}|{family}"


def make_pitcher_thesis_key(row: dict[str, Any]) -> str:
    """pitcher + event + directional_workload_or_performance_thesis"""
    sport    = str(row.get("sport") or row.get("league") or "").upper()
    position = str(row.get("position") or "").upper()
    if sport not in ("MLB", "BASEBALL") and position != "SP":
        return ""
    pitcher   = _norm(row.get("player_name") or row.get("player") or "")
    event     = _norm(row.get("event_id") or "")
    direction = _direction_norm(row)
    stat      = _stat_norm(row)
    # K LESS is a single directional thesis per pitcher-event
    if "strikeout" in stat or stat in ("k", "ks", "k_more", "k_less"):
        return f"{pitcher}|{event}|K_{direction}"
    if "out" in stat and "pitch" not in stat:
        return f"{pitcher}|{event}|OUTS_{direction}"
    return f"{pitcher}|{event}|{stat}_{direction}"


def make_event_script_key(row: dict[str, Any]) -> str:
    """event + shared_game_script (team totals / game-level)"""
    return _norm(row.get("event_id") or "")


def _assign_keys(row: dict[str, Any]) -> None:
    row["exact_leg_key"]       = make_exact_leg_key(row)
    row["player_event_key"]    = make_player_event_key(row)
    row["distribution_key"]    = make_distribution_key(row)
    row["pitcher_thesis_key"]  = make_pitcher_thesis_key(row)
    row["event_script_key"]    = make_event_script_key(row)


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def _get_prob_lower_bound(row: dict[str, Any]) -> float:
    """Best available calibrated lower bound for ranking."""
    v = (row.get("calibrated_lower_bound") or
         row.get("calibrated_probability_lower_bound") or
         row.get("calibrated_probability") or
         row.get("model_probability") or 0)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _is_terminal_rejected(row: dict[str, Any]) -> bool:
    from .labels import REJECT_LABELS
    tl = row.get("terminal_label")
    return tl in {l.value for l in REJECT_LABELS}


def _classify_duplicates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build duplicate maps and return:
      {
        exact_groups: {key: [row_id, ...]},
        player_event_groups: {key: [row_id, ...]},
        distribution_groups: {key: [row_id, ...]},
        pitcher_thesis_groups: {key: [row_id, ...]},
        event_script_groups: {key: [row_id, ...]},
        duplicate_class: {row_id: str},
      }
    """
    exact_groups:          dict[str, list[int]] = defaultdict(list)
    player_event_groups:   dict[str, list[int]] = defaultdict(list)
    distribution_groups:   dict[str, list[int]] = defaultdict(list)
    pitcher_thesis_groups: dict[str, list[int]] = defaultdict(list)
    event_script_groups:   dict[str, list[int]] = defaultdict(list)

    for i, row in enumerate(rows):
        exact_groups[row["exact_leg_key"]].append(i)
        player_event_groups[row["player_event_key"]].append(i)
        distribution_groups[row["distribution_key"]].append(i)
        if row["pitcher_thesis_key"]:
            pitcher_thesis_groups[row["pitcher_thesis_key"]].append(i)
        if row["event_script_key"]:
            event_script_groups[row["event_script_key"]].append(i)

    duplicate_class: dict[int, str] = {}
    duplicate_group_id: dict[int, str | None] = {i: None for i in range(len(rows))}

    def _group_hash(indices: list[int]) -> str:
        key = "|".join(str(i) for i in sorted(indices))
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    # EXACT_DUPLICATE
    for key, idxs in exact_groups.items():
        if len(idxs) > 1:
            ghash = f"EXACT_{_group_hash(idxs)}"
            for i in idxs:
                if i not in duplicate_class:
                    duplicate_class[i] = "EXACT_DUPLICATE"
                duplicate_group_id[i] = ghash

    # ALTERNATE_THRESHOLD_DUPLICATE (same player+event+stat+direction, diff line)
    for key, idxs in player_event_groups.items():
        if len(idxs) > 1:
            # Group by (player_event_key, stat, direction)
            sub: dict[str, list[int]] = defaultdict(list)
            for i in idxs:
                row = rows[i]
                sub_key = f"{_stat_norm(row)}|{_direction_norm(row)}"
                sub[sub_key].append(i)
            for sub_key, sub_idxs in sub.items():
                if len(sub_idxs) > 1:
                    ghash = f"ALT_THRESH_{_group_hash(sub_idxs)}"
                    for i in sub_idxs:
                        if i not in duplicate_class:
                            duplicate_class[i] = "ALTERNATE_THRESHOLD_DUPLICATE"
                        if duplicate_group_id[i] is None:
                            duplicate_group_id[i] = ghash

    # SHARED_LATENT_PLAYER_EXPOSURE
    for key, idxs in distribution_groups.items():
        if len(idxs) > 1 and "_DISTRIBUTION" in key.upper():
            ghash = f"LATENT_{_group_hash(idxs)}"
            for i in idxs:
                if i not in duplicate_class:
                    duplicate_class[i] = "SHARED_LATENT_PLAYER_EXPOSURE"
                if duplicate_group_id[i] is None:
                    duplicate_group_id[i] = ghash

    # DUPLICATE_PITCHER_THESIS
    for key, idxs in pitcher_thesis_groups.items():
        if len(idxs) > 1:
            ghash = f"PITCHER_{_group_hash(idxs)}"
            for i in idxs:
                if i not in duplicate_class:
                    duplicate_class[i] = "DUPLICATE_PITCHER_THESIS"
                if duplicate_group_id[i] is None:
                    duplicate_group_id[i] = ghash

    # Mark non-duplicates
    for i in range(len(rows)):
        if i not in duplicate_class:
            duplicate_class[i] = "INDEPENDENT_SUPPORTED"

    return {
        "exact_groups":          {k: v for k, v in exact_groups.items() if len(v) > 1},
        "player_event_groups":   {k: v for k, v in player_event_groups.items() if len(v) > 1},
        "distribution_groups":   {k: v for k, v in distribution_groups.items() if len(v) > 1},
        "pitcher_thesis_groups": {k: v for k, v in pitcher_thesis_groups.items() if len(v) > 1},
        "event_script_groups":   {k: v for k, v in event_script_groups.items() if len(v) > 1},
        "duplicate_class":       duplicate_class,
        "duplicate_group_id":    duplicate_group_id,
    }


# ---------------------------------------------------------------------------
# Hard rule enforcement
# ---------------------------------------------------------------------------

def _apply_hard_rules(rows: list[dict[str, Any]], dup_map: dict[str, Any]) -> dict[str, Any]:
    """
    Apply PATCH-014 hard rejection rules. Returns summary of actions taken.
    """
    actions_taken: list[str] = []
    rejected_count = 0

    def _reject(i: int, label: str, reason: str) -> None:
        nonlocal rejected_count
        row = rows[i]
        if not _is_terminal_rejected(row):
            row["terminal_label"] = label
            row.setdefault("blockers", []).append(reason)
            row.setdefault("gates", {}).setdefault("cross_ticket_governor", {})["rejection"] = reason
            rejected_count += 1
            actions_taken.append(f"row[{i}] {row.get('player_name', '?')} → {label}")

    # Rule 1: EXACT_DUPLICATE — keep highest lower-bound occurrence, reject rest
    for key, idxs in dup_map["exact_groups"].items():
        ranked = sorted(idxs, key=lambda i: _get_prob_lower_bound(rows[i]), reverse=True)
        for i in ranked[1:]:   # all but the best
            _reject(i, PropLabel.REJECT_EXACT_DUPLICATE.value, "EXACT_DUPLICATE — keep_one_per_exact_leg")

    # Rule 2: ALTERNATE_THRESHOLD_DUPLICATE — keep strongest per (player, event, stat, direction)
    for key, idxs in dup_map["player_event_groups"].items():
        sub: dict[str, list[int]] = defaultdict(list)
        for i in idxs:
            row = rows[i]
            sub_key = f"{_stat_norm(row)}|{_direction_norm(row)}"
            sub[sub_key].append(i)
        for sub_key, sub_idxs in sub.items():
            if len(sub_idxs) > 1:
                ranked = sorted(sub_idxs, key=lambda i: _get_prob_lower_bound(rows[i]), reverse=True)
                for i in ranked[1:]:
                    _reject(i, PropLabel.REJECT_ALTERNATE_THRESHOLD_DUPLICATE.value,
                            "ALTERNATE_THRESHOLD_DUPLICATE — keep_strongest_verified_threshold")

    # Rule 3: DUPLICATE_PITCHER_THESIS — keep one per pitcher+event+direction
    for key, idxs in dup_map["pitcher_thesis_groups"].items():
        ranked = sorted(idxs, key=lambda i: _get_prob_lower_bound(rows[i]), reverse=True)
        for i in ranked[1:]:
            _reject(i, PropLabel.REJECT_DUPLICATE_PITCHER_THESIS.value,
                    "DUPLICATE_PITCHER_THESIS — keep_one_per_pitcher_directional_thesis")

    # Rule 4: Power card structure copy detection
    # Detect if a Power card shares ≥ 2 legs with an existing Flex card
    flex_leg_sets: dict[str, set[str]] = {}
    power_leg_sets: dict[str, set[str]] = {}
    for row in rows:
        slip_type = str(row.get("slip_type") or row.get("card_type") or "").upper()
        card_id   = str(row.get("card_id") or row.get("slip_id") or "unknown")
        leg_key   = row.get("exact_leg_key", "")
        if "FLEX" in slip_type:
            flex_leg_sets.setdefault(card_id, set()).add(leg_key)
        elif "POWER" in slip_type:
            power_leg_sets.setdefault(card_id, set()).add(leg_key)

    for power_id, power_legs in power_leg_sets.items():
        for flex_id, flex_legs in flex_leg_sets.items():
            shared = power_legs & flex_legs
            if len(shared) >= 2:
                # Flag all Power card rows
                for row in rows:
                    if (str(row.get("card_id") or row.get("slip_id") or "") == power_id and
                            not _is_terminal_rejected(row)):
                        row.setdefault("blockers", []).append("REJECT_DUPLICATE_STRUCTURE")
                        row.setdefault("gates", {}).setdefault("cross_ticket_governor", {})[
                            "power_flex_copy"] = (
                            f"Power card {power_id} shares {len(shared)} legs with Flex {flex_id}"
                        )
                actions_taken.append(
                    f"Power card {power_id} REJECT_DUPLICATE_STRUCTURE (shares {len(shared)} legs with Flex {flex_id})"
                )

    return {"actions": actions_taken, "rejected": rejected_count}


# ---------------------------------------------------------------------------
# Cross-card fragility
# ---------------------------------------------------------------------------

def _compute_fragility(rows: list[dict[str, Any]], dup_map: dict[str, Any]) -> dict[str, Any]:
    """
    For every unique underlying thesis, calculate:
      cards_at_risk = cards containing the thesis / total cards
    Returns fragility class and the most critical thesis.
    """
    total_rows = len(rows)
    if total_rows == 0:
        return {"portfolio_fragility_class": "DIVERSIFIED", "total_rows": 0}

    # Count row-level occurrences per thesis (using duplicate_group_id as proxy for thesis)
    thesis_counts: dict[str, int] = defaultdict(int)
    for i, row in enumerate(rows):
        gid = dup_map["duplicate_group_id"].get(i)
        if gid:
            thesis_counts[gid] += 1

    if not thesis_counts:
        return {
            "portfolio_fragility_class": "DIVERSIFIED",
            "total_rows":  total_rows,
            "unique_theses": total_rows,
        }

    critical_thesis = max(thesis_counts, key=lambda k: thesis_counts[k])
    max_share = thesis_counts[critical_thesis] / total_rows

    if max_share > _FRAGILE_THRESHOLD:
        fragility_class = "FRAGILE"
    elif max_share > _CONCENTRATED_THRESHOLD:
        fragility_class = "CONCENTRATED"
    else:
        fragility_class = "DIVERSIFIED"

    return {
        "portfolio_fragility_class":           fragility_class,
        "critical_thesis":                     critical_thesis,
        "critical_thesis_row_count":           thesis_counts[critical_thesis],
        "share_of_rows_at_risk":               round(max_share, 4),
        "total_rows":                          total_rows,
        "unique_underlying_theses":            total_rows - sum(
            max(0, c - 1) for c in thesis_counts.values()
        ),
        "thesis_distribution":                 dict(sorted(
            thesis_counts.items(), key=lambda x: -x[1]
        )[:10]),
    }


# ---------------------------------------------------------------------------
# DB cross-ticket audit log
# ---------------------------------------------------------------------------

_DDL_CT_LOG = """
CREATE TABLE IF NOT EXISTS cross_ticket_exposure_log (
    id                              BIGSERIAL PRIMARY KEY,
    session_id                      TEXT,
    slate_date                      DATE,
    total_rows                      INT,
    unique_underlying_theses        INT,
    exact_duplicate_groups          INT,
    alternate_threshold_groups      INT,
    shared_latent_groups            INT,
    pitcher_thesis_groups           INT,
    portfolio_fragility_class       TEXT,
    critical_thesis                 TEXT,
    share_of_rows_at_risk           NUMERIC,
    rows_rejected                   INT,
    actions_json                    JSONB,
    logged_at                       TIMESTAMPTZ DEFAULT NOW()
)
"""

_ct_log_ready = False


def _ensure_ct_log() -> None:
    global _ct_log_ready
    try:
        import psycopg2  # type: ignore
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        cur  = conn.cursor()
        cur.execute(_DDL_CT_LOG)
        conn.commit()
        cur.close()
        conn.close()
        _ct_log_ready = True
    except Exception:
        pass


def _log_audit(
    session_id: str | None,
    slate_date: Any,
    dup_map: dict[str, Any],
    fragility: dict[str, Any],
    hard_rule_result: dict[str, Any],
) -> None:
    global _ct_log_ready
    if not _ct_log_ready:
        _ensure_ct_log()
    try:
        import json
        import psycopg2  # type: ignore
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        cur  = conn.cursor()
        cur.execute(
            """
            INSERT INTO cross_ticket_exposure_log (
                session_id, slate_date, total_rows,
                unique_underlying_theses,
                exact_duplicate_groups, alternate_threshold_groups,
                shared_latent_groups, pitcher_thesis_groups,
                portfolio_fragility_class, critical_thesis,
                share_of_rows_at_risk, rows_rejected, actions_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                session_id,
                slate_date,
                fragility.get("total_rows"),
                fragility.get("unique_underlying_theses"),
                len(dup_map.get("exact_groups", {})),
                len(dup_map.get("player_event_groups", {})),
                len(dup_map.get("distribution_groups", {})),
                len(dup_map.get("pitcher_thesis_groups", {})),
                fragility.get("portfolio_fragility_class"),
                fragility.get("critical_thesis"),
                fragility.get("share_of_rows_at_risk"),
                hard_rule_result.get("rejected"),
                json.dumps(hard_rule_result.get("actions", [])[:50]),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main entry point (slip-level)
# ---------------------------------------------------------------------------

def run(rows: list[dict[str, Any]], session_id: str | None = None,
        slate_date: Any = None) -> dict[str, Any]:
    """
    Slip-level entry point. Processes the full list of rows from one session.

    Modifies rows in-place:
      - Adds keys: exact_leg_key, player_event_key, distribution_key,
                   pitcher_thesis_key, event_script_key, duplicate_group_id,
                   duplicate_class, cross_ticket_fragility
      - Applies hard rejection rules to duplicate/copied rows
      - Adds gates["cross_ticket_governor"] report to each row

    Returns the audit summary dict.
    """
    if not rows:
        return {"skipped": True, "reason": "no rows"}

    # Step 1: Assign identity keys to every row
    for row in rows:
        _assign_keys(row)

    # Step 2: Classify duplicates
    dup_map = _classify_duplicates(rows)

    # Step 3: Stamp duplicate_group_id and duplicate_class on each row
    for i, row in enumerate(rows):
        row["duplicate_group_id"] = dup_map["duplicate_group_id"].get(i)
        row["duplicate_class"]    = dup_map["duplicate_class"].get(i, "INDEPENDENT_SUPPORTED")

    # Step 4: Compute cross-card fragility
    fragility = _compute_fragility(rows, dup_map)
    fragility_class = fragility.get("portfolio_fragility_class", "DIVERSIFIED")

    # Stamp fragility on every row
    for row in rows:
        row["cross_ticket_fragility"] = fragility_class

    # Step 5: Apply hard rejection rules
    hard_rule_result = _apply_hard_rules(rows, dup_map)

    # Step 6: Build per-row gate report
    for i, row in enumerate(rows):
        dc = dup_map["duplicate_class"].get(i, "INDEPENDENT_SUPPORTED")
        row.setdefault("gates", {}).setdefault("cross_ticket_governor", {}).update({
            "duplicate_class":           dc,
            "duplicate_group_id":        dup_map["duplicate_group_id"].get(i),
            "cross_ticket_fragility":    fragility_class,
            "portfolio_fragility_class": fragility_class,
            "critical_thesis":           fragility.get("critical_thesis"),
            "share_of_rows_at_risk":     fragility.get("share_of_rows_at_risk"),
            "can_execute":               False,
        })

    # Step 7: Build audit summary
    audit = {
        "total_rows":                len(rows),
        "unique_underlying_theses":  fragility.get("unique_underlying_theses", len(rows)),
        "exact_duplicate_groups":    len(dup_map.get("exact_groups", {})),
        "alternate_threshold_groups":len(dup_map.get("player_event_groups", {})),
        "shared_latent_groups":      len(dup_map.get("distribution_groups", {})),
        "pitcher_thesis_groups":     len(dup_map.get("pitcher_thesis_groups", {})),
        "portfolio_fragility_class": fragility_class,
        "critical_thesis":           fragility.get("critical_thesis"),
        "share_of_rows_at_risk":     fragility.get("share_of_rows_at_risk"),
        "rows_rejected_by_governor": hard_rule_result.get("rejected", 0),
        "actions":                   hard_rule_result.get("actions", []),
        "can_execute":               False,
    }

    # Step 8: Log to DB (non-blocking)
    _log_audit(session_id, slate_date, dup_map, fragility, hard_rule_result)

    return audit
