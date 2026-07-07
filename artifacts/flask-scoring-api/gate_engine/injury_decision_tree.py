"""
injury_decision_tree.py
Resolve role-dependency trees for WNBA (and future) props where a player's
expected usage or performance is contingent on a teammate's injury status
or on game-script risk.

Runs per-row BEFORE final classification.

Classification contract:
  DEPENDENCY_CONFLICT     → classifier caps at MODEL_QUALIFIED_HOLD
  DEPENDENCY_UNRESOLVED   → classifier caps at MONEY_QUALIFIED (blocks FINAL_APPROVED)
  ROLE_STATE_STALE        → classifier caps at MONEY_QUALIFIED (blocks FINAL_APPROVED)
  DEPENDENCY_SUPPORTS_*   → no cap; supportive signal only
  DEPENDENCY_CLEAR        → no cap
  NO_DEPENDENCY           → no cap; gate passes silently

The injury tree CANNOT create FINAL_APPROVED by itself — it can only block or
downgrade rows that would otherwise be approved.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Status constants
# ─────────────────────────────────────────────────────────────────────────────
STATUS_DEPENDENCY_CLEAR        = "DEPENDENCY_CLEAR"
STATUS_DEPENDENCY_SUPPORTS_MORE = "DEPENDENCY_SUPPORTS_MORE"
STATUS_DEPENDENCY_SUPPORTS_LESS = "DEPENDENCY_SUPPORTS_LESS"
STATUS_DEPENDENCY_CONFLICT     = "DEPENDENCY_CONFLICT"
STATUS_DEPENDENCY_UNRESOLVED   = "DEPENDENCY_UNRESOLVED"
STATUS_ROLE_STATE_STALE        = "ROLE_STATE_STALE"
STATUS_NO_DEPENDENCY           = "NO_DEPENDENCY"

STALE_THRESHOLD_HOURS = 6


# ─────────────────────────────────────────────────────────────────────────────
# Hardcoded dependency rules
# ─────────────────────────────────────────────────────────────────────────────
# Each rule entry:
#   beneficiary_keywords   — substrings checked against lowercased row player name
#   prop_trigger_keywords  — any substring match against lowercased row prop_type
#   direction              — "MORE" / "LESS" / None (all directions)
#   dependency_player      — display name of the dependency player (None for game-script)
#   dependency_key         — key used to look up in dependency_status_payload
#   role_effect_direction  — "upgrade" | "downgrade" | "context" | "risk"
#   approval_condition     — human-readable description of what clears the dependency
#   support_statuses       — dependency statuses that SUPPORT the bet direction
#   conflict_statuses      — dependency statuses that CONFLICT with the bet
#   unresolved_statuses    — statuses where the path is unclear
#
DEPENDENCY_RULES: list[dict[str, Any]] = [
    # ── Rule 1: Courtney Williams MORE assists ← Olivia Miles ────────────────
    {
        "beneficiary_keywords":  ["courtney williams"],
        "prop_trigger_keywords": ["assists", "ast", "p+a"],
        "direction":             "MORE",
        "dependency_player":     "Olivia Miles",
        "dependency_key":        "olivia miles",
        "role_effect_direction": "upgrade",
        "approval_condition":    "Miles OUT/limited clears Williams primary usage/assist path",
        "support_statuses":      {"OUT", "LIMITED"},
        "conflict_statuses":     {"ACTIVE", "FULL_GO", "FULL-GO", "FULL GO", "AVAILABLE"},
        "unresolved_statuses":   {"QUESTIONABLE", "PROBABLE", "GTD",
                                  "GAME-TIME DECISION", "DAY-TO-DAY"},
    },
    # ── Rule 2: Georgia Amoore MORE points ← Sonia Citron ────────────────────
    {
        "beneficiary_keywords":  ["georgia amoore"],
        "prop_trigger_keywords": ["points", "pts", "p+a", "p+r", "p+a+r"],
        "direction":             "MORE",
        "dependency_player":     "Sonia Citron",
        "dependency_key":        "sonia citron",
        "role_effect_direction": "upgrade",
        "approval_condition":    "Citron OUT/limited supports Amoore role/minutes/usage path",
        "support_statuses":      {"OUT", "LIMITED"},
        "conflict_statuses":     {"ACTIVE", "FULL_GO", "FULL-GO", "FULL GO", "AVAILABLE"},
        "unresolved_statuses":   {"QUESTIONABLE", "PROBABLE", "GTD", "GAME-TIME DECISION"},
    },
    # ── Rule 3: Coti McMahon MORE points ← Sonia Citron ──────────────────────
    {
        "beneficiary_keywords":  ["coti mcmahan", "coti mcmahon"],
        "prop_trigger_keywords": ["points", "pts", "p+a", "p+r", "p+a+r"],
        "direction":             "MORE",
        "dependency_player":     "Sonia Citron",
        "dependency_key":        "sonia citron",
        "role_effect_direction": "upgrade",
        "approval_condition":    "Citron OUT/limited supports McMahon role/minutes/usage path",
        "support_statuses":      {"OUT", "LIMITED"},
        "conflict_statuses":     {"ACTIVE", "FULL_GO", "FULL-GO", "FULL GO", "AVAILABLE"},
        "unresolved_statuses":   {"QUESTIONABLE", "PROBABLE", "GTD", "GAME-TIME DECISION"},
    },
    # ── Rule 4a: Ariel Atkins MORE points/combos ← Kelsey Plum ───────────────
    {
        "beneficiary_keywords":  ["ariel atkins"],
        "prop_trigger_keywords": ["points", "pts", "p+r", "p+a", "p+a+r"],
        "direction":             "MORE",
        "dependency_player":     "Kelsey Plum",
        "dependency_key":        "kelsey plum",
        "role_effect_direction": "upgrade",
        "approval_condition":    "Plum OUT supports Atkins usage/creation path",
        "support_statuses":      {"OUT", "LIMITED"},
        "conflict_statuses":     {"ACTIVE", "FULL_GO", "FULL-GO", "FULL GO", "AVAILABLE"},
        "unresolved_statuses":   {"QUESTIONABLE", "PROBABLE", "GTD", "GAME-TIME DECISION"},
    },
    # ── Rule 4b: Ariel Atkins MORE combos ← Cameron Brink (context only) ─────
    # Brink ACTIVE is neutral for Atkins; only Brink OUT adds context.
    {
        "beneficiary_keywords":  ["ariel atkins"],
        "prop_trigger_keywords": ["p+r", "p+a+r", "rebounds"],
        "direction":             "MORE",
        "dependency_player":     "Cameron Brink",
        "dependency_key":        "cameron brink",
        "role_effect_direction": "context",
        "approval_condition":    "Brink OUT alters frontcourt/rebound context for Atkins",
        "support_statuses":      {"OUT", "LIMITED"},
        "conflict_statuses":     set(),
        "unresolved_statuses":   set(),
    },
    # ── Rule 5a: Dearica Hamby MORE points/combos ← Kelsey Plum ──────────────
    {
        "beneficiary_keywords":  ["dearica hamby"],
        "prop_trigger_keywords": ["points", "pts", "p+r", "p+a", "p+a+r"],
        "direction":             "MORE",
        "dependency_player":     "Kelsey Plum",
        "dependency_key":        "kelsey plum",
        "role_effect_direction": "upgrade",
        "approval_condition":    "Plum OUT supports Hamby usage/creation path",
        "support_statuses":      {"OUT", "LIMITED"},
        "conflict_statuses":     {"ACTIVE", "FULL_GO", "FULL-GO", "FULL GO", "AVAILABLE"},
        "unresolved_statuses":   {"QUESTIONABLE", "PROBABLE", "GTD", "GAME-TIME DECISION"},
    },
    # ── Rule 5b: Dearica Hamby MORE combos ← Cameron Brink (context only) ────
    {
        "beneficiary_keywords":  ["dearica hamby"],
        "prop_trigger_keywords": ["p+r", "p+a+r", "rebounds"],
        "direction":             "MORE",
        "dependency_player":     "Cameron Brink",
        "dependency_key":        "cameron brink",
        "role_effect_direction": "context",
        "approval_condition":    "Brink OUT alters frontcourt/rebound context for Hamby",
        "support_statuses":      {"OUT", "LIMITED"},
        "conflict_statuses":     set(),
        "unresolved_statuses":   set(),
    },
    # ── Rule 6: Natasha Howard MORE points/P+R ← game-script / blowout risk ──
    {
        "beneficiary_keywords":  ["natasha howard"],
        "prop_trigger_keywords": ["points", "pts", "p+r", "p+a+r", "rebounds", "reb"],
        "direction":             "MORE",
        "dependency_player":     None,            # not a player — game-script risk
        "dependency_key":        "_game_script",   # special sentinel in dep payload
        "role_effect_direction": "risk",
        "approval_condition":    "Blowout/game-script risk absent before Howard MORE approval",
        "support_statuses":      {"NEUTRAL", "COMPETITIVE", "CLOSE_GAME"},
        "conflict_statuses":     {"BLOWOUT_RISK", "HEAVY_FAVORITE", "GARBAGE_TIME_RISK"},
        "unresolved_statuses":   {"UNKNOWN", "TBD"},
    },
]

# Priority order for combining multiple evaluations: higher = more restrictive
_STATUS_PRIORITY: dict[str, int] = {
    STATUS_DEPENDENCY_CONFLICT:     5,
    STATUS_DEPENDENCY_UNRESOLVED:   4,
    STATUS_ROLE_STATE_STALE:        3,
    STATUS_DEPENDENCY_SUPPORTS_LESS: 2,
    STATUS_DEPENDENCY_SUPPORTS_MORE: 2,
    STATUS_DEPENDENCY_CLEAR:        1,
    STATUS_NO_DEPENDENCY:           0,
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(
    row: dict[str, Any],
    dependency_status_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Evaluate injury / role dependency for one row.

    dependency_status_payload  maps lowercased player names (or the sentinel
    "_game_script") to status dicts of the form:
        { "status": str, "confirmed_at": str (ISO-8601), "source": str }

    Writes row["gates"]["injury_decision_tree"] and appends to row["blockers"].
    """
    player    = (row.get("player")    or "").lower()
    prop_type = (row.get("prop_type") or "").lower()
    direction = (row.get("direction") or "").upper()

    dep_payload = dependency_status_payload or {}
    matching    = _find_matching_rules(player, prop_type, direction)

    if not matching:
        result = _no_dependency_result()
        row.setdefault("gates", {})["injury_decision_tree"] = result
        return row

    evaluated = [_evaluate_rule(r, dep_payload) for r in matching]
    combined  = _combine_evaluations(evaluated)

    blocker: str | None = None
    status = combined["injury_tree_status"]
    if status in (STATUS_DEPENDENCY_CONFLICT,
                  STATUS_DEPENDENCY_UNRESOLVED,
                  STATUS_ROLE_STATE_STALE):
        dep_name = combined.get("dependency_player") or "unknown"
        blocker = f"INJURY_TREE:{status}:dep={dep_name}"
        row.setdefault("blockers", []).append(blocker)

    combined["passed"]              = status != STATUS_DEPENDENCY_CONFLICT
    combined["injury_tree_blocker"] = blocker
    combined["all_evaluations"]     = evaluated

    row.setdefault("gates", {})["injury_decision_tree"] = combined
    return row


