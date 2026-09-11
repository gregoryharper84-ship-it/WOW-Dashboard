from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping

from kalshi_weather_v2.persistence import KalshiWeatherPersistence

GLOBAL_TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
LOCAL_WEATHER_AUTHORITY = "KALSHI_WEATHER_MARKET_EXPERT"
CAPABILITY_KEY = "KALSHI_WEATHER_PROBABILITY"

# These are scoped market/edge holds. They remain visible and continue to block
# edge/rank publication, but they do not erase an independently completed,
# immutable weather probability.
_MARKET_ONLY_PREFIXES = (
    "FEE_",
    "MARKET_",
    "ORDERBOOK_",
    "EXECUTABLE_",
    "FRICTION_",
)
_MARKET_ONLY_EXACT = {
    "FEE_AND_FRICTION_NOT_EVALUATED_IN_SHADOW_CAPTURE",
    "SHADOW_ONLY_NO_PUBLICATION",
    "V17_GLOBAL_TERMINAL_REQUIRED",
}


def reduce_kalshi_weather_prediction_v17(*, client, prediction_id: str) -> Mapping[str, Any]:
    """Apply the V17 global publication ceiling to one immutable weather row.

    The weather specialist owns the weather probability. This reducer owns only
    validation/publication authority. It never invents or modifies probability.
    Market evidence is reconciled separately after probability validation so a
    market/fee failure cannot silently erase a completed weather probability.
    """
    persistence = KalshiWeatherPersistence(client)
    prediction = persistence.load_prediction(prediction_id)
    if not prediction:
        return _blocked("NO_PLAY_DATA_INSUFFICIENT", "IMMUTABLE_PREDICTION_NOT_FOUND", ["IMMUTABLE_PREGAME_WRITE_MISSING"])

    blockers: list[str] = []
    warnings = _strings(prediction.get("warnings"))
    local_blockers = _strings(prediction.get("blockers"))

    if bool(prediction.get("can_execute")):
        blockers.append("CAN_EXECUTE_MUST_BE_FALSE")

    rule_snapshot_id = str(prediction.get("rule_snapshot_id") or "")
    rule = persistence.load_rule(rule_snapshot_id) if rule_snapshot_id else None
    if not rule:
        blockers.append("SETTLEMENT_RULE_SNAPSHOT_MISSING")
    else:
        if str(rule.get("ticker") or "") != str(prediction.get("ticker") or ""):
            blockers.append("RULE_PREDICTION_TICKER_MISMATCH")
        if bool(rule.get("can_execute")):
            blockers.append("RULE_CAN_EXECUTE_MUST_BE_FALSE")
        if not str(rule.get("settlement_source_name") or "").strip():
            blockers.append("SETTLEMENT_SOURCE_UNRESOLVED")

    capability = persistence.load_runtime_capability(CAPABILITY_KEY)
    capability_evidence = capability.get("evidence") if isinstance(capability.get("evidence"), Mapping) else {}
    if str(capability.get("capability_status") or "").upper() != "AVAILABLE":
        blockers.append("KALSHI_WEATHER_PROBABILITY_CAPABILITY_UNAVAILABLE")
    if capability_evidence.get("probability_publishable") is not True:
        blockers.append("CAPABILITY_NOT_CERTIFIED_FOR_PROBABILITY_PUBLICATION")
    if bool(capability.get("can_execute")):
        blockers.append("CAPABILITY_CAN_EXECUTE_MUST_BE_FALSE")

    model_payload = prediction.get("model_payload") if isinstance(prediction.get("model_payload"), Mapping) else {}
    contract = model_payload.get("contract") if isinstance(model_payload.get("contract"), Mapping) else {}
    terminal = model_payload.get("terminal") if isinstance(model_payload.get("terminal"), Mapping) else {}
    terminal_payload = terminal.get("payload") if isinstance(terminal.get("payload"), Mapping) else {}

    if not contract:
        blockers.append("CONTRACT_SNAPSHOT_MISSING")
    else:
        if str(contract.get("ticker") or "") != str(prediction.get("ticker") or ""):
            blockers.append("CONTRACT_PREDICTION_TICKER_MISMATCH")
        if str(contract.get("rule_snapshot_id") or "") != rule_snapshot_id:
            blockers.append("CONTRACT_RULE_SNAPSHOT_MISMATCH")
        if not str(contract.get("settlement_source") or "").strip():
            blockers.append("CONTRACT_SETTLEMENT_SOURCE_MISSING")
        if not str(contract.get("settlement_location_code") or "").strip() and not str(contract.get("settlement_station_id") or "").strip():
            blockers.append("CONTRACT_SETTLEMENT_LOCATION_MISSING")
        if not str(contract.get("timezone") or "").strip():
            blockers.append("CONTRACT_TIMEZONE_MISSING")
        if not str(contract.get("observation_window") or "").strip():
            blockers.append("CONTRACT_OBSERVATION_WINDOW_MISSING")

    if model_payload.get("market_price_used_as_model_input") is not False:
        blockers.append("MARKET_PRICE_MODEL_INPUT_PROHIBITED")
    if bool(model_payload.get("can_execute")):
        blockers.append("MODEL_CAN_EXECUTE_MUST_BE_FALSE")

    if terminal_payload:
        required = terminal_payload.get("global_terminal_authority_required")
        if required not in (None, GLOBAL_TERMINAL_AUTHORITY):
            blockers.append("GLOBAL_TERMINAL_AUTHORITY_MISMATCH")
        if terminal_payload.get("local_global_terminal_authority") is True:
            blockers.append("LOCAL_TERMINAL_AUTHORITY_PROHIBITED")

    p_yes = _prob(prediction.get("p_yes"), "P_YES", blockers)
    p_no = _prob(prediction.get("p_no"), "P_NO", blockers)
    lower = _prob(prediction.get("lower_bound_yes"), "LOWER_BOUND_YES", blockers)
    upper = _prob(prediction.get("upper_bound_yes"), "UPPER_BOUND_YES", blockers)
    if p_yes is not None and p_no is not None and abs((p_yes + p_no) - 1.0) > 1e-6:
        blockers.append("PROBABILITY_COMPLEMENT_INCOHERENT")
    if p_yes is not None and lower is not None and upper is not None and not (lower <= p_yes <= upper):
        blockers.append("DYNAMIC_CALIBRATION_BOUNDS_INVALID")

    calibration_profile_id = str(prediction.get("calibration_profile_id") or "")
    calibration = _select_one(client, "wow_kalshi_weather_calibration_profiles", "calibration_profile_id", calibration_profile_id) if calibration_profile_id else None
    if not calibration_profile_id or not calibration:
        blockers.append("CERTIFIED_CALIBRATION_PROFILE_MISSING")
    else:
        if calibration.get("certified") is not True:
            blockers.append("CALIBRATION_PROFILE_NOT_CERTIFIED")
        certification_evidence = calibration.get("certification_evidence")
        if not isinstance(certification_evidence, Mapping) or not certification_evidence:
            blockers.append("CALIBRATION_CERTIFICATION_EVIDENCE_MISSING")
        sample_n = calibration.get("sample_n")
        if not isinstance(sample_n, int) or isinstance(sample_n, bool) or sample_n <= 0:
            blockers.append("CALIBRATION_SAMPLE_EVIDENCE_MISSING")
        if bool(calibration.get("can_execute")):
            blockers.append("CALIBRATION_CAN_EXECUTE_MUST_BE_FALSE")
        if str(calibration.get("model_version") or "") != str(prediction.get("model_version") or ""):
            blockers.append("CALIBRATION_MODEL_VERSION_MISMATCH")
        if contract and str(calibration.get("lane") or "") != str(contract.get("lane") or ""):
            blockers.append("CALIBRATION_LANE_MISMATCH")
        expected_station = str(contract.get("settlement_location_code") or contract.get("settlement_station_id") or "") if contract else ""
        if expected_station and str(calibration.get("station_id") or "") != expected_station:
            blockers.append("CALIBRATION_SETTLEMENT_IDENTITY_MISMATCH")
        expected_lead_bucket = str(model_payload.get("lead_time_bucket") or "")
        if not expected_lead_bucket:
            blockers.append("PREDICTION_LEAD_TIME_BUCKET_MISSING")
        elif str(calibration.get("lead_time_bucket") or "") != expected_lead_bucket:
            blockers.append("CALIBRATION_LEAD_TIME_BUCKET_MISMATCH")
        decision_time = _dt(prediction.get("decision_time"))
        fitted_as_of = _dt(calibration.get("fitted_as_of"))
        if decision_time is None or fitted_as_of is None or fitted_as_of > decision_time:
            blockers.append("CALIBRATION_AS_OF_VIOLATION")

    evidence_ids = [str(x) for x in (prediction.get("evidence_snapshot_ids") or []) if str(x)]
    if not evidence_ids:
        blockers.append("WEATHER_EVIDENCE_SNAPSHOT_MISSING")
    else:
        decision_time = _dt(prediction.get("decision_time"))
        for source_id in evidence_ids:
            source = _select_one(client, "wow_kalshi_weather_source_snapshots", "source_snapshot_id", source_id)
            if not source:
                blockers.append(f"SOURCE_SNAPSHOT_MISSING:{source_id}")
                continue
            if str(source.get("rule_snapshot_id") or "") != rule_snapshot_id:
                blockers.append(f"SOURCE_RULE_SNAPSHOT_MISMATCH:{source_id}")
            evidence_time = _dt(source.get("issued_at") or source.get("evidence_time"))
            if decision_time is None or evidence_time is None or evidence_time > decision_time:
                blockers.append(f"SOURCE_AS_OF_VIOLATION:{source_id}")

    probability_blockers = [b for b in local_blockers if not _market_only(b)] + blockers
    probability_blockers = list(dict.fromkeys(probability_blockers))

    if probability_blockers:
        settlement_failure = any("SETTLEMENT" in b or "RULE_" in b or "CONTRACT_" in b for b in probability_blockers)
        return _blocked(
            "NO_PLAY_SETTLEMENT_AMBIGUITY" if settlement_failure else "NO_PLAY_DATA_INSUFFICIENT",
            "V17_WEATHER_PUBLICATION_BLOCKED",
            probability_blockers,
            local_blockers=local_blockers,
            edge_blockers=list(dict.fromkeys(local_blockers + blockers)),
            warnings=warnings,
            prediction_id=prediction_id,
            capability=capability,
        )

    market_result = _reconcile_market_edge(
        client=client,
        prediction=prediction,
        p_yes=float(p_yes),
        p_no=float(p_no),
        lower_yes=float(lower),
        upper_yes=float(upper),
    )
    edge_blockers = list(dict.fromkeys([b for b in local_blockers if _market_only(b)] + market_result["blockers"]))
    raw_yes = market_result.get("raw_edge_yes")
    raw_no = market_result.get("raw_edge_no")
    conservative_yes = market_result.get("uncertainty_adjusted_edge_yes")
    conservative_no = market_result.get("uncertainty_adjusted_edge_no")
    best_side = market_result.get("best_side")
    best_adjusted = market_result.get("best_uncertainty_adjusted_edge")
    friction_verified = bool(market_result.get("friction_model_verified"))
    edge_publishable = bool(market_result.get("edge_publishable"))

    if not edge_publishable:
        status = "WATCH"
        code = "V17_WEATHER_PROBABILITY_PUBLISHED_MARKET_EDGE_HELD"
        rank_eligible = False
    elif best_adjusted is None or best_adjusted <= 0.0:
        status = "NO_EDGE"
        code = "NO_POSITIVE_UNCERTAINTY_ADJUSTED_EDGE"
        rank_eligible = False
    elif not friction_verified:
        status = "WATCH"
        code = "V17_WEATHER_POSITIVE_PRE_FEE_EDGE_FRICTION_HELD"
        rank_eligible = False
    else:
        status = "QUALIFIED_EDGE"
        code = "POSITIVE_UNCERTAINTY_ADJUSTED_EDGE"
        rank_eligible = True

    # V17 publication is a view over immutable rows. Historical decision-time
    # weather/model evidence is never mutated after the fact.
    return {
        "status": status,
        "code": code,
        "prediction_id": prediction_id,
        "ticker": prediction.get("ticker"),
        "decision_time": prediction.get("decision_time"),
        "model_version": prediction.get("model_version"),
        "calibration_profile_id": calibration_profile_id,
        "p_yes": p_yes,
        "p_no": p_no,
        "model_fair_price_yes": p_yes,
        "model_fair_price_no": p_no,
        "lower_bound_yes": lower,
        "upper_bound_yes": upper,
        "central_estimate_f": prediction.get("central_estimate_f"),
        "threshold_distance": prediction.get("threshold_distance"),
        "current_yes_price": market_result.get("yes_best_ask"),
        "current_no_price": market_result.get("no_best_ask"),
        "market_price_time": market_result.get("retrieved_at"),
        "raw_edge_yes": raw_yes,
        "raw_edge_no": raw_no,
        "uncertainty_adjusted_edge_yes": conservative_yes,
        "uncertainty_adjusted_edge_no": conservative_no,
        "best_side": best_side,
        "best_uncertainty_adjusted_edge": best_adjusted,
        "pre_fee_ev_per_share_yes": market_result.get("pre_fee_ev_per_share_yes"),
        "pre_fee_ev_per_share_no": market_result.get("pre_fee_ev_per_share_no"),
        "pre_fee_ev_per_dollar_risked_yes": market_result.get("pre_fee_ev_per_dollar_risked_yes"),
        "pre_fee_ev_per_dollar_risked_no": market_result.get("pre_fee_ev_per_dollar_risked_no"),
        "edge_basis": market_result.get("edge_basis"),
        "friction_model_verified": friction_verified,
        "probability_publishable": True,
        "edge_publishable": edge_publishable,
        "rank_eligible": rank_eligible,
        "local_terminal_label_audit_only": True,
        "global_terminal_authority": GLOBAL_TERMINAL_AUTHORITY,
        "controlling_specialist": LOCAL_WEATHER_AUTHORITY,
        "probability_blockers": [],
        "edge_blockers": edge_blockers,
        "warnings": warnings,
        "capability": capability,
        "immutable_pregame_write_verified": True,
        "row_reconciliation_verified": True,
        "can_execute": False,
    }


