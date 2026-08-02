"""
same_player_mutex.py  —  Same-Player Opportunity Mutex
WOW-PATCH-2026-08-01-MULTI-WINDOW-PROP-PERSISTENCE-AND-DISTRIBUTION-AUDIT

Detects when multiple candidates share the same player and event.
Same-player props share: minutes, injury risk, foul trouble, blowout risk,
possession volume, role changes.

Per Linemaker analysis §7:
  Same-player companion legs are a shared-thesis cluster, not a natural pairing.
  Only one should normally survive unless joint dependence is explicitly modeled.

Integration:
  Called from the prop scanner loop (parallel to the cross-slip governor).
  Must run BEFORE the portfolio governor, not instead of it.

can_execute=False unconditional.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Identity extraction
# ---------------------------------------------------------------------------

def _player_key(row: dict[str, Any]) -> str | None:
    """Normalize a player identity key from a row dict."""
    pid  = (row.get("player_id") or "").strip()
    name = (row.get("player_name") or row.get("player") or "").strip().lower()
    if pid:
        return f"pid:{pid}"
    if name:
        return f"name:{name}"
    return None


def _event_key(row: dict[str, Any]) -> str | None:
    """Normalize an event identity key from a row dict."""
    eid     = (row.get("event_id") or row.get("game_id") or "").strip()
    game_dt = (row.get("game_date") or row.get("event_date") or "").strip()[:10]  # YYYY-MM-DD
    team    = (row.get("team") or "").strip().upper()

    if eid:
        return f"event:{eid}"
    if game_dt and team:
        return f"game:{game_dt}:{team}"
    if game_dt:
        return f"date:{game_dt}"
    return None


def _row_key(row: dict[str, Any]) -> tuple[str | None, str | None]:
    return _player_key(row), _event_key(row)


# ---------------------------------------------------------------------------
# Shared exposure risk label
# ---------------------------------------------------------------------------

_SHARED_RISKS = [
    "minutes",
    "injury_risk",
    "foul_trouble",
    "blowout_risk",
    "possession_volume",
    "role_changes",
]


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def detect_same_player_clusters(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Detect all same-player / same-event clusters across a row list.

    Returns
    -------
    {
        "clusters":        list[dict],  # groups with >1 member
        "row_flags":       dict[str, dict],  # row_id → {in_cluster, cluster_id, is_primary}
        "total_clusters":  int,
        "total_blocked":   int,         # non-primary rows in clusters
    }

    Each cluster dict:
    {
        "cluster_id":              str,
        "player_key":              str,
        "event_key":               str,
        "member_row_ids":          list,
        "primary_survivor_row_id": any,    # highest calibrated_lower_bound
        "blocked_row_ids":         list,   # all non-primary
        "shared_risks":            list[str],
        "cluster_label":           "SAME_PLAYER_SHARED_THESIS",
        "resolution":              str,
    }
    """
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        pk, ek = _row_key(row)
        if pk is None or ek is None:
            continue
        key = (pk, ek)
        groups.setdefault(key, []).append(row)

    clusters: list[dict[str, Any]] = []
    row_flags: dict[Any, dict[str, Any]] = {}
    total_blocked = 0

    for (pk, ek), members in groups.items():
        if len(members) < 2:
            continue

        cluster_id = f"spm:{pk}:{ek}"

        # Primary: highest calibrated_lower_bound (or first if all equal)
        def _lb(r: dict) -> float:
            v = r.get("calibrated_lower_bound") or r.get("calibrated_prob_lower_bound") or 0.0
            return float(v)

        sorted_m = sorted(members, key=_lb, reverse=True)
        primary  = sorted_m[0]
        blocked  = sorted_m[1:]

        primary_id  = id(primary)
        blocked_ids = [id(b) for b in blocked]
        total_blocked += len(blocked)

        # Flag each member
        for m in members:
            is_primary = id(m) is primary_id or m is primary
            row_flags[id(m)] = {
                "in_cluster":   True,
                "cluster_id":   cluster_id,
                "is_primary":   is_primary,
                "cluster_label": "SAME_PLAYER_SHARED_THESIS",
            }

        blocked_tickers = [
            b.get("prop") or b.get("ticker") or b.get("player_prop") or str(id(b))
            for b in blocked
        ]
        primary_ticker = (
            primary.get("prop") or primary.get("ticker") or primary.get("player_prop")
            or str(primary_id)
        )

        resolution = (
            f"Only '{primary_ticker}' survives (highest calibrated_lower_bound). "
            f"Blocked: {blocked_tickers}. "
            f"Override requires joint_dependence_modeled=True."
        )

        clusters.append({
            "cluster_id":              cluster_id,
            "player_key":              pk,
            "event_key":               ek,
            "member_row_ids":          [id(m) for m in members],
            "primary_survivor_row_id": primary_id,
            "blocked_row_ids":         blocked_ids,
            "shared_risks":            list(_SHARED_RISKS),
            "cluster_label":           "SAME_PLAYER_SHARED_THESIS",
            "resolution":              resolution,
            "member_count":            len(members),
        })

    return {
        "clusters":       clusters,
        "row_flags":      row_flags,
        "total_clusters": len(clusters),
        "total_blocked":  total_blocked,
        "can_execute":    False,
    }


