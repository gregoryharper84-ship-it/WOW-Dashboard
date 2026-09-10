from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .contract_rule_acquisition import KalshiContractRuleAcquirer
from .hourly_forecast_fusion import build_hourly_weather_evidence, source_snapshot_id
from .hourly_index import KalshiWeatherIndexAdapter, WeatherIndexError
from .hourly_rule_semantics import parse_hourly_temperature_rule
from .http_client import ReadOnlyJsonClient
from .kalshi_market_data import KalshiPublicMarketAdapter
from .models import MarketSnapshot, ProbabilityPackage
from .orchestrator import evaluate_weather_contract
from .persistence import KalshiWeatherPersistence, content_id
from .probability_core import WeatherProbabilityCore
from .source_adapters import NwsAdapter, OpenMeteoAdapter


MODEL_VERSION = "KALSHI_WEATHER_V2_HOURLY_SHADOW_R1"
DEFAULT_OPEN_METEO_MODELS = ("gfs_seamless", "ecmwf_ifs025")
ALLOWED_OPEN_METEO_MODELS = {
    "gfs_seamless",
    "ecmwf_ifs025",
    "gem_seamless",
    "icon_seamless",
}


class KalshiWeatherRuntimeError(RuntimeError):
    def __init__(self, code: str, blockers: Sequence[str], *, status_code: int = 409):
        self.code = code
        self.blockers = tuple(dict.fromkeys(str(x) for x in blockers if str(x)))
        self.status_code = status_code
        super().__init__(f"{code}: {', '.join(self.blockers)}")


