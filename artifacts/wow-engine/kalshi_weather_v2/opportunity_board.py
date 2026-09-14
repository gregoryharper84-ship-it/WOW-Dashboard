from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


STRONG_EDGE_MIN = 0.10
QUALIFIED_EDGE_MIN = 0.05


@dataclass(frozen=True)
class OpportunityBoardResult:
    status: str
    opportunities: tuple[Mapping[str, Any], ...]
    generated_from_predictions: int
    rankable_opportunities: int
    can_execute: bool = False


def build_opportunity_board(*, client) -> OpportunityBoardResult:
    predictions = _rows(client, "wow_kalshi_weather_predictions")
    market_snapshots = _rows(client, "wow_kalshi_weather_market_snapshots")
    rules = _rows(client, "wow_kalshi_weather_contract_rules")

    rule_by_id = {
        str(row.get("rule_snapshot_id")): row
        for row in rules
        if row.get("rule_snapshot_id")
    }
    latest_prediction = _latest_by(predictions, key="ticker", time_key="decision_time")
    latest_market_by_prediction = _latest_by(
        market_snapshots,
        key="prediction_id",
        time_key="retrieved_at",
    )

    out: list[dict[str, Any]] = []
    for ticker, prediction in latest_prediction.items():
        market = latest_market_by_prediction.get(str(prediction.get("prediction_id") or ""), {})
        rule = rule_by_id.get(str(prediction.get("rule_snapshot_id") or ""), {})
        model_payload = prediction.get("model_payload") if isinstance(prediction.get("model_payload"), Mapping) else {}
        contract = model_payload.get("contract") if isinstance(model_payload.get("contract"), Mapping) else {}

        p_yes = _float_or_none(prediction.get("p_yes"))
        p_no = _float_or_none(prediction.get("p_no"))
        lower_yes = _float_or_none(prediction.get("lower_bound_yes"))
        upper_yes = _float_or_none(prediction.get("upper_bound_yes"))
        yes_ask = _float_or_none(market.get("yes_best_ask"))
        no_ask = _float_or_none(market.get("no_best_ask"))
        yes_break_even = _float_or_none(market.get("yes_effective_break_even"))
        no_break_even = _float_or_none(market.get("no_effective_break_even"))
        probability_publishable = bool(prediction.get("probability_publishable"))
        friction_verified = bool(market.get("friction_model_verified"))
        market_open = str(market.get("market_status") or "").strip().lower() in {"active", "open"}

        raw_yes_edge = p_yes - yes_ask if probability_publishable and p_yes is not None and yes_ask is not None else None
        raw_no_edge = p_no - no_ask if probability_publishable and p_no is not None and no_ask is not None else None
        adjusted_yes_edge = None
        adjusted_no_edge = None
        if probability_publishable and friction_verified:
            if lower_yes is not None and yes_break_even is not None:
                adjusted_yes_edge = lower_yes - yes_break_even
            if upper_yes is not None and no_break_even is not None:
                adjusted_no_edge = (1.0 - upper_yes) - no_break_even

        best_side = None
        adjusted_edge = None
        raw_edge = None
        model_probability = None
        market_probability = None
        expected_value_per_dollar_risked = None
        if adjusted_yes_edge is not None or adjusted_no_edge is not None:
            candidates = [
                ("YES", adjusted_yes_edge, raw_yes_edge, p_yes, yes_break_even),
                ("NO", adjusted_no_edge, raw_no_edge, p_no, no_break_even),
            ]
            candidates = [row for row in candidates if row[1] is not None]
            if candidates:
                best_side, adjusted_edge, raw_edge, model_probability, market_probability = max(
                    candidates,
                    key=lambda row: float(row[1]),
                )
                if market_probability not in (None, 0) and model_probability is not None:
                    expected_value_per_dollar_risked = (model_probability - market_probability) / market_probability

        blockers = list(prediction.get("blockers") or [])
        status, rank_eligible = _classify(
            probability_publishable=probability_publishable,
            friction_verified=friction_verified,
            market_open=market_open,
            adjusted_edge=adjusted_edge,
            blockers=blockers,
        )
        out.append(
            {
                "market": str(rule.get("raw_market", {}).get("title") if isinstance(rule.get("raw_market"), Mapping) else "") or ticker,
                "contract": ticker,
                "settlement_source": rule.get("settlement_source_name"),
                "settlement_location": contract.get("settlement_location_code"),
                "observation_window": contract.get("observation_window"),
                "current_yes_ask": yes_ask,
                "current_no_ask": no_ask,
                "model_p_yes": p_yes if probability_publishable else None,
                "model_p_no": p_no if probability_publishable else None,
                "model_fair_yes": p_yes if probability_publishable else None,
                "model_fair_no": p_no if probability_publishable else None,
                "uncertainty_lower_yes": lower_yes if probability_publishable else None,
                "uncertainty_upper_yes": upper_yes if probability_publishable else None,
                "best_side": best_side,
                "raw_edge": raw_edge,
                "uncertainty_adjusted_edge": adjusted_edge,
                "expected_value_per_dollar_risked": expected_value_per_dollar_risked,
                "friction_model_verified": friction_verified,
                "market_price_time": market.get("retrieved_at"),
                "forecast_decision_time": prediction.get("decision_time"),
                "central_estimate_f": prediction.get("central_estimate_f"),
                "threshold_distance": prediction.get("threshold_distance"),
                "confidence": _confidence(prediction),
                "key_weather_driver": _driver(model_payload),
                "key_risk": _risk(blockers, friction_verified, market_open),
                "status": status,
                "rank_eligible": rank_eligible,
                "blockers": blockers,
                "probability_publishable": probability_publishable,
                "market_price_used_as_weather_probability": False,
                "can_execute": False,
            }
        )

    out.sort(
        key=lambda row: (
            not bool(row.get("rank_eligible")),
            -(float(row.get("uncertainty_adjusted_edge")) if row.get("uncertainty_adjusted_edge") is not None else -999.0),
            str(row.get("contract") or ""),
        )
    )
    rankable = sum(1 for row in out if row.get("rank_eligible"))
    return OpportunityBoardResult(
        status="KALSHI_WEATHER_OPPORTUNITY_BOARD",
        opportunities=tuple(out),
        generated_from_predictions=len(latest_prediction),
        rankable_opportunities=rankable,
        can_execute=False,
    )


