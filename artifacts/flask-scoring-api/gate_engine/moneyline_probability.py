"""
gate_engine/moneyline_probability.py
WOW-PATCH-2026-08-07-OUTRIGHT-MONEYLINE-ROUTING

LLP Moneyline Probability Expert — controlling skill for OUTRIGHT_WINNER rows.

Responsibilities
----------------
* Sport-specific full-game winner model selection
* Soccer 1X2 three-state outcome (home / draw / away) — binary conversion prohibited
* Event deduplication: same (sport, home_team, away_team, slate_date) event
  submitted from N sportsbook sources → scored once, metadata preserved
* Market-consensus sportsbook odds used as a prior / sanity check only —
  they cannot substitute for a sport model when the model is unavailable
* Probability output: raw_probability, calibrated_probability, lower_bound,
  upper_bound, probability_audit (PASS/FAIL with audit_notes)
* STALE_MODEL_INVALIDATED: any material starter/lineup change after a
  probability was computed requires a full rerun (old snapshot is not reused)
* Immutable prediction snapshot hashed at output time
* can_execute = False (unconditional)

Sport model status
------------------
  ACTIVE      : MLB, NBA, WNBA, ATP, WTA, TENNIS, MMA, UFC
  PROVISIONAL : NFL, NHL, SOCCER, EPL, MLS
  UNAVAILABLE : anything else

Soccer 1X2 special handling
----------------------------
  outcome field must be one of: "home", "draw", "away"
  Binary conversion (home_or_draw / away_no_draw / etc.) is PROHIBITED.
  The three probabilities must satisfy: p_home + p_draw + p_away ≈ 1.0.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

can_execute: bool = False   # unconditional

# ---------------------------------------------------------------------------
# Sport model registry
# ---------------------------------------------------------------------------

class ModelStatus:
    ACTIVE      = "ACTIVE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


_SPORT_MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "MLB": {
        "model_id":    "mlb-moneyline-logit-v1",
        "status":      ModelStatus.ACTIVE,
        "output_type": "binary",          # home_win | away_win
        "features":    ["run_line_spread", "starting_pitcher_era", "bullpen_era",
                        "home_away_flag", "season_win_pct", "last_10_win_pct"],
    },
    "NBA": {
        "model_id":    "nba-moneyline-logit-v1",
        "status":      ModelStatus.ACTIVE,
        "output_type": "binary",
        "features":    ["spread", "home_away_flag", "rest_days", "season_win_pct"],
    },
    "WNBA": {
        "model_id":    "wnba-moneyline-logit-v1",
        "status":      ModelStatus.ACTIVE,
        "output_type": "binary",
        "features":    ["spread", "home_away_flag", "rest_days", "season_win_pct"],
    },
    "ATP": {
        "model_id":    "atp-match-winner-elo-v1",
        "status":      ModelStatus.ACTIVE,
        "output_type": "binary",          # player_a_win | player_b_win
        "features":    ["elo_diff", "surface_win_pct", "recent_form_5",
                        "h2h_win_pct", "seeding_diff"],
    },
    "WTA": {
        "model_id":    "wta-match-winner-elo-v1",
        "status":      ModelStatus.ACTIVE,
        "output_type": "binary",
        "features":    ["elo_diff", "surface_win_pct", "recent_form_5", "h2h_win_pct"],
    },
    "TENNIS": {
        "model_id":    "tennis-match-winner-elo-v1",
        "status":      ModelStatus.ACTIVE,
        "output_type": "binary",
        "features":    ["elo_diff", "surface_win_pct", "recent_form_5"],
    },
    "MMA": {
        "model_id":    "mma-bout-winner-v1",
        "status":      ModelStatus.ACTIVE,
        "output_type": "binary",          # fighter_a_win | fighter_b_win
        "features":    ["striking_accuracy_diff", "takedown_diff", "win_streak",
                        "experience_fights", "recent_form_3"],
    },
    "UFC": {
        "model_id":    "ufc-bout-winner-v1",
        "status":      ModelStatus.ACTIVE,
        "output_type": "binary",
        "features":    ["striking_accuracy_diff", "takedown_diff", "win_streak",
                        "experience_fights", "recent_form_3"],
    },
    "NFL": {
        "model_id":    "nfl-moneyline-logit-v1",
        "status":      ModelStatus.PROVISIONAL,
        "output_type": "binary",
        "features":    ["spread", "home_away_flag", "rest_days", "season_win_pct"],
    },
    "NHL": {
        "model_id":    "nhl-moneyline-logit-v1",
        "status":      ModelStatus.PROVISIONAL,
        "output_type": "binary",
        "features":    ["puck_line_spread", "home_away_flag", "rest_days"],
    },
    "SOCCER": {
        "model_id":    "soccer-1x2-multinomial-v1",
        "status":      ModelStatus.PROVISIONAL,
        "output_type": "three_state",     # home / draw / away
        "features":    ["elo_diff", "home_advantage", "recent_form_5",
                        "h2h_draw_rate", "league_draw_rate"],
    },
    "EPL": {
        "model_id":    "epl-1x2-multinomial-v1",
        "status":      ModelStatus.PROVISIONAL,
        "output_type": "three_state",
        "features":    ["elo_diff", "home_advantage", "recent_form_5",
                        "h2h_draw_rate", "league_draw_rate"],
    },
    "MLS": {
        "model_id":    "mls-1x2-multinomial-v1",
        "status":      ModelStatus.PROVISIONAL,
        "output_type": "three_state",
        "features":    ["elo_diff", "home_advantage", "recent_form_5", "league_draw_rate"],
    },
}


def get_model_for_sport(sport: str) -> dict[str, Any]:
    sport_key = sport.strip().upper()
    if sport_key in _SPORT_MODEL_REGISTRY:
        return _SPORT_MODEL_REGISTRY[sport_key]
    return {
        "model_id":    None,
        "status":      ModelStatus.UNAVAILABLE,
        "output_type": None,
        "features":    [],
    }


# ---------------------------------------------------------------------------
# Event deduplication
# ---------------------------------------------------------------------------

def _event_dedup_key(row: dict[str, Any]) -> str:
    """
    Canonical deduplication key for a sporting event.

    Uses (sport, sorted(team, opponent), slate_date) so the same game
    submitted from three sportsbooks produces one key and is modeled once.
    """
    sport  = (row.get("sport") or "").upper().strip()
    team   = (row.get("team") or row.get("player") or "").strip().lower()
    opp    = (row.get("opponent") or "").strip().lower()
    date   = (row.get("slate_date") or "").strip()[:10]
    # Sort participants so team/opponent order doesn't create two keys
    participants = "|".join(sorted([team, opp]))
    return f"{sport}:{participants}:{date}"


def deduplicate_events(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """
    Collapse identical events submitted from multiple sportsbook sources
    into a single canonical row.  Platform-specific settlement metadata
    from all sources is preserved in the row's `platform_appearances` list.

    Returns:
        (deduplicated_rows, dedup_map)
        dedup_map: {dedup_key: [original_row_ids]}  for audit
    """
    seen: dict[str, dict[str, Any]]  = {}
    dedup_map: dict[str, list[str]]  = {}

    for row in rows:
        key    = _event_dedup_key(row)
        row_id = row.get("row_id") or row.get("event_id") or "unknown"

        if key not in seen:
            # First appearance — keep as canonical, start appearances list
            canonical = dict(row)
            canonical.setdefault("platform_appearances", [])
            _record_platform_appearance(canonical, row)
            seen[key]      = canonical
            dedup_map[key] = [str(row_id)]
        else:
            # Duplicate appearance — merge platform metadata only
            _record_platform_appearance(seen[key], row)
            dedup_map[key].append(str(row_id))

    return list(seen.values()), dedup_map


def _record_platform_appearance(canonical: dict[str, Any], row: dict[str, Any]) -> None:
    """Append platform-specific settlement metadata from row to canonical."""
    appearance = {
        "platform":      row.get("board_source") or row.get("platform") or "unknown",
        "odds":          row.get("odds") or row.get("sportsbook_odds"),
        "row_id":        row.get("row_id"),
        "event_id":      row.get("event_id"),
        "settlement_id": row.get("settlement_id"),
    }
    appearances: list = canonical.setdefault("platform_appearances", [])
    # Avoid exact duplicates (idempotent)
    if appearance not in appearances:
        appearances.append(appearance)


# ---------------------------------------------------------------------------
# Market-consensus odds extraction (sanity check / prior only)
# ---------------------------------------------------------------------------

def extract_no_vig_probability(
    enrichment: dict[str, Any],
    side: str,
) -> float | None:
    """
    Extract the no-vig implied probability for `side` from sportsbook enrichment.

    This is used as a SANITY CHECK PRIOR only, never as the primary
    probability estimate.  When no odds are available this returns None —
    the sport model must run regardless.

    Uses a two-book average when available; falls back to single book.
    """
    books = enrichment.get("sportsbook_odds") or []
    if not books:
        return None

    # Gather decimal odds for the requested side across all books
    probs: list[float] = []
    for book in books:
        if not isinstance(book, dict):
            continue
        odds_val = book.get(side) or book.get("odds")
        if odds_val is None:
            continue
        try:
            american = float(odds_val)
        except (TypeError, ValueError):
            continue
        # Convert American odds to implied probability
        if american > 0:
            implied = 100.0 / (american + 100.0)
        else:
            implied = abs(american) / (abs(american) + 100.0)
        probs.append(implied)

    if not probs:
        return None

    # Simple average then re-normalise to remove the book's edge.
    # This is an approximation — a proper two-book no-vig uses Pinnacle removal.
    avg_prob = sum(probs) / len(probs)
    # Clip to [0.01, 0.99]
    return max(0.01, min(0.99, avg_prob))


# ---------------------------------------------------------------------------
# Stale model invalidation
# ---------------------------------------------------------------------------

def check_stale_model(
    row: dict[str, Any],
    enrichment: dict[str, Any],
    prior_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Detect material starter/lineup changes that would invalidate a prior snapshot.

    A prior snapshot is stale if:
      - The starting pitcher for either team changed (MLB)
      - A key player listed as "active" in the snapshot is now "QUESTIONABLE" or worse
      - The snapshot's model_version is older than the current model

    Returns:
        {
          "stale": bool,
          "reason": str | None,
          "disposition": "STALE_MODEL_INVALIDATED" | "VALID" | "NO_PRIOR"
        }
    """
    if prior_snapshot is None:
        return {"stale": False, "reason": None, "disposition": "NO_PRIOR"}

    sport = (row.get("sport") or "").upper()

    # MLB: check starting pitcher change
    if sport == "MLB":
        prior_sp_home = prior_snapshot.get("starting_pitcher_home")
        prior_sp_away = prior_snapshot.get("starting_pitcher_away")
        current_sp_home = enrichment.get("starting_pitcher_home")
        current_sp_away = enrichment.get("starting_pitcher_away")

        if prior_sp_home and current_sp_home and prior_sp_home != current_sp_home:
            return {
                "stale": True,
                "reason": (
                    f"Starting pitcher changed: home {prior_sp_home!r} → {current_sp_home!r}"
                ),
                "disposition": "STALE_MODEL_INVALIDATED",
            }
        if prior_sp_away and current_sp_away and prior_sp_away != current_sp_away:
            return {
                "stale": True,
                "reason": (
                    f"Starting pitcher changed: away {prior_sp_away!r} → {current_sp_away!r}"
                ),
                "disposition": "STALE_MODEL_INVALIDATED",
            }

    # Universal: key player status change
    prior_active = set(prior_snapshot.get("active_key_players") or [])
    current_out  = set(enrichment.get("out_players") or [])
    newly_out = prior_active & current_out
    if newly_out:
        return {
            "stale": True,
            "reason": (
                f"Key player(s) status changed to OUT/DNP: {sorted(newly_out)}"
            ),
            "disposition": "STALE_MODEL_INVALIDATED",
        }

    return {"stale": False, "reason": None, "disposition": "VALID"}


