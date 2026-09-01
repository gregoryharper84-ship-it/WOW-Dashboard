"""WOW v16 MLB 1st-Inning Pitches Thrown ("1IP") specialist runtime.

Implements the orchestration semantics of
WOW-PATCH-2026-09-01-MLB-1IP-FULL-MODEL-GOVERNED
(docs/wow/contracts/canonical/WOW-PATCH-2026-09-01-MLB-1IP-FULL-MODEL-GOVERNED.md):
pending official-lineup confirmation is not, by itself, MODEL_UNAVAILABLE.
When the starter is confirmed and an approved projected/reconstructed top
four with sufficient batter-level inputs exists, the controlling specialist
(wow.mlb-first-inning-pitch-count-expert) still runs the batter-by-batter
event tree, with widened uncertainty and a MODEL_QUALIFIED_HOLD ceiling,
pending mandatory final refresh once the confirmed lineup posts.

The Monte Carlo event-tree simulator (simulate_1ip) is ported unchanged in
method from artifacts/flask-scoring-api/gate_engine/mlb/ip1_event_tree.py
(see .agents/memory/1ip-route-fix.md, commit 35bcfa3) and extended only to
also report the BF-conditional MORE/LESS splits and fourth-batter dependence
share the skill contract requires
(docs/wow/contracts/canonical/wow-mlb-first-inning-pitch-count-expert-SKILL-v3.md)
-- it does not re-derive or change the simulation method itself.

This module does not fabricate bf_distribution, pitches-per-batter
parameters, calibration, or lineup/batter evidence. All of those are
supplied by the caller as hydrated evidence (mirroring how
pick_request_runtime.py already accepts caller-supplied RawPropEvidence for
every stat type this repository does not auto-hydrate). Acquiring that
evidence live from Baseball Savant/MLB Stats API is a separate,
not-yet-built acquisition-layer project; this module's contract begins once
that evidence exists as input.

This module never sets probability_publishable=True and never touches
can_execute. Whether a row's computed probability may ultimately reach a
caller is still gated, unmodified, by
api_prod_market._prop_route_artifact()/wow_prop_certified_model_artifact --
no certified, promoted artifact exists yet for (MLB,
1ST_INNING_PITCHES_THROWN), so that pre-existing hard gate continues to
return MODEL_UNAVAILABLE end-to-end until one is certified through the
existing, unmodified promotion process. This module is infrastructure that
becomes reachable once that certification exists; it does not bypass it.
"""
from __future__ import annotations

import math
import random
from typing import Any, Literal

CANONICAL_STAT_TYPE = "1ST_INNING_PITCHES_THROWN"
CONTROLLING_SPECIALIST = "wow.mlb-first-inning-pitch-count-expert"
SUPPORTING_SPECIALIST = "wow.mlb-pitcher-failure-path-expert"
CAN_EXECUTE = False

MODEL_1IP_MONTE_CARLO = "1ip_monte_carlo_event_tree_v1"
_MIN_PITCHES_PER_BATTER = 3
_FOURTH_BATTER_MIN_TOTAL = _MIN_PITCHES_PER_BATTER * 4
_MIN_TRIALS = 25000

# Uncertainty widening applied to the pitches-per-batter std when the lineup
# is not officially confirmed. These are explicit, documented orchestration
# multipliers -- not a fitted/calibrated adjustment -- and are why every
# provisional-evidence row is capped at MODEL_QUALIFIED_HOLD rather than a
# higher ceiling. Values kept conservative and easy to audit; the actual
# calibrated haircut for a promoted artifact will come from that artifact's
# own certification, not from this module.
_FULL_PROJECTION_STD_WIDENING = 1.15
_PARTIAL_PROJECTION_STD_WIDENING = 1.30

LineupEvidenceState = Literal[
    "OFFICIAL_CONFIRMED",
    "PROJECTED_OR_RECONSTRUCTED",
    "INSUFFICIENT_TO_RECONSTRUCT",
]

_REQUIRED_BATTER_FIELDS = ("player", "handedness", "p_pa_vs_pitcher_profile")
_MIN_BATTERS_FULL = 4
_MIN_BATTERS_PARTIAL_SUFFICIENT = 3