def capture_hourly_shadow(
    *,
    client,
    ticker: str,
    index_city: str,
    expected_location: str,
    forecast_latitude: float,
    forecast_longitude: float,
    decision_time: str | None = None,
    open_meteo_models: Sequence[str] = DEFAULT_OPEN_METEO_MODELS,
    http: ReadOnlyJsonClient | None = None,
) -> Mapping[str, Any]:
    """Capture one immutable hourly model shadow snapshot.

    The route is intentionally useful before probability certification: exact
    rules and independent forecasts are persisted even when no certified
    calibration profile exists. In that state the prediction row has null
    probability fields, probability_publishable=false and an explicit blocker.
    """
    now = _parse_or_now(decision_time)
    if not (-90.0 <= float(forecast_latitude) <= 90.0):
        raise KalshiWeatherRuntimeError("FORECAST_LOCATION_INVALID", ("LATITUDE_OUT_OF_RANGE",), status_code=422)
    if not (-180.0 <= float(forecast_longitude) <= 180.0):
        raise KalshiWeatherRuntimeError("FORECAST_LOCATION_INVALID", ("LONGITUDE_OUT_OF_RANGE",), status_code=422)
    models = _normalize_models(open_meteo_models)

    owned_http = http is None
    http_client = http or ReadOnlyJsonClient()
    get_json = http_client.get_json
    persistence = KalshiWeatherPersistence(client)

    try:
        rules = KalshiContractRuleAcquirer(get_json).acquire(ticker, acquired_at=now)
        parsed = parse_hourly_temperature_rule(
            rules,
            index_city=index_city,
            expected_location=expected_location,
        )
        contract = parsed.to_contract_snapshot(rules)
        persistence.persist_rule_package(rules)

        nws = NwsAdapter(get_json)
        point_snapshot = nws.point_metadata(
            float(forecast_latitude),
            float(forecast_longitude),
            retrieved_at=now,
        )
        forecast_hourly_url = _forecast_hourly_url(point_snapshot.payload)
        nws_hourly = nws.hourly_forecast(forecast_hourly_url, retrieved_at=now)

        target = _parse_utc(parsed.observation_time_utc)
        target_date = target.date().isoformat()
        open_meteo = OpenMeteoAdapter(get_json).multi_model_hourly_temperatures(
            float(forecast_latitude),
            float(forecast_longitude),
            target_date,
            target_date,
            models,
            retrieved_at=now,
        )

        point_id = source_snapshot_id(point_snapshot)
        nws_id = source_snapshot_id(nws_hourly)
        om_id = source_snapshot_id(open_meteo)
        persistence.persist_source_snapshot(
            rule_snapshot_id=contract.rule_snapshot_id,
            source_snapshot_id=point_id,
            snapshot=point_snapshot,
        )
        persistence.persist_source_snapshot(
            rule_snapshot_id=contract.rule_snapshot_id,
            source_snapshot_id=nws_id,
            snapshot=nws_hourly,
        )
        persistence.persist_source_snapshot(
            rule_snapshot_id=contract.rule_snapshot_id,
            source_snapshot_id=om_id,
            snapshot=open_meteo,
        )

        evidence = build_hourly_weather_evidence(
            contract=contract,
            analysis_time=now,
            nws_snapshot=nws_hourly,
            open_meteo_snapshot=open_meteo,
            settlement_source_verified=True,
            settlement_location_verified=True,
        )

        lead_bucket = lead_time_bucket(now, parsed.observation_time_utc)
        calibration_row = persistence.load_latest_certified_calibration(
            station_id=contract.settlement_location_code or f"KALSHI_WEATHER_INDEX:{index_city}",
            lane=contract.lane,
            lead_time_bucket=lead_bucket,
            fitted_before=now,
        )
        blockers: list[str] = []
        warnings: list[str] = []
        calibration_profile_id: str | None = None
        if calibration_row is None:
            probability = _missing_probability("CALIBRATION_PROFILE_UNAVAILABLE")
            blockers.append("CALIBRATION_PROFILE_UNAVAILABLE")
        else:
            calibration_profile_id, calibration = calibration_row
            probability = WeatherProbabilityCore().build(
                contract=contract,
                evidence=evidence,
                calibration=calibration,
            )

        capability = persistence.load_runtime_capability()
        capability_available = str(capability.get("capability_status") or "").upper() == "AVAILABLE"
        if not capability_available:
            blockers.append("KALSHI_WEATHER_PROBABILITY_CAPABILITY_UNAVAILABLE")
        if not probability.calibrated:
            blockers.append("IMPLEMENTATION_NOT_CERTIFIED")

        market_evidence = None
        market = _held_market(now)
        try:
            market_evidence = KalshiPublicMarketAdapter(get_json).snapshot(contract.ticker, retrieved_at=now)
            market = MarketSnapshot(
                yes_price=market_evidence.yes_best_ask,
                no_price=market_evidence.no_best_ask,
                price_time=market_evidence.retrieved_at,
                market_open=market_evidence.market_open,
                orderbook_nonempty=market_evidence.orderbook_nonempty,
                executable_price_verified=(market_evidence.yes_best_ask is not None or market_evidence.no_best_ask is not None),
                fee_known=False,
                fee_per_share=None,
                friction_model_verified=False,
                yes_effective_break_even=None,
                no_effective_break_even=None,
            )
            blockers.append("FEE_AND_FRICTION_NOT_EVALUATED_IN_SHADOW_CAPTURE")
        except Exception as exc:
            blockers.append(f"MARKET_SNAPSHOT_UNAVAILABLE:{type(exc).__name__}")

        terminal = evaluate_weather_contract(
            contract=contract,
            evidence=evidence,
            probability=probability,
            market=market,
        )
        blockers.extend(terminal.blockers)
        warnings.extend(terminal.warnings)

        publishable = bool(
            capability_available
            and probability.calibrated
            and terminal.probability_publishable
        )
        if not publishable:
            blockers.append("SHADOW_ONLY_NO_PUBLICATION")

        prediction_payload = {
            "ticker": contract.ticker,
            "rule_snapshot_id": contract.rule_snapshot_id,
            "decision_time": now,
            "source_snapshot_ids": list(evidence.source_snapshot_ids),
            "model_version": MODEL_VERSION,
            "lead_time_bucket": lead_bucket,
        }
        prediction_id = content_id("kalshi-weather-prediction", prediction_payload)
        model_payload = {
            "contract": asdict(contract),
            "forecast": dict(evidence.notes),
            "forecast_latitude": float(forecast_latitude),
            "forecast_longitude": float(forecast_longitude),
            "nws_point_snapshot_id": point_id,
            "lead_time_bucket": lead_bucket,
            "probability_source": probability.probability_source,
            "capability": dict(capability),
            "terminal": asdict(terminal),
            "market_price_used_as_model_input": False,
            "can_execute": False,
        }
        persistence.persist_prediction(
            prediction_id=prediction_id,
            rule_snapshot_id=contract.rule_snapshot_id,
            ticker=contract.ticker,
            decision_time=now,
            model_version=MODEL_VERSION,
            calibration_profile_id=calibration_profile_id,
            probability=probability,
            evidence=evidence,
            probability_status=terminal.code if publishable else "SHADOW_BLOCKED",
            probability_publishable=publishable,
            blockers=blockers,
            warnings=warnings,
            model_payload=model_payload,
        )

        market_snapshot_id = None
        if market_evidence is not None:
            market_snapshot_id = content_id(
                "kalshi-weather-market",
                {
                    "prediction_id": prediction_id,
                    "retrieved_at": market_evidence.retrieved_at,
                    "ticker": market_evidence.ticker,
                    "yes_best_bid": market_evidence.yes_best_bid,
                    "no_best_bid": market_evidence.no_best_bid,
                    "yes_best_ask": market_evidence.yes_best_ask,
                    "no_best_ask": market_evidence.no_best_ask,
                },
            )
            persistence.persist_market_snapshot(
                market_snapshot_id=market_snapshot_id,
                prediction_id=prediction_id,
                ticker=contract.ticker,
                retrieved_at=market_evidence.retrieved_at,
                market_status=market_evidence.market_status,
                yes_best_bid=market_evidence.yes_best_bid,
                no_best_bid=market_evidence.no_best_bid,
                market=market,
                raw_market=market_evidence.source_market,
                raw_orderbook=market_evidence.source_orderbook,
            )

        return {
            "status": "SHADOW_CAPTURED" if not publishable else terminal.status,
            "prediction_id": prediction_id,
            "rule_snapshot_id": contract.rule_snapshot_id,
            "source_snapshot_ids": list(evidence.source_snapshot_ids),
            "market_snapshot_id": market_snapshot_id,
            "ticker": contract.ticker,
            "index_city": parsed.index_city,
            "target_time_utc": parsed.observation_time_utc,
            "lead_time_bucket": lead_bucket,
            "central_estimate_f": evidence.central_estimate,
            "disagreement_f": evidence.disagreement_magnitude,
            "p_yes": probability.p_yes if publishable else None,
            "p_no": probability.p_no if publishable else None,
            "probability_status": terminal.code if publishable else "SHADOW_BLOCKED",
            "probability_publishable": publishable,
            "blockers": list(dict.fromkeys(blockers)),
            "warnings": list(dict.fromkeys(warnings)),
            "capability_status": capability.get("capability_status") or "UNAVAILABLE",
            "can_execute": False,
        }
    finally:
        if owned_http:
            http_client.close()