def _classify(
    *,
    probability_publishable: bool,
    friction_verified: bool,
    market_open: bool,
    adjusted_edge: float | None,
    blockers: list[Any],
) -> tuple[str, bool]:
    blocker_text = " ".join(str(x) for x in blockers).upper()
    if "SETTLEMENT" in blocker_text and "AMBIG" in blocker_text:
        return "NO_PLAY_SETTLEMENT_AMBIGUITY", False
    if not probability_publishable:
        return "NO_PLAY_DATA_INSUFFICIENT", False
    if not market_open:
        return "WATCH", False
    if not friction_verified:
        return "WATCH", False
    if adjusted_edge is None:
        return "NO_PLAY_DATA_INSUFFICIENT", False
    if adjusted_edge >= STRONG_EDGE_MIN:
        return "STRONG_EDGE", True
    if adjusted_edge >= QUALIFIED_EDGE_MIN:
        return "QUALIFIED_EDGE", True
    if adjusted_edge > 0:
        return "WATCH", False
    return "NO_EDGE", False


def _latest_by(rows: list[Mapping[str, Any]], *, key: str, time_key: str) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        identity = str(row.get(key) or "").strip()
        if not identity:
            continue
        current = out.get(identity)
        if current is None or str(row.get(time_key) or "") > str(current.get(time_key) or ""):
            out[identity] = row
    return out


def _rows(client, table: str) -> list[Mapping[str, Any]]:
    try:
        data = client.table(table).select("*").limit(5000).execute().data or []
    except Exception:
        return []
    return [dict(row) for row in data if isinstance(row, Mapping)]


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _confidence(prediction: Mapping[str, Any]) -> str:
    if not prediction.get("probability_publishable"):
        return "LOW_UNCERTIFIED"
    width = None
    lo = _float_or_none(prediction.get("lower_bound_yes"))
    hi = _float_or_none(prediction.get("upper_bound_yes"))
    if lo is not None and hi is not None:
        width = hi - lo
    if width is not None and width <= 0.15:
        return "HIGH"
    if width is not None and width <= 0.30:
        return "MEDIUM"
    return "LOW"


def _driver(model_payload: Mapping[str, Any]) -> str | None:
    fusion = model_payload.get("forecast_fusion") if isinstance(model_payload.get("forecast_fusion"), Mapping) else {}
    estimate = fusion.get("central_estimate_f") or model_payload.get("central_estimate_f")
    disagreement = fusion.get("provider_disagreement_f")
    if estimate is None and disagreement is None:
        return None
    return f"forecast_center={estimate}; provider_disagreement_f={disagreement}"


def _risk(blockers: list[Any], friction_verified: bool, market_open: bool) -> str:
    if blockers:
        return ";".join(str(x) for x in blockers[:4])
    if not friction_verified:
        return "ACCOUNT_CASH_ROUNDING_PRECISION_NOT_VERIFIED"
    if not market_open:
        return "MARKET_NOT_OPEN"
    return "WEATHER_AND_THRESHOLD_UNCERTAINTY"
