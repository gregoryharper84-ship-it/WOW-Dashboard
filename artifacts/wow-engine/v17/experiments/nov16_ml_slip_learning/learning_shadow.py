"""WOW V17 November 16 lessons: additive, offline research diagnostics.

No fitted model is trained, calibrated, modified, or served here. Team-event
scenario distributions are *inputs* owned by LLP_TEAM_BETTING_ENGINE. Nothing
may certify itself, mutate an existing prediction, publish, or trade.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import isfinite, log
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
AUTOMATIC_PROMOTION = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
RESEARCH_ONLY = True


def _out(status: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "research_only": RESEARCH_ONLY,
            "can_execute": CAN_EXECUTE, "automatic_promotion": AUTOMATIC_PROMOTION,
            "terminal_authority": TERMINAL_AUTHORITY,
            "production_publication_allowed": False,
            "probability_mutated": False, **extra}


def _num(x: Any, minimum: float | None = None, maximum: float | None = None) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    value = float(x)
    if not isfinite(value) or (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        return None
    return value


def _utc(x: Any) -> datetime | None:
    if not isinstance(x, str):
        return None
    try:
        stamp = datetime.fromisoformat(x.replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except ValueError:
        return None


def _identity(r: Mapping[str, Any]) -> tuple[str, str] | None:
    sport = r.get("sport")
    event = r.get("official_event_id")
    if not isinstance(sport, str) or not sport.strip() or not isinstance(event, str) or not event.strip():
        return None
    return sport.strip().upper(), event.strip()


def _pregame(r: Mapping[str, Any], *, cutoff_key: str = "snapshot_at_utc") -> bool:
    stamp, start = _utc(r.get(cutoff_key)), _utc(r.get("event_start_utc"))
    return stamp is not None and start is not None and stamp < start


def score_prop_projection(pregame: Mapping[str, Any], settlement: Mapping[str, Any]) -> dict[str, Any]:
    """Grade binary outcome AND continuous count-projection error separately.

    Minimum-clearing-count observations are *not* evidence that the forecast
    median was near the line. Missing projection yields no MAE, not zero MAE.
    """
    base = _out("UNVERIFIED_PROP_GRADE", hit=None, projection_absolute_error=None,
                projection_signed_error=None, margin_vs_line=None,
                discrete_increments_above=None, predictive_crps=None, distribution_status="NOT_SUPPLIED")
    if (_identity(pregame) is None or _identity(pregame) != _identity(settlement)
            or not _pregame(pregame)
            or not str(pregame.get("receipt_id") or "").strip()
            or not str(pregame.get("market_id") or "").strip()
            or not str(pregame.get("source_snapshot_hash") or "").strip()
            or pregame.get("market_id") != settlement.get("market_id")
            or settlement.get("source_type") != "INDEPENDENT_OFFICIAL"
            or not str(settlement.get("source_id") or "").strip()):
        return base
    start, finalized = _utc(pregame.get("event_start_utc")), _utc(settlement.get("finalized_at_utc"))
    actual, line = settlement.get("actual_count"), _num(pregame.get("exact_line"), 0)
    if (start is None or finalized is None or finalized < start
            or type(actual) is not int or actual < 0 or line is None
            or str(pregame.get("side") or "").upper() not in {"OVER", "UNDER"}):
        return base
    side = pregame["side"].upper()
    hit = (actual > line) if side == "OVER" else (actual < line)
    push = actual == line
    projection = pregame.get("projected_mean")
    projected = _num(projection, 0) if projection is not None else None
    increment = int(actual - (int(line) + 1)) if side == "OVER" and actual > line else None
    pmf = pregame.get("predictive_count_pmf")
    crps, distribution_status = None, "NOT_SUPPLIED"
    if pmf is not None:
        distribution_status = "INVALID_DISTRIBUTION"
        if isinstance(pmf, (list, tuple)) and 0 < len(pmf) <= 10001:
            probs = [_num(p, 0, 1) for p in pmf]
            if all(p is not None for p in probs) and abs(sum(probs) - 1) <= 1e-6:
                cumulative = 0.0
                crps = 0.0
                for k, prob in enumerate(probs):
                    cumulative += prob
                    crps += (cumulative - float(k >= actual)) ** 2
                if actual >= len(probs):
                    crps += actual - len(probs)
                distribution_status = "SCORED_DISTRIBUTION"
    return _out("GRADED_RESEARCH_ONLY", hit=None if push else hit, push=push,
                actual_count=actual, exact_line=line, margin_vs_line=actual - line,
                discrete_increments_above=increment,
                projection_absolute_error=abs(projected - actual) if projected is not None else None,
                projection_signed_error=projected - actual if projected is not None else None,
                receipt_id=pregame["receipt_id"], settlement_source_id=settlement["source_id"],
                near_threshold=(increment == 0 if increment is not None else False),
                near_threshold_implies_median=False,
                predictive_crps=crps, distribution_status=distribution_status)


def conditional_usage_shadow(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Consume, never infer, fitted/recorded score-state regime probabilities.

    Every state supplies an externally fitted expected count, variance, and
    exact-line P(over); we do NOT infer a state distribution from the moneyline
    nor invent Poisson tails from the mean. Returns research mixture moments.
    """
    missing = _out("SCENARIO_EVIDENCE_INSUFFICIENT", expected_count=None,
                   variance=None, p_over=None, trailing_expected_count=None,
                   leading_expected_count=None)
    if (_identity(snapshot) is None or not _pregame(snapshot)
            or str(snapshot.get("team_event_scenario_sport") or "").upper() != _identity(snapshot)[0]
            or str(snapshot.get("team_event_scenario_official_event_id") or "") != _identity(snapshot)[1]
            or not snapshot.get("team_event_scenario_receipt_id")
            or not snapshot.get("team_event_model_artifact_hash")
            or not snapshot.get("prop_fit_artifact_hash")
            or not snapshot.get("prop_source_snapshot_hash")
            or _utc(snapshot.get("team_event_scenario_at_utc")) is None
            or _utc(snapshot["team_event_scenario_at_utc"]) > _utc(snapshot["snapshot_at_utc"])
            or _utc(snapshot.get("prop_fit_cutoff_utc")) is None
            or _utc(snapshot["prop_fit_cutoff_utc"]) > _utc(snapshot["snapshot_at_utc"])):
        return missing
    states = snapshot.get("score_states")
    if not isinstance(states, (list, tuple)) or not states:
        return missing
    means: dict[str, float] = {}
    weights, components = [], []
    for state in states:
        if not isinstance(state, Mapping) or state.get("state") not in {"LEADING", "TIED", "TRAILING"}:
            return missing
        label = state["state"]
        w, mean = _num(state.get("weight"), 0, 1), _num(state.get("expected_count"), 0)
        var, over = _num(state.get("count_variance"), 0), _num(state.get("p_over_exact_line"), 0, 1)
        if label in means or None in (w, mean, var, over):
            return missing
        means[label] = mean
        weights.append(w)
        components.append((w, mean, var, over))
    if set(means) != {"LEADING", "TIED", "TRAILING"} or abs(sum(weights) - 1.0) > 1e-6:
        return missing
    avg = sum(w * mean for w, mean, _, _ in components)
    variance = sum(w * (var + (mean - avg) ** 2) for w, mean, var, _ in components)
    return _out("CONDITIONAL_USAGE_RESEARCH_ONLY", expected_count=avg, variance=variance,
                p_over=sum(w * p for w, _, _, p in components),
                trailing_expected_count=means["TRAILING"],
                leading_expected_count=means["LEADING"],
                state_weight_sum=sum(weights),
                ranking_or_publication_eligible=False,
                game_script_source="LLP_TEAM_EVENT_SCENARIO_INPUT_NOT_ML_PROXY")