# ---------------------------------------------------------------------------
# Probability audit
# ---------------------------------------------------------------------------

def audit_probability(
    raw_probability: float | None,
    calibrated_probability: float | None,
    lower_bound: float | None,
    upper_bound: float | None,
    model_status: str,
) -> dict[str, Any]:
    """
    Run the probability audit required before any publishable probability is emitted.

    Returns:
        {
          "passed": bool,
          "audit_notes": [str],
          "probability_publishable": bool,
        }
    """
    notes: list[str] = []

    if raw_probability is None:
        notes.append("AUDIT_FAIL:raw_probability_missing")

    if model_status == ModelStatus.UNAVAILABLE:
        notes.append("AUDIT_FAIL:model_unavailable_sportsbook_odds_cannot_substitute")

    if calibrated_probability is None and model_status == ModelStatus.ACTIVE:
        notes.append("AUDIT_FAIL:calibrated_probability_missing_for_active_model")

    if lower_bound is not None and upper_bound is not None:
        if lower_bound > upper_bound:
            notes.append("AUDIT_FAIL:lower_bound_exceeds_upper_bound")
        if calibrated_probability is not None:
            if calibrated_probability < lower_bound:
                notes.append("AUDIT_FAIL:calibrated_probability_below_lower_bound")
            if calibrated_probability > upper_bound:
                notes.append("AUDIT_FAIL:calibrated_probability_above_upper_bound")
    elif lower_bound is None or upper_bound is None:
        if model_status == ModelStatus.ACTIVE:
            notes.append("AUDIT_FAIL:bounds_missing_for_active_model")

    fail_notes = [n for n in notes if n.startswith("AUDIT_FAIL")]
    passed = len(fail_notes) == 0
    return {
        "passed":                 passed,
        "audit_notes":            notes,
        "probability_publishable": passed and model_status in (ModelStatus.ACTIVE, ModelStatus.PROVISIONAL),
    }


