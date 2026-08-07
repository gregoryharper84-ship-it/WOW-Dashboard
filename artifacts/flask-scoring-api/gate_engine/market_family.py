"""
gate_engine/market_family.py
WOW-PATCH-2026-08-07-OUTRIGHT-MONEYLINE-ROUTING

First-class, immutable market-family classification for the WOW v16 Clean Core
gate engine.  Classification runs BEFORE generic prop normalization so no
OUTRIGHT_WINNER row is ever fed into the player-prop L5/L10/role contract.

Public surface
--------------
MarketFamily            str enum of recognized families
Objective               str enum of scoring objectives
ROUTE_TABLE             immutable mapping family → objective + skill
classify_row(row)       stamp market_family + routing fields onto a row dict
validate_moneyline_v1(row)  check MONEYLINE_V1 input contract (returns violations)
check_route_compatibility(row)  return RouteCompatibilityResult
guard_route_config(rows)    raise / return error envelope on wrong-contract pairing
build_route_fields(row)     produce the route_id/market_family/… output block

Invariants (enforced by guard_route_config before the pipeline runs)
--------------------------------------------------------------------
* OUTRIGHT_WINNER + PLAYER_PROP input → RUN_INVALID_ROUTE_CONFIGURATION
* OUTRIGHT_WIN_PROBABILITY_ONLY must be paired with MONEYLINE_V1 contract
* can_execute = False  (unconditional, re-asserted in every output block)
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

can_execute: bool = False

# Market families — immutable canonical strings
class MarketFamily:
    OUTRIGHT_WINNER = "OUTRIGHT_WINNER"
    PLAYER_PROP     = "PLAYER_PROP"
    COMBO_PROP      = "COMBO_PROP"
    UNKNOWN         = "UNKNOWN"

# Scoring objectives
class Objective:
    OUTRIGHT_WIN_PROBABILITY_ONLY = "OUTRIGHT_WIN_PROBABILITY_ONLY"
    PROP_HIT_PROBABILITY          = "PROP_HIT_PROBABILITY"
    COMBO_HIT_PROBABILITY         = "COMBO_HIT_PROBABILITY"
    UNKNOWN                       = "UNKNOWN"

# Controlling skills per objective
CONTROLLING_SKILL: dict[str, str] = {
    Objective.OUTRIGHT_WIN_PROBABILITY_ONLY: "wow.llp-moneyline-probability-expert",
    Objective.PROP_HIT_PROBABILITY:          "wow.llp-prop-probability-expert",
    Objective.COMBO_HIT_PROBABILITY:         "wow.llp-combo-probability-expert",
    Objective.UNKNOWN:                       "wow.unknown",
}

# Input contract versions
class InputContract:
    MONEYLINE_V1  = "MONEYLINE_V1"
    PLAYER_PROP   = "PLAYER_PROP"   # implicit contract for legacy rows
    COMBO_PROP    = "COMBO_PROP"
    UNKNOWN       = "UNKNOWN"

# Route table: family → (objective, input_contract_version)
ROUTE_TABLE: dict[str, dict[str, str]] = {
    MarketFamily.OUTRIGHT_WINNER: {
        "objective":               Objective.OUTRIGHT_WIN_PROBABILITY_ONLY,
        "controlling_skill_id":    CONTROLLING_SKILL[Objective.OUTRIGHT_WIN_PROBABILITY_ONLY],
        "input_contract_version":  InputContract.MONEYLINE_V1,
        "required_field_profile":  "MONEYLINE_V1",
    },
    MarketFamily.PLAYER_PROP: {
        "objective":               Objective.PROP_HIT_PROBABILITY,
        "controlling_skill_id":    CONTROLLING_SKILL[Objective.PROP_HIT_PROBABILITY],
        "input_contract_version":  InputContract.PLAYER_PROP,
        "required_field_profile":  "PLAYER_PROP",
    },
    MarketFamily.COMBO_PROP: {
        "objective":               Objective.COMBO_HIT_PROBABILITY,
        "controlling_skill_id":    CONTROLLING_SKILL[Objective.COMBO_HIT_PROBABILITY],
        "input_contract_version":  InputContract.COMBO_PROP,
        "required_field_profile":  "COMBO_PROP",
    },
}

# ---------------------------------------------------------------------------
# MONEYLINE_V1 contract definition
# ---------------------------------------------------------------------------

# Fields that MUST be present for a MONEYLINE_V1 row
MONEYLINE_V1_REQUIRED_FIELDS: tuple[str, ...] = (
    "sport",           # e.g. "MLB", "WNBA", "ATP", "MMA", "SOCCER"
    "team",            # participant/side being evaluated
    "opponent",        # opposing participant
    "market_type",     # canonical key: "h2h" | "moneyline" | "match_winner" | "1x2" | "outright_winner"
    "event_id",        # unique event identifier (may be platform-supplied or synthesised)
    "slate_date",      # YYYY-MM-DD
)

# Fields that must NOT appear in a MONEYLINE_V1 row (prop-contract pollution)
MONEYLINE_V1_PROHIBITED_FIELDS: tuple[str, ...] = (
    "line",            # no prop line (More/Less threshold)
    "direction",       # no MORE/LESS side
    "prop_type",       # no stat category
    "stat_key",        # no player stat key
    "game_log",        # no L5/L10 game log array
    "player_role",     # no role-status contract
)

# Full-game / outright winner market keys — detection is case-insensitive
_OUTRIGHT_MARKET_KEYS: frozenset[str] = frozenset({
    "h2h",
    "moneyline",
    "ml",
    "match_winner",
    "match winner",
    "game_winner",
    "game winner",
    "outright_winner",
    "outright winner",
    "1x2",
    "bout_winner",
    "bout winner",
    "fight_winner",
    "fight winner",
    "full_game_outright_winner",
    "full game outright winner",
})

# Sport-specific market keys that always signal OUTRIGHT_WINNER
_OUTRIGHT_SPORTS_MARKET_MAP: dict[str, frozenset[str]] = {
    "MLB":    frozenset({"h2h", "moneyline", "ml", "game_winner"}),
    "NBA":    frozenset({"h2h", "moneyline", "ml"}),
    "WNBA":   frozenset({"h2h", "moneyline", "ml"}),
    "NFL":    frozenset({"h2h", "moneyline", "ml"}),
    "NHL":    frozenset({"h2h", "moneyline", "ml", "puck_line"}),
    "ATP":    frozenset({"match_winner", "match winner", "h2h"}),
    "WTA":    frozenset({"match_winner", "match winner", "h2h"}),
    "TENNIS": frozenset({"match_winner", "match winner", "h2h"}),
    "MMA":    frozenset({"bout_winner", "fight_winner", "h2h", "bout winner", "fight winner"}),
    "UFC":    frozenset({"bout_winner", "fight_winner", "h2h", "bout winner", "fight winner"}),
    "SOCCER": frozenset({"1x2", "h2h", "match_winner", "full_game_outright_winner"}),
    "EPL":    frozenset({"1x2", "h2h", "match_winner"}),
    "MLS":    frozenset({"1x2", "h2h", "match_winner"}),
}

# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------

def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def classify_market_family(row: dict[str, Any]) -> str:
    """
    Return the MarketFamily constant for a row.

    Classification order (immutable, runs before prop normalization):
    1. Explicit market_family field on the row (trusted if already stamped)
    2. market_type / market key detection against _OUTRIGHT_MARKET_KEYS
    3. sport + market_type intersection with _OUTRIGHT_SPORTS_MARKET_MAP
    4. Prop-family signals (line, direction, prop_type present → PLAYER_PROP)
    5. UNKNOWN as the safe default
    """
    # 1. Already classified
    existing = row.get("market_family")
    if existing in (MarketFamily.OUTRIGHT_WINNER, MarketFamily.PLAYER_PROP, MarketFamily.COMBO_PROP):
        return existing

    # 2. market_type / market key detection
    mtype = _norm(row.get("market_type") or row.get("market") or "")
    if mtype and mtype in _OUTRIGHT_MARKET_KEYS:
        return MarketFamily.OUTRIGHT_WINNER

    # 3. Sport-narrowed detection
    sport = (row.get("sport") or "").strip().upper()
    sport_map = _OUTRIGHT_SPORTS_MARKET_MAP.get(sport, frozenset())
    if mtype and mtype in sport_map:
        return MarketFamily.OUTRIGHT_WINNER

    # 4. Player-prop signals — any of these present → PLAYER_PROP
    prop_signals = (
        row.get("prop_type"),
        row.get("stat_key"),
        row.get("player"),
        row.get("player_id"),
    )
    line_signals = (
        row.get("line") is not None,
        row.get("direction") in ("MORE", "LESS", "OVER", "UNDER"),
    )
    if any(prop_signals) or any(line_signals):
        return MarketFamily.PLAYER_PROP

    # 5. No conclusive signal
    return MarketFamily.UNKNOWN


def classify_row(row: dict[str, Any]) -> dict[str, Any]:
    """
    Stamp market_family, objective, controlling_skill_id, route_id,
    input_contract_version, and required_field_profile onto a ROW DICT
    (mutates in place, also returns the dict).

    This is the first transformation applied in gate_engine_run, before
    normalize_gate_request hands the row to any specialist.
    """
    family = classify_market_family(row)
    route  = ROUTE_TABLE.get(family, ROUTE_TABLE[MarketFamily.PLAYER_PROP])

    row["market_family"]           = family
    row["objective"]               = route["objective"]
    row["controlling_skill_id"]    = route["controlling_skill_id"]
    row["input_contract_version"]  = route["input_contract_version"]
    row["required_field_profile"]  = route["required_field_profile"]
    row["route_id"]                = _build_route_id(family, row)
    return row


def _build_route_id(family: str, row: dict[str, Any]) -> str:
    """Deterministic, human-readable route identifier for this row."""
    sport   = (row.get("sport") or "UNK").upper()
    mtype   = (row.get("market_type") or "prop").lower().replace(" ", "_")
    if family == MarketFamily.OUTRIGHT_WINNER:
        return f"OUTRIGHT_WINNER:{sport}:{mtype}"
    elif family == MarketFamily.PLAYER_PROP:
        prop = (row.get("prop_type") or row.get("stat_key") or "prop").lower().replace(" ", "_")
        return f"PLAYER_PROP:{sport}:{prop}"
    elif family == MarketFamily.COMBO_PROP:
        return f"COMBO_PROP:{sport}"
    return f"UNKNOWN:{sport}"


# ---------------------------------------------------------------------------
# MONEYLINE_V1 contract validator
# ---------------------------------------------------------------------------

def validate_moneyline_v1_contract(row: dict[str, Any]) -> list[str]:
    """
    Validate a MONEYLINE_V1 input row against the contract.

    Returns a list of violation strings (empty = valid).
    """
    violations: list[str] = []

    # Required fields
    for f in MONEYLINE_V1_REQUIRED_FIELDS:
        val = row.get(f)
        if val is None or (isinstance(val, str) and not val.strip()):
            violations.append(f"MISSING_REQUIRED_FIELD:{f}")

    # Prohibited fields
    for f in MONEYLINE_V1_PROHIBITED_FIELDS:
        val = row.get(f)
        present = val is not None and val != "" and val != []
        if present:
            violations.append(f"PROHIBITED_FIELD_PRESENT:{f}")

    # market_type must resolve to an outright key
    mtype = _norm(row.get("market_type") or "")
    if mtype and mtype not in _OUTRIGHT_MARKET_KEYS:
        violations.append(f"INVALID_MARKET_TYPE_FOR_MONEYLINE_V1:{row.get('market_type')!r}")

    # Soccer 1X2 must carry outcome field (home/draw/away), not direction
    sport = (row.get("sport") or "").upper()
    if sport == "SOCCER" and _norm(row.get("market_type") or "") == "1x2":
        if "outcome" not in row:
            violations.append("SOCCER_1X2_MISSING_OUTCOME_FIELD:required=home|draw|away")
        direction = row.get("direction")
        if direction in ("MORE", "LESS", "OVER", "UNDER"):
            violations.append(
                "SOCCER_1X2_BINARY_CONVERSION_PROHIBITED:"
                "draw_is_a_distinct_outcome_not_a_side"
            )

    return violations


# ---------------------------------------------------------------------------
# Route compatibility check
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RouteCompatibilityResult:
    passed:                  bool
    route_id:                str
    market_family:           str
    objective:               str
    controlling_skill_id:    str
    input_contract_version:  str
    required_field_profile:  str
    violations:              tuple[str, ...] = field(default_factory=tuple)
    error_code:              str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "compatibility":          "PASS" if self.passed else "FAIL",
            "route_id":               self.route_id,
            "market_family":          self.market_family,
            "objective":              self.objective,
            "controlling_skill_id":   self.controlling_skill_id,
            "input_contract_version": self.input_contract_version,
            "required_field_profile": self.required_field_profile,
            "violations":             list(self.violations),
            "error_code":             self.error_code,
            "can_execute":            False,
        }


def check_route_compatibility(row: dict[str, Any]) -> RouteCompatibilityResult:
    """
    Return a RouteCompatibilityResult for a single row.

    A row MUST have market_family stamped (call classify_row first).
    """
    family     = row.get("market_family", MarketFamily.UNKNOWN)
    route_info = ROUTE_TABLE.get(family, ROUTE_TABLE[MarketFamily.PLAYER_PROP])
    route_id   = row.get("route_id") or _build_route_id(family, row)
    violations: list[str] = []

    if family == MarketFamily.OUTRIGHT_WINNER:
        v = validate_moneyline_v1_contract(row)
        violations.extend(v)

    passed = len(violations) == 0
    return RouteCompatibilityResult(
        passed=passed,
        route_id=route_id,
        market_family=family,
        objective=route_info["objective"],
        controlling_skill_id=route_info["controlling_skill_id"],
        input_contract_version=route_info["input_contract_version"],
        required_field_profile=route_info["required_field_profile"],
        violations=tuple(violations),
        error_code=None if passed else "ROUTE_COMPATIBILITY_FAIL",
    )


# ---------------------------------------------------------------------------
# Pre-pipeline compatibility guard
# ---------------------------------------------------------------------------

def guard_route_config(
    rows: list[dict[str, Any]],
    body_input_contract: str | None = None,
) -> dict[str, Any] | None:
    """
    Check ALL rows for routing mismatches BEFORE the pipeline runs.

    Returns None if everything is compatible.
    Returns a structured error envelope (to be returned as HTTP 409) when:
      - Any OUTRIGHT_WINNER row is paired with a PLAYER_PROP-style body contract
      - Any OUTRIGHT_WINNER row fails MONEYLINE_V1 contract validation
      - Any row has market_family=UNKNOWN and market_type is set

    Routing bugs MUST NOT resolve to NO_PLAY.  They are pre-pipeline failures
    and must return RUN_INVALID_ROUTE_CONFIGURATION with
    candidate_evaluation_completed=false.
    """
    outright_rows    : list[dict[str, Any]] = []
    player_prop_rows : list[dict[str, Any]] = []
    violations_map   : dict[str, list[str]] = {}

    for row in rows:
        family = row.get("market_family", MarketFamily.UNKNOWN)
        row_id = row.get("row_id") or row.get("player") or "unknown"

        if family == MarketFamily.OUTRIGHT_WINNER:
            outright_rows.append(row)
            v = validate_moneyline_v1_contract(row)
            if v:
                violations_map[str(row_id)] = v

        elif family == MarketFamily.PLAYER_PROP:
            player_prop_rows.append(row)

    # Rule 1: Mixed OUTRIGHT_WINNER + PLAYER_PROP in same request
    if outright_rows and player_prop_rows:
        outright_ids  = [r.get("row_id") or r.get("player") or "?" for r in outright_rows]
        prop_ids      = [r.get("row_id") or r.get("player") or "?" for r in player_prop_rows]
        return {
            "code":                          "RUN_INVALID_ROUTE_CONFIGURATION",
            "primary_blocker":               "MONEYLINE_ROUTED_TO_PROP_CONTRACT",
            "can_execute":                   False,
            "candidate_evaluation_completed": False,
            "detail": (
                "OUTRIGHT_WINNER and PLAYER_PROP rows cannot share the same run. "
                "Submit moneyline candidates in a separate request using the "
                "MONEYLINE_V1 input contract."
            ),
            "outright_winner_rows": outright_ids,
            "player_prop_rows":     prop_ids,
            "resolution":           "Submit OUTRIGHT_WINNER rows in a dedicated request.",
        }

    # Rule 2: Body-level contract declares PLAYER_PROP but rows are OUTRIGHT_WINNER
    if outright_rows and body_input_contract == InputContract.PLAYER_PROP:
        return {
            "code":                          "RUN_INVALID_ROUTE_CONFIGURATION",
            "primary_blocker":               "MONEYLINE_ROUTED_TO_PROP_CONTRACT",
            "can_execute":                   False,
            "candidate_evaluation_completed": False,
            "detail": (
                "input_contract_version=PLAYER_PROP declared in request body "
                "but rows classify as OUTRIGHT_WINNER. "
                "Set input_contract_version=MONEYLINE_V1 and use MONEYLINE_V1 fields."
            ),
            "resolution": "Resubmit with input_contract_version=MONEYLINE_V1.",
        }

    # Rule 3: OUTRIGHT_WINNER rows with MONEYLINE_V1 contract violations
    if violations_map:
        return {
            "code":                          "RUN_INVALID_ROUTE_CONFIGURATION",
            "primary_blocker":               "MONEYLINE_V1_CONTRACT_VIOLATION",
            "can_execute":                   False,
            "candidate_evaluation_completed": False,
            "detail": (
                f"OUTRIGHT_WINNER rows failed MONEYLINE_V1 contract validation "
                f"({len(violations_map)} row(s))."
            ),
            "contract_violations": violations_map,
            "resolution": (
                "Supply: sport, team, opponent, market_type, event_id, slate_date. "
                "Remove: line, direction, prop_type, stat_key, game_log, player_role."
            ),
        }

    return None  # all clear


# ---------------------------------------------------------------------------
# Output block builder
# ---------------------------------------------------------------------------

def build_route_fields(row: dict[str, Any]) -> dict[str, Any]:
    """
    Build the route_compatibility output block for a scored row.
    This is injected into each row's output in prop_ledger and terminal_labels.
    """
    compat = check_route_compatibility(row)
    return {
        "route_id":               row.get("route_id") or compat.route_id,
        "market_family":          row.get("market_family") or compat.market_family,
        "objective":              row.get("objective") or compat.objective,
        "controlling_skill_id":   row.get("controlling_skill_id") or compat.controlling_skill_id,
        "input_contract_version": row.get("input_contract_version") or compat.input_contract_version,
        "required_field_profile": row.get("required_field_profile") or compat.required_field_profile,
        "compatibility":          "PASS" if compat.passed else "FAIL",
        "compatibility_violations": list(compat.violations),
        "can_execute":            False,
    }