def _usable_batters(projected_top_four: Any) -> list[dict[str, Any]]:
    if not isinstance(projected_top_four, list):
        return []
    usable = []
    for entry in projected_top_four:
        if not isinstance(entry, dict):
            continue
        if all(entry.get(field) not in (None, "") for field in _REQUIRED_BATTER_FIELDS):
            ppa = entry.get("p_pa_vs_pitcher_profile")
            if isinstance(ppa, (int, float)) and not isinstance(ppa, bool) and math.isfinite(float(ppa)):
                usable.append(entry)
    return usable


def classify_lineup_evidence(
    *,
    starter_status: str,
    official_lineup_status: str,
    projected_top_four: Any = None,
) -> tuple[LineupEvidenceState, str, list[str]]:
    """Classify a 1IP row's lineup evidence per the v3 skill + Sept-1 patch.

    Returns (state, completeness, reasons). completeness is only meaningful
    for PROJECTED_OR_RECONSTRUCTED ("FULL" or "PARTIAL_SUFFICIENT") and
    controls how much extra uncertainty is applied.
    """
    lineup = str(official_lineup_status or "").strip().upper()
    starter = str(starter_status or "").strip().upper()

    if lineup == "CONFIRMED":
        return "OFFICIAL_CONFIRMED", "FULL", []

    if starter != "CONFIRMED":
        return (
            "INSUFFICIENT_TO_RECONSTRUCT",
            "NONE",
            ["STARTER_NOT_CONFIRMED"],
        )

    usable = _usable_batters(projected_top_four)
    if len(usable) >= _MIN_BATTERS_FULL:
        return "PROJECTED_OR_RECONSTRUCTED", "FULL", []
    if len(usable) >= _MIN_BATTERS_PARTIAL_SUFFICIENT:
        return "PROJECTED_OR_RECONSTRUCTED", "PARTIAL_SUFFICIENT", ["PROJECTED_TOP_FOUR_PARTIAL"]

    return (
        "INSUFFICIENT_TO_RECONSTRUCT",
        "NONE",
        ["PROJECTED_TOP_FOUR_UNOBTAINABLE", f"USABLE_BATTERS={len(usable)}"],
    )


def starter_changed(captured_starter: Any, current_starter: Any) -> bool:
    """True when the pitcher recorded at evidence capture no longer matches
    the pitcher confirmed at final refresh -- a row-local SLATE_PURGE, never
    a reason to hold or reject any sibling row in the same batch."""
    a = " ".join(str(captured_starter or "").strip().casefold().split())
    b = " ".join(str(current_starter or "").strip().casefold().split())
    return bool(a) and bool(b) and a != b


def _box_muller_normal(mu: float, sigma: float) -> float:
    while True:
        u1 = random.random()
        u2 = random.random()
        if u1 > 1e-12:
            break
    mag = math.sqrt(-2.0 * math.log(u1))
    z = mag * math.cos(2.0 * math.pi * u2)
    return mu + sigma * z


def _draw_pitches_for_batter(mean: float, std: float) -> int:
    raw = _box_muller_normal(mean, std)
    return max(_MIN_PITCHES_PER_BATTER, round(raw))


