"""Non-serving Nov 14 WOW V17 follow-on: NBA, cross-market, and value lanes.

Class C/diagnostic research only. Does not replace fitted specialists,
existing exposure/cash gates, the V17 terminal reducer, or any probabilities.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite, sqrt
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
AUTOMATIC_PROMOTION = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
RESEARCH_ONLY = True


def _prob(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if isfinite(v) and 0 <= v <= 1 else None


def _number(value: Any, *, minimum: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if not isfinite(v) or (minimum is not None and v < minimum):
        return None
    return v


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None


def _name(value: Any) -> str:
    return str(value or "").strip().upper()


def _base(status: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": status,
        "research_only": RESEARCH_ONLY,
        "can_execute": CAN_EXECUTE,
        "automatic_promotion": AUTOMATIC_PROMOTION,
        "terminal_authority": TERMINAL_AUTHORITY,
        "probability_mutated": False,
        "production_publication_allowed": False,
        **extra,
    }


def nba_fatigue_three_point_shadow(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Emit pregame interaction features and Beta-binomial 3PT variance ONLY.

    Beta-binomial is the two-cell Dirichlet-multinomial special case. No
    fitted fatigue coefficient, winner probability, or calibrated interval
    is inferred from the raw interaction or shot prior.
    """
    pending = _base(
        "MODEL_INPUTS_INSUFFICIENT", sport="NBA", fatigue_interaction=None,
        three_point_margin_variance=None, three_point_margin_sd=None,
        home_three_point_variance=None, away_three_point_variance=None,
        adjusted_win_probability=None, calibrated_lower_bound=None,
    )
    start = _time(snapshot.get("event_start_utc"))
    freeze = _time(snapshot.get("snapshot_timestamp_utc"))
    if _name(snapshot.get("sport")) != "NBA" or not snapshot.get("official_event_id"):
        return pending
    if not snapshot.get("source_snapshot_hash") or not snapshot.get("shot_fit_artifact_hash"):
        return _base("SOURCE_PROVENANCE_MISSING", **{k: v for k, v in pending.items() if k not in {"status", "research_only", "can_execute", "automatic_promotion", "terminal_authority", "probability_mutated", "production_publication_allowed"}})
    if start is None or freeze is None or not freeze < start:
        return {**pending, "status": "PREGAME_CHRONOLOGY_INVALID"}
    fit_cutoff = _time(snapshot.get("shot_fit_data_cutoff_utc"))
    if fit_cutoff is None or fit_cutoff > freeze:
        return {**pending, "status": "SHOT_FIT_NOT_AVAILABLE_AT_FREEZE"}
    pace = _number(snapshot.get("projected_pace"), minimum=1)
    perimeter = _number(snapshot.get("home_perimeter_defense_index"), minimum=0)
    b2b = snapshot.get("opponent_back_to_back")
    traveled = snapshot.get("opponent_traveled_since_last_game")
    if pace is None or perimeter is None or type(b2b) is not bool or type(traveled) is not bool:
        return pending
    rest_home = _number(snapshot.get("home_rest_days"), minimum=0)
    rest_away = _number(snapshot.get("away_rest_days"), minimum=0)
    if rest_home is None or rest_away is None:
        return pending

    shot_variances: dict[str, float] = {}
    for side in ("home", "away"):
        profile = snapshot.get(side + "_three_point_prior")
        if not isinstance(profile, Mapping):
            return pending
        attempts = profile.get("projected_attempts")
        alpha = _number(profile.get("alpha"), minimum=0)
        beta = _number(profile.get("beta"), minimum=0)
        prior_time = _time(profile.get("frozen_at_utc"))
        if type(attempts) is not int or attempts < 0 or alpha is None or beta is None:
            return pending
        if (alpha <= 0 or beta <= 0 or prior_time is None or prior_time > freeze
                or str(profile.get("official_event_id") or "") != str(snapshot["official_event_id"])):
            return {**pending, "status": "SHOT_PRIOR_NOT_AVAILABLE_AT_FREEZE"}
        mu = alpha / (alpha + beta)
        concentration = alpha + beta
        # Var(K) = n mu (1-mu) (n+k)/(1+k) with Beta(alpha,beta).
        shot_variances[side] = attempts * mu * (1 - mu) * (attempts + concentration) / (1 + concentration)

    result = {
        **pending,
        "status": "FEATURES_ONLY_SHADOW",
        "fatigue_interaction": float(b2b and traveled) * perimeter * pace,
        "rest_days_differential_home_minus_away": rest_home - rest_away,
        "home_three_point_variance": shot_variances["home"],
        "away_three_point_variance": shot_variances["away"],
        "shot_count_covariance": None,
        "three_point_margin_variance": None,
        "three_point_margin_sd": None,
        "feature_basis": "FROZEN_INPUT_ONLY_NO_FITTED_EFFECT",
    }
    cov = snapshot.get("shot_count_covariance")
    if cov is not None:
        cov_value = _number(cov)
        if cov_value is None or not snapshot.get("joint_shot_covariance_artifact_hash"):
            return {**pending, "status": "SHOT_COVARIANCE_NOT_VALIDATED"}
        limit = sqrt(shot_variances["home"] * shot_variances["away"])
        if abs(cov_value) > limit + 1e-9:
            return {**pending, "status": "SHOT_COVARIANCE_INVALID"}
        variance = max(0.0, 9.0 * (
            shot_variances["home"] + shot_variances["away"] - 2.0 * cov_value
        ))
        result.update(
            shot_count_covariance=cov_value,
            three_point_margin_variance=variance,
            three_point_margin_sd=sqrt(variance),
        )
    return result