def revision_regret_shadow(original: Mapping[str, Any], revised: Mapping[str, Any],
                           official_outcomes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Pair immutable before/after decisions; NEVER use finals to revise picks.

    Brier comparisons need both pregame probabilities AND independently settled
    outcomes. Replacing one player/event with another is candidate-selection
    regret, not a calibrated within-event probability improvement.
    """
    bad = _out("REVISION_UNVERIFIED", original_brier=None, revised_brier=None,
               brier_regret=None, lower_bound_delta=None)
    if (not original.get("receipt_id") or not revised.get("receipt_id")
            or original["receipt_id"] == revised["receipt_id"]
            or not original.get("slot_id") or original.get("slot_id") != revised.get("slot_id")
            or not _pregame(original, cutoff_key="forecast_at_utc")
            or not _pregame(revised, cutoff_key="forecast_at_utc")
            or _utc(revised["forecast_at_utc"]) <= _utc(original["forecast_at_utc"])):
        return bad
    if (not revised.get("new_evidence_source_id") or not revised.get("revision_reason")
            or _utc(revised.get("new_evidence_at_utc")) is None
            or not (_utc(original["forecast_at_utc"]) < _utc(revised["new_evidence_at_utc"])
                    <= _utc(revised["forecast_at_utc"]))):
        return {**bad, "status": "REVISION_EVIDENCE_NOT_DOCUMENTED"}
    for rec in (original, revised):
        p, lb = _num(rec.get("predicted_probability"), 0, 1), _num(rec.get("calibrated_lower_bound"), 0, 1)
        if p is None or (lb is not None and lb > p) or not _identity(rec):
            return bad
    comparisons = []
    for rec in (original, revised):
        outcome = official_outcomes.get(str(rec.get("receipt_id")), {})
        final_at = _utc(outcome.get("finalized_at_utc"))
        if (outcome.get("source_type") != "INDEPENDENT_OFFICIAL"
                or not outcome.get("source_id")
                or final_at is None or final_at < _utc(rec["event_start_utc"])
                or type(outcome.get("hit")) is not bool):
            return {**bad, "status": "SETTLEMENT_UNVERIFIED"}
        comparisons.append((rec["predicted_probability"] - int(outcome["hit"])) ** 2)
    lb0 = _num(original.get("calibrated_lower_bound"), 0, 1)
    lb1 = _num(revised.get("calibrated_lower_bound"), 0, 1)
    same_target = (original.get("proposition_id") == revised.get("proposition_id")
                   and original.get("market_family") == revised.get("market_family")
                   and original.get("exact_line") == revised.get("exact_line")
                   and _identity(original) == _identity(revised))
    return _out("PAIRED_REVISION_DIAGNOSTIC", original_brier=comparisons[0],
                revised_brier=comparisons[1], brier_regret=comparisons[1] - comparisons[0],
                lower_bound_delta=lb1 - lb0 if same_target and lb0 is not None and lb1 is not None else None,
                same_target=same_target,
                comparison_basis="SAME_PROPOSITION" if same_target else "CANDIDATE_SELECTION_DIFFERENT_PROPOSITIONS",
                automatic_revision_lock=False,
                original_receipt_id=original["receipt_id"], revised_receipt_id=revised["receipt_id"])


def slip_exposure_shadow(slips: Sequence[Mapping[str, Any]], *, soft_penalty: float = 0.2) -> dict[str, Any]:
    """Research-only cross-slip exposure; no hard maximum or fabricated joint P.

    `priority_score` is an external RESEARCH RANKING score, not a probability.
    `event` and `thesis_key` are independent concentration diagnostics.
    Existing same-event cross-market certification holds remain controlling.
    """
    bad = _out("SLIP_INPUTS_INVALID", slips={}, event_reuse={}, thesis_reuse={})
    rate = _num(soft_penalty, 0, 1)
    if rate is None or not slips:
        return bad
    seen: set[str] = set()
    per_slip: dict[str, dict[str, Any]] = {}
    event_slips: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    thesis_slips: defaultdict[str, set[str]] = defaultdict(set)
    for slip in slips:
        if not isinstance(slip, Mapping):
            return bad
        slip_id = str(slip.get("slip_id") or "").strip()
        score = _num(slip.get("priority_score"), 0, 1)
        legs = slip.get("legs")
        if not slip_id or slip_id in seen or score is None or not isinstance(legs, (list, tuple)) or not legs:
            return bad
        seen.add(slip_id)
        events, theses, ids = set(), set(), set()
        for leg in legs:
            if not isinstance(leg, Mapping):
                return bad
            event = _identity(leg)
            leg_id = str(leg.get("leg_id") or "").strip()
            thesis = str(leg.get("thesis_key") or "").strip()
            if event is None or not leg_id or leg_id in ids:
                return bad
            ids.add(leg_id)
            events.add(event)
            if thesis:
                theses.add(thesis)
        for event in events:
            event_slips[event].add(slip_id)
        for thesis in theses:
            thesis_slips[thesis].add(slip_id)
        per_slip[slip_id] = {"events": events, "theses": theses, "priority_score": score,
                             "same_event_cross_market_correlation": None,
                             "joint_probability": None}
    enriched = {}
    n_slips = len(per_slip)
    for sid, detail in per_slip.items():
        others = set()
        for event in detail["events"]:
            others |= event_slips[event] - {sid}
        for thesis in detail["theses"]:
            others |= thesis_slips[thesis] - {sid}
        concentration = len(others) / max(1, n_slips - 1)
        enriched[sid] = {"priority_score": detail["priority_score"],
                         "soft_adjusted_priority_score": detail["priority_score"] * (1 - rate * concentration),
                         "shared_with_slips": sorted(others), "overlap_fraction": concentration,
                         "same_event_cross_market_correlation": None,
                         "joint_probability": None,
                         "exposure_diagnostic_only": True}
    concentration_warnings = sorted(f"{s}:{e}" for (s, e), ids in event_slips.items()
                                    if len(ids) / n_slips > 0.50)
    return _out("EXPOSURE_DIAGNOSTIC_ONLY", slips=enriched,
                concentration_warnings=concentration_warnings,
                event_reuse={f"{s}:{e}": len(v) for (s, e), v in event_slips.items() if len(v) > 1},
                thesis_reuse={k: len(v) for k, v in thesis_slips.items() if len(v) > 1},
                hard_exposure_limit_applied=False, portfolio_eligible_for_publication=False)


def chronological_holdout_shadow(rows: Sequence[Mapping[str, Any]], *,
                                  train_end_utc: str, calibration_end_utc: str,
                                  holdout_start_utc: str, holdout_end_utc: str) -> dict[str, Any]:
    """Paired baseline/challenger assessment on independent untouched holdout.

    Does not fit, tune, backdate, approve, or promote models. Caller must bring
    authentic frozen forecasts and independently sourced official outcomes.
    """
    bad = _out("HOLDOUT_INPUTS_INVALID", sample_size=0, baseline=None, challenger=None)
    train, cal, start, end = map(_utc, (train_end_utc, calibration_end_utc,
                                      holdout_start_utc, holdout_end_utc))
    if None in (train, cal, start, end) or not (train < cal < start <= end) or not rows:
        return bad
    seen = set()
    base_brier, next_brier, base_ll, next_ll, gap0, gap1 = ([] for _ in range(6))
    prop_errors0, prop_errors1 = [], []
    for r in rows:
        identity = _identity(r)
        event, pred, fit, calibrator = (_utc(r.get(k)) for k in (
            "event_start_utc", "forecast_at_utc", "model_fit_cutoff_utc", "calibration_fit_cutoff_utc"))
        finalized = _utc(r.get("finalized_at_utc"))
        key = (identity, r.get("proposition_id"), r.get("exact_line"))
        if (identity is None or key in seen or not r.get("immutable_forecast_receipt_id")
                or not r.get("source_snapshot_hash")
                or not r.get("baseline_model_hash") or not r.get("challenger_model_hash")
                or not r.get("calibration_receipt_id")
                or r.get("settlement_source_type") != "INDEPENDENT_OFFICIAL"
                or not r.get("settlement_source_id")
                or None in (event, pred, fit, calibrator, finalized)
                or not (fit <= train and calibrator <= cal and start <= event <= end
                        and cal <= pred < event and finalized >= event)
                or type(r.get("outcome")) is not bool):
            return bad
        seen.add(key)
        p0, p1 = _num(r.get("baseline_probability"), 0, 1), _num(r.get("challenger_probability"), 0, 1)
        lb0, lb1 = _num(r.get("baseline_lower_bound"), 0, 1), _num(r.get("challenger_lower_bound"), 0, 1)
        if None in (p0, p1, lb0, lb1) or lb0 > p0 or lb1 > p1:
            return bad
        y = int(r["outcome"])
        base_brier.append((p0 - y) ** 2)
        next_brier.append((p1 - y) ** 2)
        # Score mathematical extreme forecasts using a documented numerical floor.
        eps = 1e-12
        base_ll.append(-(y * log(max(p0, eps)) + (1-y) * log(max(1-p0, eps))))
        next_ll.append(-(y * log(max(p1, eps)) + (1-y) * log(max(1-p1, eps))))
        gap0.append(y-lb0)
        gap1.append(y-lb1)
        actual = r.get("actual_count")
        m0, m1 = r.get("baseline_projected_mean"), r.get("challenger_projected_mean")
        if any(v is not None for v in (actual, m0, m1)):
            a, x, z = _num(actual, 0), _num(m0, 0), _num(m1, 0)
            if None in (a, x, z) or type(actual) is not int:
                return bad
            prop_errors0.append(abs(x-a))
            prop_errors1.append(abs(z-a))
    avg = lambda values: sum(values)/len(values)
    result = _out("PAIRED_HOLDOUT_RESEARCH_ONLY", sample_size=len(rows),
                  baseline={"brier": avg(base_brier), "log_loss": avg(base_ll),
                            "lower_bound_empirical_gap": avg(gap0),
                            "prop_mae": avg(prop_errors0) if prop_errors0 else None},
                  challenger={"brier": avg(next_brier), "log_loss": avg(next_ll),
                              "lower_bound_empirical_gap": avg(gap1),
                              "prop_mae": avg(prop_errors1) if prop_errors1 else None},
                  paired_brier_delta=avg(next_brier)-avg(base_brier),
                  paired_log_loss_delta=avg(next_ll)-avg(base_ll),
                  independently_certified=False, model_promoted=False)
    return result


def joint_scenario_slip_shadow(legs: Sequence[Mapping[str, Any]],
                               bundle: Mapping[str, Any],
                               offer: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Estimate full-slip success ONLY from frozen, jointly resolved scenarios.

    No independence product, no claimed fitted lower bound, no live-market EV.
    Caller must independently authenticate source hashes and scenario science.
    """
    bad = _out("JOINT_SCENARIOS_UNVERIFIED", joint_probability=None,
               break_even_probability=None, research_edge=None,
               joint_model_certified=False)
    freeze, cutoff = _utc(bundle.get("scenario_frozen_at_utc")), _utc(bundle.get("joint_fit_cutoff_utc"))
    if (freeze is None or cutoff is None or cutoff > freeze
            or not bundle.get("source_snapshot_hash")
            or not bundle.get("joint_model_artifact_hash")
            or not bundle.get("joint_calibration_receipt_id")
            or bundle.get("joint_scenario_source_verified") is not True
            or not isinstance(legs, (list, tuple)) or not legs):
        return bad
    ids: set[str] = set()
    for leg in legs:
        if not isinstance(leg, Mapping) or _identity(leg) is None:
            return bad
        lid = str(leg.get("leg_id") or "").strip()
        start = _utc(leg.get("event_start_utc"))
        if not lid or lid in ids or start is None or freeze >= start:
            return bad
        if not str(leg.get("market_family") or "").strip() or not str(leg.get("settlement_basis") or "").strip():
            return bad
        ids.add(lid)
    scenarios = bundle.get("joint_scenarios")
    if not isinstance(scenarios, (tuple, list)) or not scenarios:
        return bad
    total, joint, marginals = 0.0, 0.0, {lid:0.0 for lid in ids}
    for state in scenarios:
        if not isinstance(state, Mapping):
            return bad
        w = _num(state.get("weight"), 0, 1)
        values = state.get("leg_success")
        if w is None or not isinstance(values, Mapping) or set(values) != ids or any(type(x) is not bool for x in values.values()):
            return bad
        total += w
        if all(values.values()):
            joint += w
        for lid, value in values.items():
            if value:
                marginals[lid] += w
    if abs(total - 1) > 1e-6:
        return bad
    break_even, price_status = None, "EXECUTABLE_OFFER_MISSING"
    if offer is not None:
        gross, fee = _num(offer.get("gross_multiplier"), 1.0), _num(offer.get("fee_rate_on_profit"), 0, 1)
        quote_at = _utc(offer.get("quoted_at_utc"))
        quote_for = str(offer.get("joint_offer_id") or "").strip()
        if (gross is not None and gross > 1 and fee is not None and fee < 1
                and quote_at is not None and quote_at <= freeze and quote_for
                and isinstance(offer.get("exact_leg_ids"), (list, tuple))
                and all(isinstance(x, str) for x in offer["exact_leg_ids"])
                and set(offer["exact_leg_ids"]) == ids):
            # If quoted gross payoff includes stake, payout net of a fee on
            # profit is: 1 + (gross - 1) * (1 - fee).
            net_gross = 1 + (gross - 1) * (1 - fee)
            break_even = 1 / net_gross
            price_status = "SUPPLIED_OFFER_UNAUTHENTICATED"
        else:
            price_status = "OFFER_INCOMPATIBLE_OR_STALE"
    return _out("JOINT_SCENARIOS_RESEARCH_ONLY", joint_probability=joint,
                marginal_probabilities=marginals,
                joint_probability_method="FROZEN_WEIGHTED_JOINT_STATES_NOT_INDEPENDENCE",
                scenarios_count=len(scenarios), price_status=price_status,
                break_even_probability=break_even,
                research_edge=joint - break_even if break_even is not None else None,
                joint_model_certified=False, publishable_value=False)