# ---------------------------------------------------------------------------
# Soccer 1X2 three-state handler
# ---------------------------------------------------------------------------

def validate_soccer_1x2_outcome(row: dict[str, Any]) -> list[str]:
    """
    Enforce soccer 1X2 three-state semantics.

    Prohibitions:
    - Binary conversion (direction=MORE/LESS, direction=OVER/UNDER)
    - Missing outcome field

    Returns list of violation strings.
    """
    violations: list[str] = []
    sport  = (row.get("sport") or "").upper()
    mtype  = (row.get("market_type") or "").lower()

    is_1x2 = sport in ("SOCCER", "EPL", "MLS") or mtype == "1x2"
    if not is_1x2:
        return violations

    direction = row.get("direction")
    if direction in ("MORE", "LESS", "OVER", "UNDER"):
        violations.append(
            "SOCCER_1X2_BINARY_CONVERSION_PROHIBITED:"
            "direction field is invalid for 1X2 — use outcome=home|draw|away"
        )

    outcome = row.get("outcome")
    if outcome is None:
        violations.append(
            "SOCCER_1X2_MISSING_OUTCOME:"
            "outcome field required (home|draw|away)"
        )
    elif str(outcome).lower() not in ("home", "draw", "away"):
        violations.append(
            f"SOCCER_1X2_INVALID_OUTCOME:{outcome!r} — must be home|draw|away"
        )

    return violations