def settle_hourly_prediction(
    *,
    client,
    prediction_id: str,
    index_city: str,
    settled_at: str | None = None,
    http: ReadOnlyJsonClient | None = None,
) -> Mapping[str, Any]:
    """Grade one shadow prediction against the exact canonical index point."""
    now = _parse_or_now(settled_at)
    persistence = KalshiWeatherPersistence(client)
    if persistence.outcome_exists_for_prediction(prediction_id):
        raise KalshiWeatherRuntimeError("OUTCOME_ALREADY_RECORDED", (prediction_id,), status_code=409)
    prediction = persistence.load_prediction(prediction_id)
    if not prediction:
        raise KalshiWeatherRuntimeError("PREDICTION_NOT_FOUND", (prediction_id,), status_code=404)
    rule = persistence.load_rule(str(prediction.get("rule_snapshot_id") or ""))
    if not rule:
        raise KalshiWeatherRuntimeError("RULE_SNAPSHOT_NOT_FOUND", (str(prediction.get("rule_snapshot_id") or ""),), status_code=409)

    model_payload = prediction.get("model_payload")
    contract = model_payload.get("contract") if isinstance(model_payload, Mapping) else None
    if not isinstance(contract, Mapping):
        raise KalshiWeatherRuntimeError("PREDICTION_CONTRACT_PAYLOAD_INVALID", ("CONTRACT_MISSING",), status_code=409)
    observation_window = str(contract.get("observation_window") or "")
    prefix = "POINT_IN_TIME:"
    if not observation_window.startswith(prefix):
        raise KalshiWeatherRuntimeError("PREDICTION_CONTRACT_PAYLOAD_INVALID", ("POINT_IN_TIME_REQUIRED",), status_code=409)
    target = _parse_utc(observation_window[len(prefix):])
    target_ms = int(target.timestamp() * 1000)
    if _parse_utc(now) <= target:
        raise KalshiWeatherRuntimeError("SETTLEMENT_NOT_READY", ("TARGET_TIME_NOT_PASSED",), status_code=409)

    owned_http = http is None
    http_client = http or ReadOnlyJsonClient()
    try:
        snapshot = KalshiWeatherIndexAdapter(http_client.get_json).snapshot(
            index_city,
            retrieved_at=now,
            detailed=True,
        )
    finally:
        if owned_http:
            http_client.close()

    points = [point for point in snapshot.points if point.timestamp_ms == target_ms]
    if len(points) != 1:
        raise KalshiWeatherRuntimeError("SETTLEMENT_POINT_UNAVAILABLE", (f"TARGET_MS:{target_ms}",), status_code=409)
    point = points[0]
    if point.status != "normal" or point.value_f is None:
        raise KalshiWeatherRuntimeError(
            "SETTLEMENT_POINT_NOT_FINAL_QUALITY",
            (f"STATUS:{point.status}",),
            status_code=409,
        )

    value = float(point.value_f)
    yes_outcome = _contract_yes_outcome(contract, value)
    outcome_id = content_id(
        "kalshi-weather-outcome",
        {
            "prediction_id": prediction_id,
            "target_ms": target_ms,
            "settled_value": value,
            "yes_outcome": yes_outcome,
        },
    )
    persistence.persist_outcome(
        outcome_id=outcome_id,
        prediction_id=prediction_id,
        settled_at=now,
        settlement_source_name=str(rule.get("settlement_source_name") or ""),
        settlement_source_url=rule.get("settlement_source_url"),
        settled_value=value,
        yes_outcome=yes_outcome,
        settlement_payload={
            "index_city": snapshot.city,
            "config_version": snapshot.config_version,
            "target_timestamp_ms": target_ms,
            "point": {
                "timestamp_ms": point.timestamp_ms,
                "value_f": str(point.value_f),
                "status": point.status,
                "contributors": point.contributors,
                "receipt_basis": point.receipt_basis,
            },
            "raw_index_snapshot": dict(snapshot.raw),
            "can_execute": False,
        },
        p_yes=prediction.get("p_yes"),
    )
    return {
        "status": "SETTLED",
        "outcome_id": outcome_id,
        "prediction_id": prediction_id,
        "target_time_utc": target.isoformat().replace("+00:00", "Z"),
        "settled_value_f": value,
        "yes_outcome": yes_outcome,
        "brier_eligible": prediction.get("p_yes") is not None,
        "probability_publishable": False,
        "can_execute": False,
    }


