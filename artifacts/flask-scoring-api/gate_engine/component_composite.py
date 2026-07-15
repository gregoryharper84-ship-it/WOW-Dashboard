"""
component_composite.py — WOW-PATCH-2026-07-15 Section 4: Component/Composite Mutex

Create a distribution graph per player/event and block conflicting exposure
across the entire session.

Conflicts blocked:
  P+R MORE  + rebounds LESS              → COMPONENT_COMPOSITE_CONFLICT
  PRA MORE  + points LESS                → COMPONENT_COMPOSITE_CONFLICT
  FGA MORE  + points/rebounds LESS       → REJECT_CONTRADICTORY_ROLE_STATE
                                           (unless joint simulation provided)
  Intentional middle requires explicit joint simulation + mutually compatible
  winning intervals + correlation review.
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel


# ---------------------------------------------------------------------------
# Stat family graph
# ---------------------------------------------------------------------------

# Canonical stat families
STAT_FAMILY_ALIASES: dict[str, str] = {
    # points
    "points": "points", "pts": "points", "point": "points",
    # rebounds
    "rebounds": "rebounds", "reb": "rebounds", "rebound": "rebounds",
    # assists
    "assists": "assists", "ast": "assists", "assist": "assists",
    # FGA
    "fga": "fga", "field goals attempted": "fga",
    "field goal attempts": "fga", "field_goals_attempted": "fga",
    # steals
    "steals": "steals", "stl": "steals",
    # blocks
    "blocks": "blocks", "blk": "blocks",
    # composites
    "pra": "pra", "pts+reb+ast": "pra", "pts_reb_ast": "pra",
    "p+r": "p+r", "pts+reb": "p+r", "pts_reb": "p+r", "points+rebounds": "p+r",
    "p+a": "p+a", "pts+ast": "p+a", "pts_ast": "p+a", "points+assists": "p+a",
    "r+a": "r+a", "reb+ast": "r+a", "reb_ast": "r+a", "rebounds+assists": "r+a",
    "fantasy_score": "fantasy_score", "fantasy": "fantasy_score",
}

# Which atomic stats does each composite contain?
COMPOSITE_COMPONENTS: dict[str, frozenset[str]] = {
    "pra":          frozenset({"points", "rebounds", "assists"}),
    "p+r":          frozenset({"points", "rebounds"}),
    "p+a":          frozenset({"points", "assists"}),
    "r+a":          frozenset({"rebounds", "assists"}),
    "fantasy_score": frozenset({"points", "rebounds", "assists", "fga", "steals", "blocks"}),
}

# Which atomic stats does FGA correlate with (for the contradictory role check)?
FGA_CORRELATED: frozenset[str] = frozenset({"points"})

# Composites that are themselves distributions (not just sums)
COMPOSITE_FAMILIES: frozenset[str] = frozenset(COMPOSITE_COMPONENTS.keys())


def _normalize_stat(raw: str) -> str:
    """Normalize a raw prop_type string to a canonical stat family."""
    key = raw.lower().strip()
    return STAT_FAMILY_ALIASES.get(key, key)


def _components_of(stat: str) -> frozenset[str]:
    """Return the atomic component stats contained in a stat family."""
    return COMPOSITE_COMPONENTS.get(stat, frozenset({stat}))


# ---------------------------------------------------------------------------
# Core conflict detection
# ---------------------------------------------------------------------------

def detect_conflicts(
    player_rows: list[dict[str, Any]],
    joint_model_provided: bool = False,
) -> list[dict[str, Any]]:
    """
    Given all rows for a single player, detect component/composite conflicts.

    Returns list of conflict dicts:
        {
          label:       str  (COMPONENT_COMPOSITE_CONFLICT | REJECT_CONTRADICTORY_ROLE_STATE)
          stat_a:      str
          direction_a: str
          stat_b:      str
          direction_b: str
          detail:      str
        }
    """
    conflicts: list[dict[str, Any]] = []

    # Build a lookup: stat_family -> list of directions seen
    family_dirs: dict[str, list[str]] = {}
    for row in player_rows:
        raw  = row.get("prop_type") or ""
        stat = _normalize_stat(raw)
        direction = (row.get("direction") or "MORE").upper()
        family_dirs.setdefault(stat, []).append(direction)

    # Collect all (stat, direction) pairs
    pairs: list[tuple[str, str]] = []
    for stat, dirs in family_dirs.items():
        for d in dirs:
            pairs.append((stat, d))

    # Check every pair for conflicts
    for i, (stat_a, dir_a) in enumerate(pairs):
        for j, (stat_b, dir_b) in enumerate(pairs):
            if j <= i:
                continue
            conflict = _check_pair(stat_a, dir_a, stat_b, dir_b, joint_model_provided)
            if conflict:
                conflicts.append(conflict)

    return conflicts


def _check_pair(
    stat_a: str, dir_a: str,
    stat_b: str, dir_b: str,
    joint_model_provided: bool,
) -> dict[str, Any] | None:
    """Check if a single pair of (stat, direction) entries conflict."""
    # Opposite directions on the same underlying stat distribution
    # e.g., points MORE + points LESS
    if stat_a == stat_b and dir_a != dir_b:
        return {
            "label":       PropLabel.COMPONENT_COMPOSITE_CONFLICT.value,
            "stat_a":      stat_a,
            "direction_a": dir_a,
            "stat_b":      stat_b,
            "direction_b": dir_b,
            "detail":      f"Same stat in opposing directions: {stat_a}",
        }

    comps_a = _components_of(stat_a)
    comps_b = _components_of(stat_b)
    shared  = comps_a & comps_b

    if not shared:
        # No component overlap — check FGA contradictory role state
        # FGA MORE + rebounds LESS (orthogonal stats, role contradiction)
        if (
            {stat_a, stat_b} & {"fga"} and
            not joint_model_provided
        ):
            other = stat_b if stat_a == "fga" else stat_a
            other_dir = dir_b if stat_a == "fga" else dir_a
            fga_dir   = dir_a if stat_a == "fga" else dir_b
            # FGA MORE + points LESS or rebounds LESS → REJECT_CONTRADICTORY_ROLE_STATE
            if fga_dir == "MORE" and other_dir == "LESS" and other in FGA_CORRELATED | {"rebounds"}:
                return {
                    "label":       PropLabel.REJECT_CONTRADICTORY_ROLE_STATE.value,
                    "stat_a":      stat_a,
                    "direction_a": dir_a,
                    "stat_b":      stat_b,
                    "direction_b": dir_b,
                    "detail": (
                        f"FGA MORE contradicts {other} LESS without joint simulation; "
                        "requires explicit joint model and mutually compatible winning intervals"
                    ),
                }
        return None

    # Shared component exists — check if directions conflict
    # Composite A MORE and component LESS in stat_b (or vice versa)
    #   → composite MORE includes the component, but component is LESS
    conflict_found = False
    detail_msg = ""

    # Case 1: stat_a is composite MORE, stat_b is component LESS
    if stat_a in COMPOSITE_FAMILIES and dir_a == "MORE" and dir_b == "LESS":
        if stat_b in comps_a:
            conflict_found = True
            detail_msg = (
                f"{stat_a} MORE includes {stat_b}; "
                f"{stat_b} LESS contradicts composite MORE direction"
            )

    # Case 2: stat_b is composite MORE, stat_a is component LESS
    elif stat_b in COMPOSITE_FAMILIES and dir_b == "MORE" and dir_a == "LESS":
        if stat_a in comps_b:
            conflict_found = True
            detail_msg = (
                f"{stat_b} MORE includes {stat_a}; "
                f"{stat_a} LESS contradicts composite MORE direction"
            )

    # Case 3: stat_a is composite LESS, stat_b is component MORE
    elif stat_a in COMPOSITE_FAMILIES and dir_a == "LESS" and dir_b == "MORE":
        if stat_b in comps_a:
            conflict_found = True
            detail_msg = (
                f"{stat_a} LESS includes {stat_b}; "
                f"{stat_b} MORE contradicts composite LESS direction"
            )

    # Case 4: stat_b is composite LESS, stat_a is component MORE
    elif stat_b in COMPOSITE_FAMILIES and dir_b == "LESS" and dir_a == "MORE":
        if stat_a in comps_b:
            conflict_found = True
            detail_msg = (
                f"{stat_b} LESS includes {stat_a}; "
                f"{stat_a} MORE contradicts composite LESS direction"
            )

    if conflict_found:
        return {
            "label":       PropLabel.COMPONENT_COMPOSITE_CONFLICT.value,
            "stat_a":      stat_a,
            "direction_a": dir_a,
            "stat_b":      stat_b,
            "direction_b": dir_b,
            "detail":      detail_msg,
        }

    return None


# ---------------------------------------------------------------------------
# Gate entry point (slip-level — requires all rows)
# ---------------------------------------------------------------------------

def run(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Run the component/composite mutex gate over all rows.

    Groups rows by player, detects conflicts per player, and stamps
    conflicting rows with the appropriate terminal label and blocker.

    Returns a summary report.
    """
    by_player: dict[str, list[dict]] = {}
    for row in rows:
        player = (row.get("player") or "UNKNOWN").lower()
        by_player.setdefault(player, []).append(row)

    all_conflicts: list[dict[str, Any]] = []

    for player, player_rows in by_player.items():
        joint_model = any(
            row.get("enrichment_flags", {}).get("joint_model_provided")
            for row in player_rows
        )
        conflicts = detect_conflicts(player_rows, joint_model_provided=joint_model)
        if not conflicts:
            continue

        for conflict in conflicts:
            all_conflicts.append({"player": player, **conflict})

        # Stamp all rows for this player with the highest-severity conflict
        for conflict in conflicts:
            lbl = conflict["label"]
            for row in player_rows:
                if row.get("terminal_label") is None:
                    row["terminal_label"] = lbl
                row.setdefault("blockers", []).append(
                    f"{lbl}:{conflict['stat_a']}_{conflict['direction_a']}"
                    f"_vs_{conflict['stat_b']}_{conflict['direction_b']}"
                )
                row.setdefault("gates", {})["component_composite"] = {
                    "passed":    False,
                    "conflicts": conflicts,
                }

    # Stamp clean rows
    for row in rows:
        if "component_composite" not in row.get("gates", {}):
            row.setdefault("gates", {})["component_composite"] = {
                "passed":    True,
                "conflicts": [],
            }

    return {
        "conflicts_found":  len(all_conflicts),
        "conflicts":        all_conflicts,
        "players_checked":  len(by_player),
    }