def apply_same_player_mutex(
    rows:                    list[dict[str, Any]],
    joint_dependence_modeled: bool = False,
) -> dict[str, Any]:
    """
    Apply the same-player mutex to a list of rows.

    Mutates each row in-place by adding:
        row["shared_thesis_cluster"]  = True | False
        row["same_player_blocked"]    = True | False
        row["cluster_id"]             = str | None
        row["same_player_block_reason"] = str | None

    Non-primary rows in a cluster have same_player_blocked=True unless
    joint_dependence_modeled=True.

    Returns
    -------
    {
        "rows_in":          int,
        "rows_blocked":     int,
        "clusters_found":   int,
        "cluster_detail":   list[dict],  # one per cluster
        "joint_dependence_modeled": bool,
        "can_execute":      False,
    }
    """
    detection = detect_same_player_clusters(rows)
    flags     = detection["row_flags"]

    blocked_count = 0

    for row in rows:
        rid  = id(row)
        info = flags.get(rid)
        if info:
            row["shared_thesis_cluster"]   = True
            row["cluster_id"]              = info["cluster_id"]
            is_blocked = (not info["is_primary"]) and (not joint_dependence_modeled)
            row["same_player_blocked"]     = is_blocked
            if is_blocked:
                row["same_player_block_reason"] = (
                    f"SAME_PLAYER_SHARED_THESIS — non-primary leg in cluster "
                    f"{info['cluster_id']}. Set joint_dependence_modeled=True to override."
                )
                blocked_count += 1
            else:
                row["same_player_block_reason"] = None
        else:
            row["shared_thesis_cluster"]   = False
            row["same_player_blocked"]     = False
            row["cluster_id"]              = None
            row["same_player_block_reason"] = None

    return {
        "rows_in":                  len(rows),
        "rows_blocked":             blocked_count,
        "clusters_found":           detection["total_clusters"],
        "cluster_detail":           detection["clusters"],
        "joint_dependence_modeled": joint_dependence_modeled,
        "can_execute":              False,
    }


def check_same_game_correlated_legs(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Detect same-game correlated multi-player groups (e.g., Dodgers trio stacks).

    Flags groups of 2+ different players sharing the same event/game.
    These share: game environment, pitcher performance, weather, umpire,
    game length, bullpen quality, lineup volume.

    Does NOT auto-block them — returns a warning + correlation notes.
    The cross-slip governor and portfolio governor handle actual blocking.

    Returns
    -------
    {
        "same_game_groups":      list[dict],
        "total_correlated_legs": int,
        "independence_warning":  str | None,
        "can_execute":           False,
    }
    """
    event_players: dict[str, set[str]] = {}
    event_rows:    dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        ek = _event_key(row)
        pk = _player_key(row)
        if not ek or not pk:
            continue
        event_players.setdefault(ek, set()).add(pk)
        event_rows.setdefault(ek, []).append(row)

    groups: list[dict[str, Any]] = []
    for ek, players in event_players.items():
        if len(players) < 2:
            continue
        er = event_rows[ek]
        groups.append({
            "event_key":    ek,
            "player_count": len(players),
            "player_keys":  sorted(players),
            "row_count":    len(er),
            "shared_factors": [
                "game_environment", "pitcher_performance", "weather",
                "umpire", "game_length", "bullpen_quality", "lineup_volume",
            ],
            "warning": (
                f"{len(players)} players from same event share correlated game factors. "
                "Combined structure requires dependence modeling; independent multiplication prohibited."
            ),
        })

    total_legs = sum(g["row_count"] for g in groups)
    ind_warning: str | None = None
    if groups:
        ind_warning = (
            f"SAME_GAME_MULTI_LEG: {len(groups)} correlated event group(s) detected "
            f"({total_legs} legs total). Independent probability multiplication is prohibited. "
            "Route through cross-slip governor before approval."
        )

    return {
        "same_game_groups":      groups,
        "total_correlated_legs": total_legs,
        "independence_warning":  ind_warning,
        "can_execute":           False,
    }