_MARKET = {
    "MONEYLINE": "ML", "OUTRIGHT_WINNER": "ML", "GAME_WINNER": "ML",
    "MATCH_WINNER": "ML", "FIGHT_WINNER": "ML",
    "SPREAD": "SPREAD", "HANDICAP": "SPREAD", "PUCK_LINE": "SPREAD",
    "TOTAL": "TOTAL", "GAME_TOTAL": "TOTAL", "OVER_UNDER": "TOTAL",
}


def audit_same_event_cross_market(legs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Exact-event cross-market audit; refuses to assume independent legs.

    This supplements, but does not bypass, portfolio_exposure_gate. A supplied
    joint-receipt string is never authenticated inside this function, so it
    cannot independently resolve a correlated pair.
    """
    findings: dict[str, Any] = {}
    events: dict[tuple[str, str], list[tuple[str, str]]] = {}
    seen_ids: set[str] = set()
    for leg in legs:
        row_id = str(leg.get("row_id") or "").strip()
        sport = _name(leg.get("sport"))
        event = str(leg.get("official_event_id") or "").strip()
        market = _MARKET.get(_name(leg.get("market_family")))
        blockers: list[str] = []
        if not row_id or row_id in seen_ids:
            return _base("ROW_ID_MISSING_OR_DUPLICATE", rows={}, card_structurally_qualified=False)
        seen_ids.add(row_id)
        if not sport or not event or market is None:
            blockers.append("EXACT_EVENT_OR_MARKET_IDENTITY_MISSING")
        if market in {"SPREAD", "TOTAL"}:
            line = _number(leg.get("exact_line"))
            if line is None or not str(leg.get("selected_side") or "").strip():
                blockers.append("SECONDARY_MARKET_EXACT_LINE_MISSING")
            receipt_time = _time(leg.get("market_prediction_timestamp_utc"))
            start_time = _time(leg.get("event_start_utc"))
            if not (
                leg.get("market_model_receipt_id")
                and leg.get("market_model_artifact_hash")
                and leg.get("calibration_receipt_id")
                and _MARKET.get(_name(leg.get("model_market_family"))) == market
                and str(leg.get("model_official_event_id") or "") == event
                and _number(leg.get("model_exact_line")) == line
                and _name(leg.get("model_selected_side")) == _name(leg.get("selected_side"))
                and leg.get("market_model_certified") is True
                and receipt_time is not None and start_time is not None
                and receipt_time < start_time
            ):
                blockers.append("SECONDARY_MARKET_SPECIALIST_UNVERIFIED")
        findings[row_id] = {
            "sport": sport, "official_event_id": event, "market_role": market,
            "same_event_cross_market_correlation": None,
            "joint_probability": None, "co_exposure_row_ids": [],
            "blockers": blockers,
            "card_eligible": False,
        }
        if sport and event and market is not None:
            events.setdefault((sport, event), []).append((row_id, market))

    for group in events.values():
        families = {m for _, m in group}
        if len(families) > 1:
            for row_id, _ in group:
                findings[row_id]["co_exposure_row_ids"] = [rid for rid, _ in group if rid != row_id]
                findings[row_id]["blockers"].append("SAME_EVENT_CROSS_MARKET_DEPENDENCE_UNQUANTIFIED")
    blocked = any(row["blockers"] for row in findings.values())
    return _base(
        "HELD_FOR_CROSS_MARKET" if blocked else "STRUCTURE_ONLY_PASS",
        rows=findings,
        card_structurally_qualified=not blocked,
        card_publication_allowed=False,
    )


def dual_gate_strong_confidence_shadow(
    model: Mapping[str, Any],
    quote: Mapping[str, Any] | None,
    policy: Mapping[str, Any],
    *,
    as_of_utc: str,
) -> dict[str, Any]:
    """Separate sporting probability ranking from exact executable value.

    Computes research-only candidate gates. Cannot publish or place orders.
    Fair no-vig probability is market context, NOT the executable break-even.
    """
    base = _base(
        "MODEL_INPUTS_INSUFFICIENT", probability_rank_eligible=False,
        strong_probability_tier_pass=False, value_candidate_pass=False,
        actionable_card_eligible=False, blockers=[], break_even_probability=None,
        lower_bound_friction_adjusted_edge=None, market_no_vig_probability=None,
        calibrated_probability=model.get("calibrated_probability"),
        calibrated_lower_bound=model.get("calibrated_lower_bound"),
    )
    p = _prob(model.get("calibrated_probability"))
    lower = _prob(model.get("calibrated_lower_bound"))
    weight = _prob(model.get("market_prior_weight"))
    pred_time = _time(model.get("immutable_prediction_timestamp_utc"))
    event_time = _time(model.get("event_start_utc"))
    now = _time(as_of_utc)
    if not (model.get("prediction_id") and model.get("model_artifact_hash")
            and model.get("official_event_id") and model.get("sport")
            and model.get("selected_participant")
            and _MARKET.get(_name(model.get("market_family"))) == "ML"
            and model.get("market_period") and model.get("settlement_basis")
            and p is not None
            and lower is not None and lower <= p
            and pred_time is not None and event_time is not None and now is not None
            and pred_time <= now and pred_time < event_time and now < event_time
            and model.get("calibration_status") == "PASS"
            and model.get("probability_publishable") is True
            and model.get("rank_eligible") is True):
        return base
    out = {**base, "status": "PROBABILITY_ONLY", "probability_rank_eligible": True}
    floor = _prob(policy.get("approved_tier_1_floor"))
    buffer = _prob(policy.get("friction_buffer"))
    if (floor is None or buffer is None or not policy.get("approved_policy_receipt")
            or policy.get("policy_registry_verified") is not True):
        return {**out, "status": "TIER_POLICY_UNVERIFIED", "blockers": ["TIER_POLICY_UNVERIFIED"]}
    strong = lower >= floor
    out["strong_probability_tier_pass"] = strong
    if not strong:
        return {**out, "status": "BELOW_STRONG_PROBABILITY_FLOOR",
                "blockers": ["BELOW_STRONG_PROBABILITY_FLOOR"]}
    if quote is None:
        return {**out, "status": "PRICE_MISSING", "blockers": ["PRICE_MISSING"]}
    quote_time = _time(quote.get("quoted_at_utc"))
    odds = quote.get("american_odds")
    fee = _prob(quote.get("fee_rate_on_profit"))
    no_vig = _prob(quote.get("market_no_vig_probability"))
    max_age = _number(policy.get("max_quote_age_minutes"), minimum=0)
    if (not quote.get("quote_source_id")
            or not quote.get("two_way_market_snapshot_id")
            or quote.get("two_way_market_verified") is not True
            or _name(quote.get("sport")) != _name(model.get("sport"))
            or str(quote.get("official_event_id")) != str(model.get("official_event_id"))
            or _name(quote.get("selected_participant")) != _name(model.get("selected_participant"))
            or _MARKET.get(_name(quote.get("market_family"))) != "ML"
            or _name(quote.get("market_period")) != _name(model.get("market_period"))
            or _name(quote.get("settlement_basis")) != _name(model.get("settlement_basis"))
            or quote_time is None or quote_time > now or quote_time >= event_time
            or max_age is None or (now - quote_time).total_seconds() / 60 > max_age
            or type(odds) is not int or abs(odds) < 100
            or fee is None or no_vig is None or weight is None):
        return {**out, "status": "EXACT_PRICE_OR_IDENTITY_UNVERIFIED",
                "blockers": ["EXACT_PRICE_OR_IDENTITY_UNVERIFIED"]}
    profit_on_one_unit = (100 / abs(odds)) if odds < 0 else (odds / 100)
    net_profit = profit_on_one_unit * (1 - fee)
    if net_profit <= 0:
        return {**out, "status": "NO_EXECUTABLE_NET_RETURN", "blockers": ["NO_EXECUTABLE_NET_RETURN"]}
    break_even = 1 / (1 + net_profit)
    edge = lower - break_even - buffer
    reasons = []
    if weight > 0.50:
        reasons.append("MARKET_DEPENDENT_MODEL")
    if edge <= 0:
        reasons.append("LOWER_BOUND_EXECUTABLE_EDGE_NON_POSITIVE")
    if abs(p - no_vig) > .10 and quote.get("market_disagreement_resolved") is not True:
        reasons.append("MODEL_MARKET_DISAGREEMENT_UNRESOLVED")
    out.update(
        break_even_probability=break_even,
        lower_bound_friction_adjusted_edge=edge,
        market_no_vig_probability=no_vig,
        value_candidate_pass=not reasons,
        status="RESEARCH_VALUE_CANDIDATE" if not reasons else "RESEARCH_VALUE_HOLD",
        blockers=reasons,
    )
    return out


__all__ = [
    "nba_fatigue_three_point_shadow",
    "audit_same_event_cross_market",
    "dual_gate_strong_confidence_shadow",
]