def governance_snapshot(*, client) -> Mapping[str, Any]:
    capability = KalshiWeatherPersistence(client).load_runtime_capability(CAPABILITY_KEY)
    evidence = capability.get("evidence") if isinstance(capability.get("evidence"), Mapping) else {}
    return {
        "schema_version": "WOW_V17_KALSHI_WEATHER_GOVERNANCE_V1",
        "service": "KALSHI_WEATHER_MARKET_EXPERT",
        "global_terminal_authority": GLOBAL_TERMINAL_AUTHORITY,
        "local_terminal_label_audit_only": True,
        "canonical_runtime": "RENDER_SUPABASE_GOVERNED_CORE",
        "capability": capability,
        "probability_publishable_now": (
            str(capability.get("capability_status") or "").upper() == "AVAILABLE"
            and evidence.get("probability_publishable") is True
        ),
        "invariants": {
            "contract_first": True,
            "exact_settlement_identity_required": True,
            "market_probability_is_not_model_probability": True,
            "certified_station_lane_lead_time_calibration_required": True,
            "calibration_certification_evidence_required": True,
            "immutable_pregame_write_required": True,
            "row_reconciliation_required": True,
            "point_estimate_is_not_lower_bound": True,
            "market_edge_reconciled_separately_from_probability": True,
            "unverified_friction_blocks_rank_not_probability": True,
            "local_specialist_may_publish": False,
            "can_execute": False,
        },
        "can_execute": False,
    }