def lead_time_bucket(analysis_time: str, target_time: str) -> str:
    delta_h = (_parse_utc(target_time) - _parse_utc(analysis_time)).total_seconds() / 3600.0
    if delta_h < 0:
        raise KalshiWeatherRuntimeError("TARGET_BEFORE_ANALYSIS", (f"DELTA_H:{delta_h:.3f}",), status_code=422)
    if delta_h < 1:
        return "H0"
    if delta_h < 2:
        return "H1"
    if delta_h < 3:
        return "H2"
    if delta_h < 6:
        return "H3_5"
    if delta_h < 13:
        return "H6_12"
    if delta_h < 25:
        return "H13_24"
    return "H25_PLUS"


def _forecast_hourly_url(payload: Mapping[str, Any]) -> str:
    props = payload.get("properties") if isinstance(payload, Mapping) else None
    url = str(props.get("forecastHourly") or "").strip() if isinstance(props, Mapping) else ""
    if not url.startswith("https://api.weather.gov/"):
        raise KalshiWeatherRuntimeError("NWS_POINT_METADATA_INVALID", ("FORECAST_HOURLY_URL_MISSING_OR_UNTRUSTED",), status_code=409)
    return url


def _normalize_models(values: Sequence[str]) -> tuple[str, ...]:
    models = tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    if not models:
        raise KalshiWeatherRuntimeError("OPEN_METEO_MODELS_INVALID", ("MODEL_SET_EMPTY",), status_code=422)
    unsupported = tuple(model for model in models if model not in ALLOWED_OPEN_METEO_MODELS)
    if unsupported:
        raise KalshiWeatherRuntimeError("OPEN_METEO_MODELS_INVALID", tuple(f"UNSUPPORTED_MODEL:{m}" for m in unsupported), status_code=422)
    return models


def _contract_yes_outcome(contract: Mapping[str, Any], value: float) -> bool:
    lower = contract.get("threshold_lower")
    upper = contract.get("threshold_upper")
    lower_inclusive = bool(contract.get("lower_inclusive", True))
    upper_inclusive = bool(contract.get("upper_inclusive", True))
    if lower is None and upper is None:
        raise KalshiWeatherRuntimeError("SETTLEMENT_RULE_INVALID", ("THRESHOLDS_MISSING",), status_code=409)
    if lower is not None:
        lower_ok = value >= float(lower) if lower_inclusive else value > float(lower)
    else:
        lower_ok = True
    if upper is not None:
        upper_ok = value <= float(upper) if upper_inclusive else value < float(upper)
    else:
        upper_ok = True
    return bool(lower_ok and upper_ok)


def _held_market(now: str) -> MarketSnapshot:
    return MarketSnapshot(
        yes_price=None,
        no_price=None,
        price_time=now,
        market_open=False,
        orderbook_nonempty=False,
        executable_price_verified=False,
        fee_known=False,
        friction_model_verified=False,
    )


def _missing_probability(reason: str) -> ProbabilityPackage:
    return ProbabilityPackage(
        p_yes=None,
        p_no=None,
        central_estimate=None,
        lower_bound_yes=None,
        upper_bound_yes=None,
        threshold_distance=None,
        calibration_method=None,
        probability_source=reason,
        market_price_used_as_input=False,
        coherent=False,
        calibrated=False,
    )


def _parse_or_now(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return _parse_utc(value).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise KalshiWeatherRuntimeError("TIMESTAMP_INVALID", (str(value),), status_code=422) from exc
    if parsed.tzinfo is None:
        raise KalshiWeatherRuntimeError("TIMESTAMP_INVALID", ("TIMEZONE_REQUIRED",), status_code=422)
    return parsed.astimezone(timezone.utc)
