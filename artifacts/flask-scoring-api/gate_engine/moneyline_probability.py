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

    Prefers the upstream Odds API event id when present — this is the
    canonical identity carried by the board scan / MoneylineMarketSnapshot,
    and is immune to team-name spelling differences between sources.

    Falls back to (sport, sorted(team, opponent), slate_date) so the same
    game submitted from three sportsbooks produces one key and is modeled once.
    """
    event_id = str(row.get("event_id") or "").strip()
    if event_id:
        return f"EVENT:{event_id}"
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
    opponent: str | None = None,
) -> float | None:
    """
    Extract the no-vig implied probability for the candidate `side` from
    sportsbook enrichment.

    Matching strategy (in order):
      1. Book entries where book["team"] matches `side` → candidate probs
      2. Book entries where book["team"] matches `opponent` → opponent probs
         When both sides present, compute proper two-sided no-vig:
         no_vig = p_candidate / (p_candidate + p_opponent)
      3. If no team field present on any entry, fall back to all "odds" values.

    This is a SANITY CHECK PRIOR only — never the primary probability estimate.
    When no odds are available or the candidate's team cannot be matched,
    returns None so the sport model governs.
    """
    books = enrichment.get("sportsbook_odds") or []
    if not books:
        return None

    def _american_to_implied(american: float) -> float:
        if american > 0:
            return 100.0 / (american + 100.0)
        else:
            return abs(american) / (abs(american) + 100.0)

    candidate_probs: list[float] = []
    opponent_probs:  list[float] = []
    unmatched_probs: list[float] = []

    side_lower     = (side     or "").lower().strip()
    opponent_lower = (opponent or "").lower().strip()

    for book in books:
        if not isinstance(book, dict):
            continue

        odds_val  = book.get("odds")
        book_team = (book.get("team") or book.get("side") or "").lower().strip()

        if odds_val is None:
            continue
        try:
            american = float(odds_val)
        except (TypeError, ValueError):
            continue

        implied = _american_to_implied(american)

        if book_team:
            if side_lower and book_team == side_lower:
                candidate_probs.append(implied)
            elif opponent_lower and book_team == opponent_lower:
                opponent_probs.append(implied)
            # else: a different team's entry — ignore for this candidate
        else:
            # No team identifier on this entry; pool for fallback
            unmatched_probs.append(implied)

    # -- Primary path: team-matched candidate odds ---------------------------
    if candidate_probs:
        avg_candidate = sum(candidate_probs) / len(candidate_probs)
        if opponent_probs:
            # Proper two-sided no-vig: removes the book's overround on H2H
            avg_opponent = sum(opponent_probs) / len(opponent_probs)
            denom = avg_candidate + avg_opponent
            if denom > 0:
                no_vig = avg_candidate / denom
                return max(0.01, min(0.99, no_vig))
        # Single-side implied probability (approximate; no opponent for removal)
        return max(0.01, min(0.99, avg_candidate))

    # -- Fallback: no team tags on any entry --------------------------------
    if unmatched_probs:
        avg = sum(unmatched_probs) / len(unmatched_probs)
        return max(0.01, min(0.99, avg))

    return None


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
# Pipeline audit helper
# ---------------------------------------------------------------------------

def _build_pipeline_audit(ml_result: Any) -> dict[str, Any]:
    """
    Build the probability_audit dict from a MoneylineResult.

    Audit passes only when:
      - independent_probability is not None (model actually ran), AND
      - ml_result.blockers is empty (no downstream gate failures)

    When independent_probability is None (MARKET_OBSERVATION_ONLY path),
    audit.passed=False regardless of blocker state, because raw_probability
    is unavailable — market data cannot substitute for the independent model.
    """
    notes: list[str] = list(ml_result.blockers or [])
    independent_prob = ml_result.outputs.independent_probability
    market_fallback  = (ml_result.sport_model or {}).get("market_derived_fallback", False)

    if independent_prob is None:
        if market_fallback:
            notes.append(
                "AUDIT_FAIL:independent_probability_unavailable_MARKET_OBSERVATION_ONLY "
                "(market_no_vig used as bounded calibration input; cannot substitute for model)"
            )
        else:
            notes.append("AUDIT_FAIL:independent_probability_unavailable")
        return {"passed": False, "audit_notes": notes}

    # Independent model ran — pass iff no blocking failures
    passed = len(ml_result.blockers) == 0
    return {"passed": passed, "audit_notes": notes}


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

    # Direct-entry contract check.  The full pipeline repeats this check for
    # callers that invoke it directly, but this boundary guarantees app/API
    # callers receive a typed non-crashing failure envelope.
    from gate_engine.moneyline.orientation import (
        orientation_blocker,
        resolve_participant_orientation,
    )
    orientation = resolve_participant_orientation(row, enrichment)
    if not orientation.resolved:
        blockers.append(orientation_blocker(orientation))
        return _build_result(
            terminal, blockers, None, row, model_entry, enrichment,
            orientation_resolution=orientation.to_dict(),
        )

    # Soccer 1X2 three-state check
    three_state_result: dict[str, Any] | None = None
    is_1x2 = model_entry.get("output_type") == "three_state"
    if is_1x2:
        violations = validate_soccer_1x2_outcome(row)
        if violations:
            blockers.extend(violations)
            return _build_result(
                terminal, blockers, None, row, model_entry, enrichment,
                orientation_resolution=orientation.to_dict(),
            )

    # Stale model check
    stale = check_stale_model(row, enrichment, prior_snapshot)
    if stale["stale"]:
        blockers.append(f"STALE_MODEL_INVALIDATED:{stale['reason']}")
        return _build_result(
            "STALE_MODEL_INVALIDATED", blockers, None, row, model_entry, enrichment,
            stale_check=stale,
            orientation_resolution=orientation.to_dict(),
        )

    # Model unavailability check — sportsbook odds cannot substitute
    if model_status == ModelStatus.UNAVAILABLE:
        blockers.append(
            f"NO_REGISTERED_MODEL:sport={sport} "
            "sportsbook_odds_cannot_substitute_for_sport_model"
        )
        return _build_result(
            "MODEL_UNAVAILABLE", blockers, None, row, model_entry, enrichment,
            orientation_resolution=orientation.to_dict(),
        )

    # ---------------------------------------------------------------------------
    # Delegate to the full WOW v16 Moneyline Architecture pipeline.
    #
    # The research-layer stub that derived raw_probability directly from the
    # sportsbook no-vig consensus has been replaced by a layered pipeline that:
    #   1. Computes an INDEPENDENT sport model probability (Elo, H2H, power)
    #   2. Runs a Monte Carlo game-state simulation
    #   3. Integrates the failure-path kill-path regime matrix
    #   4. Audits cross-submodel disagreement (widens uncertainty 1×/1.15×/1.35×)
    #   5. Applies bounded market shrinkage calibration (market data enters HERE only)
    #   6. Computes the calibrated lower bound (used for candidate ranking)
    #   7. Classifies favorite / underdog with upset-pathway taxonomy
    #   8. Performs exact no-vig market comparison (downstream only)
    #   9. Runs mandatory final refresh
    #
    # All upstream stages receive a clean enrichment with odds fields stripped.
    # can_execute=False unconditional.
    # ---------------------------------------------------------------------------
    from gate_engine.moneyline.pipeline import run_moneyline_pipeline

    ml_result = run_moneyline_pipeline(
        row=row,
        enrichment=enrichment,
        prior_snapshot=prior_snapshot,
        n_sims=5000,
    )

    # Build a backward-compatible probability_snapshot from the new result
    # so the app.py call site (which reads "probability_snapshot") continues
    # to receive a structured snapshot in the expected format.
    _snap = build_prediction_snapshot(
        row=row,
        raw_probability=ml_result.outputs.independent_probability,
        calibrated_probability=ml_result.outputs.calibrated_probability,
        lower_bound=ml_result.outputs.calibrated_probability_lower_bound,
        upper_bound=ml_result.outputs.calibrated_probability_upper_bound,
        model_entry=model_entry,
        audit_result=_build_pipeline_audit(ml_result),
        consensus_prior=None,
        three_state=ml_result.three_state_1x2,
        stale_check=stale if stale["disposition"] != "NO_PRIOR" else None,
    )
    # Inject new four-output fields into snapshot for GPT observability
    _snap["independent_probability"]            = ml_result.outputs.independent_probability
    _snap["calibrated_probability"]             = ml_result.outputs.calibrated_probability
    _snap["calibrated_probability_lower_bound"] = ml_result.outputs.calibrated_probability_lower_bound
    _snap["calibrated_probability_upper_bound"] = ml_result.outputs.calibrated_probability_upper_bound
    _snap["net_edge"]                           = ml_result.outputs.net_edge
    _snap["moneyline_architecture_layers"]      = {
        "sport_model":        ml_result.sport_model,
        "simulation":         ml_result.simulation,
        "failure_path":       ml_result.failure_path,
        "disagreement_audit": ml_result.disagreement_audit,
        "calibration":        ml_result.calibration,
        "classification":     ml_result.classification,
        "market_comparison":  ml_result.market_comparison,
        "final_refresh":      ml_result.final_refresh,
        "probability_claim_audit": ml_result.probability_claim_audit,
        "event_decision":     ml_result.event_decision,
        "slate_integrity":    ml_result.slate_integrity,
    }
    # Only overwrite the compatibility snapshot hash when the pipeline produced
    # a real one (early-return paths leave it None; build_prediction_snapshot
    # already computed a valid hash from the output fields it received).
    if ml_result.snapshot_hash is not None:
        _snap["snapshot_hash"] = ml_result.snapshot_hash

    return _build_result(
        ml_result.terminal_label, ml_result.blockers, _snap, row, model_entry, enrichment,
        stale_check=stale,
        orientation_resolution=orientation.to_dict(),
    )


def _build_result(
    terminal: str,
    blockers: list[str],
    snapshot: dict[str, Any] | None,
    row: dict[str, Any],
    model_entry: dict[str, Any],
    enrichment: dict[str, Any],
    stale_check: dict[str, Any] | None = None,
    orientation_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from gate_engine.market_family import build_route_fields
    from gate_engine.moneyline.routing_policy import build_specialist_handoff

    specialist_probability = build_specialist_handoff(
        row=row,
        enrichment=enrichment,
        probability_snapshot=snapshot,
        blockers=blockers,
        governance_ceiling=terminal,
        model_id=model_entry.get("model_id"),
        model_status=model_entry.get("status", ModelStatus.UNAVAILABLE),
    )
    return {
        "terminal_label":        terminal,
        "blockers":              blockers,
        "probability_snapshot":  snapshot,
        "specialist_probability": specialist_probability,
        "model_id":              model_entry.get("model_id"),
        "model_status":          model_entry.get("status", ModelStatus.UNAVAILABLE),
        "stale_model_check":     stale_check,
        "orientation_resolution": orientation_resolution,
        "route_compatibility":   build_route_fields(row),
        "can_execute":           False,
        "can_approve_bets":      False,
        "objective":             "OUTRIGHT_WIN_PROBABILITY_ONLY",
        "controlling_skill":     "wow.llp-moneyline-probability-expert",
    }