def _wilson_interval(count: int, n: int, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval -- a standard, transparent, uncalibrated
    proportion interval. This is explicitly NOT a certified calibrator; see
    module docstring. Used only to report an auditable interval around the
    Monte Carlo point estimate."""
    if n <= 0:
        return 0.0, 1.0
    p = count / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt((p * (1 - p) / n) + (z * z / (4 * n * n)))) / denom
    return max(0.0, center - half), min(1.0, center + half)


def simulate_1ip_event_tree(
    *,
    bf_distribution: dict[str, Any],
    pitches_per_batter_dist: dict[str, Any],
    line_value: float,
    side: str,
    n_trials: int = _MIN_TRIALS,
) -> dict[str, Any]:
    """Batter-by-batter Monte Carlo simulation for first-inning pitches.

    Ported from ip1_event_tree.py::simulate_1ip, extended to also report the
    BF-conditional MORE splits and fourth_batter_dependence_share the v3
    skill's "Model requirements" and "Fourth-batter dependency" sections
    require, which the original TEST_ONLY-lane implementation did not need
    to surface. The core per-trial mechanics (BF multinomial draw, clipped
    Gaussian pitches-per-batter, the BF>=4 hard floor of 12 pitches) are
    unchanged.
    """
    n_trials = max(int(n_trials), _MIN_TRIALS)

    p_bf_3 = float(bf_distribution.get("p_bf_3") or 0.0)
    p_bf_4 = float(bf_distribution.get("p_bf_4") or 0.0)
    p_bf_gte5 = float(bf_distribution.get("p_bf_gte5") or 0.0)
    total = p_bf_3 + p_bf_4 + p_bf_gte5
    if total < 1e-9:
        p_bf_3 = p_bf_4 = p_bf_gte5 = 1.0 / 3.0
    else:
        p_bf_3 /= total
        p_bf_4 /= total
        p_bf_gte5 /= total

    mean = float(pitches_per_batter_dist.get("mean") or 4.2)
    std = float(pitches_per_batter_dist.get("std") or 1.1)

    counts = {
        3: {"n": 0, "more": 0, "less": 0},
        4: {"n": 0, "more": 0, "less": 0},
        5: {"n": 0, "more": 0, "less": 0},
    }
    pitches_sum = 0.0
    pitches_all: list[int] = []

    for _ in range(n_trials):
        r = random.random()
        if r < p_bf_3:
            n_batters = 3
        elif r < p_bf_3 + p_bf_4:
            n_batters = 4
        else:
            n_batters = 5
        bucket = n_batters if n_batters in counts else 5

        total_pitches = sum(_draw_pitches_for_batter(mean, std) for _ in range(n_batters))
        if n_batters >= 4:
            total_pitches = max(total_pitches, _FOURTH_BATTER_MIN_TOTAL)

        pitches_sum += total_pitches
        pitches_all.append(total_pitches)
        counts[bucket]["n"] += 1
        if total_pitches > line_value:
            counts[bucket]["more"] += 1
        elif total_pitches < line_value:
            counts[bucket]["less"] += 1

    n3, n4, n5 = counts[3]["n"], counts[4]["n"], counts[5]["n"]
    more3, more4, more5 = counts[3]["more"], counts[4]["more"], counts[5]["more"]
    less3, less4, less5 = counts[3]["less"], counts[4]["less"], counts[5]["less"]

    total_more = more3 + more4 + more5
    total_less = less3 + less4 + less5

    p_more = total_more / n_trials
    p_less = total_less / n_trials
    p_more_given_bf3 = (more3 / n3) if n3 else 0.0
    p_more_given_bf_ge4 = ((more4 + more5) / (n4 + n5)) if (n4 + n5) else 0.0
    more_and_bf_ge4 = more4 + more5
    fourth_batter_dependence_share = (more_and_bf_ge4 / total_more) if total_more else 0.0

    mean_pitches = pitches_sum / n_trials
    pitches_all.sort()
    median_pitches = float(pitches_all[n_trials // 2])
    variance = sum((p - mean_pitches) ** 2 for p in pitches_all) / n_trials
    std_pitches = math.sqrt(variance)

    side_norm = str(side or "").strip().upper()
    if side_norm == "MORE":
        lower_bound, upper_bound = _wilson_interval(total_more, n_trials)
    else:
        lower_bound, upper_bound = _wilson_interval(total_less, n_trials)

    return {
        "model_used": MODEL_1IP_MONTE_CARLO,
        "n_trials": n_trials,
        "P_BF_3": round(n3 / n_trials, 4),
        "P_BF_4": round(n4 / n_trials, 4),
        "P_BF_GE_5": round(n5 / n_trials, 4),
        "P_MORE_GIVEN_BF_3": round(p_more_given_bf3, 4),
        "P_MORE_GIVEN_BF_GE_4": round(p_more_given_bf_ge4, 4),
        "P_MORE": round(p_more, 4),
        "P_LESS": round(p_less, 4),
        "prob_push": round(max(0.0, 1.0 - p_more - p_less), 4),
        "fourth_batter_dependence_share": round(fourth_batter_dependence_share, 4),
        "projection_mean": round(mean_pitches, 2),
        "projection_median": round(median_pitches, 2),
        "projection_std": round(std_pitches, 2),
        "lower_bound": round(lower_bound, 4),
        "upper_bound": round(upper_bound, 4),
        "simulation_count": n_trials,
        "can_execute": False,
    }


def score_mlb_1ip(
    *,
    starter_status: str,
    official_lineup_status: str,
    projected_top_four: Any,
    pitcher_bf_distribution: dict[str, Any],
    baseline_pitches_per_batter: dict[str, Any],
    line_value: float,
    side: str,
    failure_path_prior: dict[str, Any] | None = None,
    market_evidence_present: bool = True,
    n_trials: int = _MIN_TRIALS,
) -> dict[str, Any]:
    """Score one MLB 1IP row under the Sept-1 lineup-evidence-state contract.

    Returns a dict with at minimum: lineup_evidence_state, model_evaluated,
    terminal_label, blockers, final_refresh_required, can_execute. When
    model_evaluated is True the full event-tree contract fields
    (P_BF_3/... per wow_1ip_contract_status) are included and market/money
    evidence gaps are reported as separate blockers without erasing them.
    """
    state, completeness, reasons = classify_lineup_evidence(
        starter_status=starter_status,
        official_lineup_status=official_lineup_status,
        projected_top_four=projected_top_four,
    )

    if state == "INSUFFICIENT_TO_RECONSTRUCT":
        return {
            "controlling_specialist": CONTROLLING_SPECIALIST,
            "lineup_evidence_state": state,
            "model_evaluated": False,
            "terminal_label": "REJECT_DATA_QUALITY",
            "code": "MANDATORY_EVENT_TREE_INPUTS_UNOBTAINABLE_AFTER_APPROVED_ATTEMPTS",
            "blockers": reasons,
            "final_refresh_required": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    if failure_path_prior is not None and failure_path_prior.get("status") == "MATERIAL_UNRESOLVED":
        pitches_per_batter = dict(baseline_pitches_per_batter)
        pitches_per_batter["std"] = float(pitches_per_batter.get("std") or 1.1) * _FULL_PROJECTION_STD_WIDENING
        failure_path_label = "PITCHER_FAILURE_PATH_PRIOR_UNRESOLVED"
    else:
        pitches_per_batter = dict(baseline_pitches_per_batter)
        failure_path_label = None

    widening = 1.0
    if state == "PROJECTED_OR_RECONSTRUCTED":
        widening = (
            _PARTIAL_PROJECTION_STD_WIDENING
            if completeness == "PARTIAL_SUFFICIENT"
            else _FULL_PROJECTION_STD_WIDENING
        )
        pitches_per_batter["std"] = float(pitches_per_batter.get("std") or 1.1) * widening

    result = simulate_1ip_event_tree(
        bf_distribution=pitcher_bf_distribution,
        pitches_per_batter_dist=pitches_per_batter,
        line_value=line_value,
        side=side,
        n_trials=n_trials,
    )

    blockers = list(reasons)
    if failure_path_label:
        blockers.append(failure_path_label)
    if not market_evidence_present:
        # Market/money evidence is a separate objective lane. Its absence is
        # reported as a blocker; it must never erase the completed sporting
        # probability computed above.
        blockers.append("MARKET_DATA_UNAVAILABLE")

    return {
        **result,
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "lineup_evidence_state": state,
        "lineup_evidence_completeness": completeness,
        "uncertainty_widening_applied": state == "PROJECTED_OR_RECONSTRUCTED",
        "uncertainty_widening_factor": widening,
        "model_evaluated": True,
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
        "final_refresh_required": state != "OFFICIAL_CONFIRMED",
        "calibration_method": "UNCALIBRATED_INTERVAL_WIDENING_V1",
        "blockers": blockers,
        "probability_publishable": False,
        "can_execute": False,
    }