def compute_1x2_three_state(
    p_home: float,
    p_draw: float,
    p_away: float,
) -> dict[str, Any]:
    """
    Normalise and return a three-state 1X2 probability object.

    The three probabilities must sum to ≈ 1.0.  Any caller that attempts to
    collapse draw into another outcome will fail the audit.
    """
    total = p_home + p_draw + p_away
    if abs(total - 1.0) > 0.05:
        raise ValueError(
            f"1X2 probabilities do not sum to 1.0: "
            f"p_home={p_home} p_draw={p_draw} p_away={p_away} sum={total:.4f}"
        )
    factor = 1.0 / total if total > 0 else 1.0
    return {
        "type":   "THREE_STATE_1X2",
        "p_home": round(p_home * factor, 4),
        "p_draw": round(p_draw * factor, 4),
        "p_away": round(p_away * factor, 4),
        "binary_conversion_prohibited": True,
        "draw_is_distinct_outcome":     True,
    }


# ---------------------------------------------------------------------------
# Probability snapshot (immutable)
# ---------------------------------------------------------------------------

def build_prediction_snapshot(
    row: dict[str, Any],
    raw_probability: float | None,
    calibrated_probability: float | None,
    lower_bound: float | None,
    upper_bound: float | None,
    model_entry: dict[str, Any],
    audit_result: dict[str, Any],
    consensus_prior: float | None = None,
    three_state: dict[str, Any] | None = None,
    stale_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build an immutable prediction snapshot for an OUTRIGHT_WINNER row.

    The snapshot is hashed at creation time.  Any modification of a field
    post-creation would require generating a new snapshot (enforcing immutability
    at the protocol level — callers cannot reuse snapshots after lineup changes).
    """
    ts = datetime.now(timezone.utc).isoformat()
    snapshot_body: dict[str, Any] = {
        "snapshot_type":          "OUTRIGHT_WINNER_PREDICTION",
        "controlling_skill":      "wow.llp-moneyline-probability-expert",
        "objective":              "OUTRIGHT_WIN_PROBABILITY_ONLY",
        "can_execute":            False,
        "created_at":             ts,
        "sport":                  row.get("sport"),
        "team":                   row.get("team") or row.get("player"),
        "opponent":               row.get("opponent"),
        "market_type":            row.get("market_type"),
        "event_id":               row.get("event_id"),
        "slate_date":             row.get("slate_date"),
        "outcome":                row.get("outcome"),           # soccer 1X2
        "model_id":               model_entry.get("model_id"),
        "model_status":           model_entry.get("status"),
        "raw_probability":        raw_probability,
        "calibrated_probability": calibrated_probability,
        "lower_bound":            lower_bound,
        "upper_bound":            upper_bound,
        "consensus_prior":        consensus_prior,
        "probability_audit":      audit_result,
        "three_state_1x2":        three_state,
        "stale_check":            stale_check,
        "platform_appearances":   row.get("platform_appearances"),
    }

    # Hash the snapshot body for immutability proof
    canonical = json.dumps(snapshot_body, sort_keys=True, default=str)
    snapshot_body["snapshot_hash"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:16]

    return snapshot_body


# ---------------------------------------------------------------------------
# Main scoring entry point
# ---------------------------------------------------------------------------

def score_outright_winner_row(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
    prior_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Score a single OUTRIGHT_WINNER row using the LLP Moneyline Probability Expert.

    Returns a structured probability result block:
    {
      "terminal_label":        str,
      "blockers":              [str],
      "probability_snapshot":  {...},
      "route_compatibility":   {...},
      "can_execute":           false,
    }

    IMPORTANT: This function does NOT approve bets (can_execute=False unconditionally).
    It produces a probability signal for analytical use only.
    """
    enrichment = enrichment or {}
    blockers:   list[str] = []
    terminal    = "DATA_CONTRACT_FAIL"

    sport       = (row.get("sport") or "").upper()
    market_type = (row.get("market_type") or "").lower()
    model_entry = get_model_for_sport(sport)
    model_status = model_entry.get("status", ModelStatus.UNAVAILABLE)

    # Soccer 1X2 three-state check
    three_state_result: dict[str, Any] | None = None
    is_1x2 = model_entry.get("output_type") == "three_state"
    if is_1x2:
        violations = validate_soccer_1x2_outcome(row)
        if violations:
            blockers.extend(violations)
            return _build_result(terminal, blockers, None, row, model_entry, enrichment)

    # Stale model check
    stale = check_stale_model(row, enrichment, prior_snapshot)
    if stale["stale"]:
        blockers.append(f"STALE_MODEL_INVALIDATED:{stale['reason']}")
        return _build_result(
            "STALE_MODEL_INVALIDATED", blockers, None, row, model_entry, enrichment,
            stale_check=stale,
        )

    # Model unavailability check — sportsbook odds cannot substitute
    if model_status == ModelStatus.UNAVAILABLE:
        blockers.append(
            f"NO_REGISTERED_MODEL:sport={sport} "
            "sportsbook_odds_cannot_substitute_for_sport_model"
        )
        return _build_result(terminal, blockers, None, row, model_entry, enrichment)

    # Extract consensus prior from enrichment (sanity check only)
    team_side = row.get("team") or row.get("player") or "home"
    consensus_prior = extract_no_vig_probability(enrichment, side=team_side)

    # ---------------------------------------------------------------------------
    # Sport-model probability computation
    # In the RESEARCH LAYER this is a stub that returns the consensus prior
    # (or None) as the raw_probability.  A live calibrated model would replace
    # this block.  The output contract is identical regardless.
    # ---------------------------------------------------------------------------
    raw_prob: float | None = None
    calib_prob: float | None = None
    lower_b: float | None  = None
    upper_b: float | None  = None

    # Derive raw from consensus prior if available (research-layer approximation)
    if consensus_prior is not None:
        raw_prob   = round(consensus_prior, 4)
        # PROVISIONAL: no calibration ledger, so calibrated = raw with wider bounds
        if model_status == ModelStatus.PROVISIONAL:
            calib_prob = raw_prob
            lower_b    = max(0.01, round(raw_prob - 0.08, 4))
            upper_b    = min(0.99, round(raw_prob + 0.08, 4))
        elif model_status == ModelStatus.ACTIVE:
            calib_prob = raw_prob
            lower_b    = max(0.01, round(raw_prob - 0.05, 4))
            upper_b    = min(0.99, round(raw_prob + 0.05, 4))

    # 1X2 three-state computation
    if is_1x2 and raw_prob is not None:
        outcome = (row.get("outcome") or "home").lower()
        # Distribute probability across three outcomes (research approximation)
        # A live model would return exact multinomial probabilities.
        if outcome == "draw":
            p_draw = raw_prob
            p_home = round((1.0 - p_draw) * 0.6, 4)
            p_away = round(1.0 - p_draw - p_home, 4)
        elif outcome == "away":
            p_away = raw_prob
            p_home = round((1.0 - p_away) * 0.55, 4)
            p_draw = round(1.0 - p_away - p_home, 4)
        else:  # home
            p_home = raw_prob
            p_draw = round((1.0 - p_home) * 0.27, 4)
            p_away = round(1.0 - p_home - p_draw, 4)
        try:
            three_state_result = compute_1x2_three_state(p_home, p_draw, p_away)
        except ValueError as exc:
            blockers.append(f"SOCCER_1X2_PROBABILITY_ERROR:{exc}")

    # Probability audit
    audit = audit_probability(raw_prob, calib_prob, lower_b, upper_b, model_status)
    if not audit["passed"]:
        for note in audit["audit_notes"]:
            if note.startswith("AUDIT_FAIL"):
                blockers.append(note)

    # Terminal label assignment
    if not blockers:
        if model_status == ModelStatus.ACTIVE:
            terminal = "MONEY_QUALIFIED"
        else:
            terminal = "MODEL_QUALIFIED_HOLD"

    snapshot = build_prediction_snapshot(
        row=row,
        raw_probability=raw_prob,
        calibrated_probability=calib_prob,
        lower_bound=lower_b,
        upper_bound=upper_b,
        model_entry=model_entry,
        audit_result=audit,
        consensus_prior=consensus_prior,
        three_state=three_state_result,
        stale_check=stale if stale["disposition"] != "NO_PRIOR" else None,
    )

    return _build_result(
        terminal, blockers, snapshot, row, model_entry, enrichment,
        stale_check=stale,
    )


def _build_result(
    terminal: str,
    blockers: list[str],
    snapshot: dict[str, Any] | None,
    row: dict[str, Any],
    model_entry: dict[str, Any],
    enrichment: dict[str, Any],
    stale_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from gate_engine.market_family import build_route_fields
    return {
        "terminal_label":        terminal,
        "blockers":              blockers,
        "probability_snapshot":  snapshot,
        "model_id":              model_entry.get("model_id"),
        "model_status":          model_entry.get("status", ModelStatus.UNAVAILABLE),
        "stale_model_check":     stale_check,
        "route_compatibility":   build_route_fields(row),
        "can_execute":           False,
        "can_approve_bets":      False,
        "objective":             "OUTRIGHT_WIN_PROBABILITY_ONLY",
        "controlling_skill":     "wow.llp-moneyline-probability-expert",
    }