def run_batch(
    rows: list[dict[str, Any]],
    dependency_status_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run run() over a list of rows; return the injury_decision_ledger."""
    for row in rows:
        run(row, dependency_status_payload=dependency_status_payload)
    return build_injury_decision_ledger(rows)


def build_injury_decision_ledger(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the per-row injury decision ledger from already-processed rows."""
    ledger = []
    for row in rows:
        inj = (row.get("gates") or {}).get("injury_decision_tree") or {}
        ledger.append({
            "row_id":                 row.get("row_id"),
            "player":                 row.get("player"),
            "prop_type":              row.get("prop_type"),
            "direction":              row.get("direction"),
            "injury_dependency_flag": inj.get("injury_dependency_flag", False),
            "dependency_player":      inj.get("dependency_player"),
            "dependency_status":      inj.get("dependency_status"),
            "role_state":             inj.get("role_state"),
            "role_state_timestamp":   inj.get("role_state_timestamp"),
            "role_effect_direction":  inj.get("role_effect_direction"),
            "approval_condition":     inj.get("approval_condition"),
            "injury_tree_status":     inj.get("injury_tree_status"),
            "injury_tree_blocker":    inj.get("injury_tree_blocker"),
            "terminal_label":         row.get("terminal_label"),
        })
    return ledger


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _no_dependency_result() -> dict[str, Any]:
    return {
        "passed":                 True,
        "injury_dependency_flag": False,
        "dependency_player":      None,
        "dependency_status":      None,
        "role_state":             STATUS_NO_DEPENDENCY,
        "role_state_timestamp":   None,
        "role_effect_direction":  None,
        "approval_condition":     None,
        "injury_tree_status":     STATUS_NO_DEPENDENCY,
        "injury_tree_blocker":    None,
        "all_evaluations":        [],
    }


def _find_matching_rules(
    player: str, prop_type: str, direction: str
) -> list[dict[str, Any]]:
    matches = []
    for rule in DEPENDENCY_RULES:
        if not any(kw in player for kw in rule["beneficiary_keywords"]):
            continue
        if not any(kw in prop_type for kw in rule["prop_trigger_keywords"]):
            continue
        if rule["direction"] is not None and rule["direction"] != direction:
            continue
        matches.append(rule)
    return matches


def _evaluate_rule(
    rule: dict[str, Any],
    dep_payload: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate a single dependency rule against the available status payload."""
    dep_key      = rule["dependency_key"]
    dep_info     = dep_payload.get(dep_key) or {}
    raw_status   = str(dep_info.get("status", "") or "").upper().strip()
    confirmed_at = dep_info.get("confirmed_at")

    base = {
        "dependency_player":     rule["dependency_player"],
        "dependency_status":     raw_status or None,
        "role_state_timestamp":  confirmed_at,
        "role_effect_direction": rule["role_effect_direction"],
        "approval_condition":    rule["approval_condition"],
        "_rule_matched":         rule,
    }

    if not dep_info:
        return {**base,
                "dependency_status": None,
                "role_state":        STATUS_DEPENDENCY_UNRESOLVED,
                "injury_tree_status": STATUS_DEPENDENCY_UNRESOLVED}

    if confirmed_at and _is_stale(confirmed_at):
        return {**base,
                "role_state":        STATUS_ROLE_STATE_STALE,
                "injury_tree_status": STATUS_ROLE_STATE_STALE}

    if raw_status in rule["support_statuses"]:
        direction_hint = rule.get("direction")
        tree_status = (STATUS_DEPENDENCY_SUPPORTS_LESS
                       if direction_hint == "LESS"
                       else STATUS_DEPENDENCY_SUPPORTS_MORE)
    elif raw_status in rule["conflict_statuses"]:
        tree_status = STATUS_DEPENDENCY_CONFLICT
    elif raw_status in rule["unresolved_statuses"]:
        tree_status = STATUS_DEPENDENCY_UNRESOLVED
    elif not raw_status:
        tree_status = STATUS_DEPENDENCY_UNRESOLVED
    else:
        tree_status = STATUS_DEPENDENCY_CLEAR

    return {**base, "role_state": tree_status, "injury_tree_status": tree_status}


def _combine_evaluations(evaluated: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Combine multiple rule evaluations into a single row-level gate result.
    Most-restrictive status wins.
    """
    if not evaluated:
        return _no_dependency_result()

    primary = max(
        evaluated,
        key=lambda e: _STATUS_PRIORITY.get(e["injury_tree_status"], 0)
    )

    # Collect distinct dependency player names for display
    all_dep_players = list(dict.fromkeys(
        e["dependency_player"] for e in evaluated
        if e.get("dependency_player") is not None
    ))
    dep_player_str = ", ".join(all_dep_players) if all_dep_players else None

    return {
        "injury_dependency_flag": True,
        "dependency_player":      dep_player_str,
        "dependency_status":      primary.get("dependency_status"),
        "role_state":             primary["injury_tree_status"],
        "role_state_timestamp":   primary.get("role_state_timestamp"),
        "role_effect_direction":  primary.get("role_effect_direction"),
        "approval_condition":     primary.get("approval_condition"),
        "injury_tree_status":     primary["injury_tree_status"],
    }


def _is_stale(confirmed_at_str: str, now: datetime | None = None) -> bool:
    """Return True if confirmed_at_str is older than STALE_THRESHOLD_HOURS."""
    try:
        dt = datetime.fromisoformat(confirmed_at_str.replace("Z", "+00:00"))
        _now = now or datetime.now(tz=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if _now.tzinfo is None:
            _now = _now.replace(tzinfo=timezone.utc)
        return (_now - dt).total_seconds() > STALE_THRESHOLD_HOURS * 3600
    except Exception:
        return False