def _reconcile_market_edge(
    *,
    client,
    prediction: Mapping[str, Any],
    p_yes: float,
    p_no: float,
    lower_yes: float,
    upper_yes: float,
) -> dict[str, Any]:
    blockers: list[str] = []
    market = _select_one(client, "wow_kalshi_weather_market_snapshots", "prediction_id", str(prediction.get("prediction_id") or ""))
    if not market:
        return {"edge_publishable": False, "friction_model_verified": False, "blockers": ["MARKET_SNAPSHOT_MISSING"]}

    if str(market.get("ticker") or "") != str(prediction.get("ticker") or ""):
        blockers.append("MARKET_PREDICTION_TICKER_MISMATCH")
    if market.get("executable_price_verified") is not True:
        blockers.append("EXECUTABLE_PRICE_UNVERIFIED")
    retrieved_at = _dt(market.get("retrieved_at"))
    decision_time = _dt(prediction.get("decision_time"))
    if retrieved_at is None or decision_time is None or retrieved_at < decision_time:
        blockers.append("MARKET_SNAPSHOT_TIME_INVALID")

    yes_ask = _optional_probability(market.get("yes_best_ask"))
    no_ask = _optional_probability(market.get("no_best_ask"))
    if yes_ask is None and no_ask is None:
        blockers.append("EXECUTABLE_ASK_UNAVAILABLE")

    raw_yes = p_yes - yes_ask if yes_ask is not None else None
    raw_no = p_no - no_ask if no_ask is not None else None
    lower_no = 1.0 - upper_yes

    friction_verified = bool(market.get("friction_model_verified"))
    yes_break_even = _optional_probability(market.get("yes_effective_break_even")) if friction_verified else None
    no_break_even = _optional_probability(market.get("no_effective_break_even")) if friction_verified else None

    if friction_verified:
        if yes_ask is not None and yes_break_even is None:
            blockers.append("YES_EFFECTIVE_BREAK_EVEN_MISSING")
        if no_ask is not None and no_break_even is None:
            blockers.append("NO_EFFECTIVE_BREAK_EVEN_MISSING")
        conservative_yes = lower_yes - yes_break_even if yes_break_even is not None else None
        conservative_no = lower_no - no_break_even if no_break_even is not None else None
        edge_basis = "VERIFIED_EFFECTIVE_BREAK_EVEN"
    else:
        conservative_yes = lower_yes - yes_ask if yes_ask is not None else None
        conservative_no = lower_no - no_ask if no_ask is not None else None
        edge_basis = "PRE_FEE_EXECUTABLE_ASK"
        blockers.append("FRICTION_MODEL_UNVERIFIED")

    candidates = [("YES", conservative_yes), ("NO", conservative_no)]
    candidates = [(side, edge) for side, edge in candidates if edge is not None]
    best_side, best_edge = max(candidates, key=lambda item: item[1]) if candidates else (None, None)

    fatal_edge_blockers = [
        b for b in blockers
        if b not in {"FRICTION_MODEL_UNVERIFIED"}
    ]
    edge_publishable = not fatal_edge_blockers and bool(candidates)

    return {
        "market_snapshot_id": market.get("market_snapshot_id"),
        "retrieved_at": market.get("retrieved_at"),
        "yes_best_ask": yes_ask,
        "no_best_ask": no_ask,
        "raw_edge_yes": raw_yes,
        "raw_edge_no": raw_no,
        "uncertainty_adjusted_edge_yes": conservative_yes,
        "uncertainty_adjusted_edge_no": conservative_no,
        "best_side": best_side,
        "best_uncertainty_adjusted_edge": best_edge,
        "pre_fee_ev_per_share_yes": raw_yes,
        "pre_fee_ev_per_share_no": raw_no,
        "pre_fee_ev_per_dollar_risked_yes": (raw_yes / yes_ask) if raw_yes is not None and yes_ask else None,
        "pre_fee_ev_per_dollar_risked_no": (raw_no / no_ask) if raw_no is not None and no_ask else None,
        "edge_basis": edge_basis,
        "friction_model_verified": friction_verified,
        "edge_publishable": edge_publishable,
        "blockers": blockers,
    }


def _blocked(status: str, code: str, blockers: list[str], **extra: Any) -> Mapping[str, Any]:
    return {
        "status": status,
        "code": code,
        "p_yes": None,
        "p_no": None,
        "probability_publishable": False,
        "edge_publishable": False,
        "rank_eligible": False,
        "global_terminal_authority": GLOBAL_TERMINAL_AUTHORITY,
        "controlling_specialist": LOCAL_WEATHER_AUTHORITY,
        "blockers": list(dict.fromkeys(blockers)),
        "can_execute": False,
        **extra,
    }


def _market_only(blocker: str) -> bool:
    return blocker in _MARKET_ONLY_EXACT or blocker.startswith(_MARKET_ONLY_PREFIXES)


def _strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(x) for x in value if str(x)]


def _prob(value: Any, name: str, blockers: list[str]) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        blockers.append(f"{name}_MISSING")
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        blockers.append(f"{name}_INVALID")
        return None
    return number


def _optional_probability(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        return None
    return number


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _select_one(client, table: str, key: str, value: str) -> Mapping[str, Any] | None:
    if not value:
        return None
    result = client.table(table).select("*").eq(key, value).limit(1).execute()
    rows = result.data or []
    return dict(rows[0]) if rows else None
