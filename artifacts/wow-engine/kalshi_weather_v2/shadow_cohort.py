from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any, Callable, Mapping, Sequence

from .calibration_fit import ForecastResidual, fit_candidate_calibration_profile
from .contract_rule_acquisition import KalshiContractRuleAcquirer
from .hourly_rule_semantics import ParsedHourlyTemperatureRule, parse_hourly_temperature_rule
from .http_client import ReadOnlyJsonClient
from .market_discovery import KalshiWeatherMarketDiscovery
from .persistence import content_id
from .runtime import MODEL_VERSION, capture_hourly_shadow, lead_time_bucket, settle_hourly_prediction
from .supplemental_capture import capture_hourly_supplemental_sources


@dataclass(frozen=True)
class HourlyCohortTarget:
    index_city: str
    expected_location: str
    forecast_latitude: float
    forecast_longitude: float
    forecast_reference_note: str
    enabled: bool = True


# First empirical cohort is intentionally narrow. The coordinate is a forecast
# reference point only; it is never represented as the Kalshi settlement site.
DEFAULT_HOURLY_COHORT_TARGETS = (
    HourlyCohortTarget(
        index_city="miami",
        expected_location="Miami",
        forecast_latitude=25.7617,
        forecast_longitude=-80.1918,
        forecast_reference_note="Miami city reference for gridded forecasts; settlement identity remains KALSHI_WEATHER_INDEX:miami.",
    ),
)

CAPTURE_BUCKETS = frozenset({"H0", "H1", "H2", "H3_5", "H6_12", "H13_24", "H25_PLUS"})
SETTLEMENT_GRACE_MINUTES = 10


@dataclass(frozen=True)
class ParsedCohortContract:
    target: HourlyCohortTarget
    ticker: str
    parsed: ParsedHourlyTemperatureRule


@dataclass(frozen=True)
class CohortRunResult:
    status: str
    targets_checked: int
    contracts_discovered: int
    samples_captured: int
    samples_skipped_existing: int
    supplemental_snapshots_captured: int
    predictions_settled: int
    capture_failures: tuple[str, ...]
    supplemental_failures: tuple[str, ...]
    settlement_failures: tuple[str, ...]
    probability_publishable: bool = False
    can_execute: bool = False


def run_hourly_shadow_cohort_once(
    *,
    db_client_fn: Callable[[], object],
    targets: Sequence[HourlyCohortTarget] = DEFAULT_HOURLY_COHORT_TARGETS,
    now: str | None = None,
) -> CohortRunResult:
    decision_time = _iso(_parse_or_now(now))
    db = db_client_fn()
    existing_keys = _existing_sample_keys(db)
    capture_failures: list[str] = []
    supplemental_failures: list[str] = []
    settlement_failures: list[str] = []
    contracts_discovered = 0
    captured = 0
    skipped = 0
    supplemental_captured = 0

    http = ReadOnlyJsonClient()
    try:
        for target in targets:
            if not target.enabled:
                continue
            try:
                contracts = _discover_target_contracts(http, target, decision_time)
            except Exception as exc:
                capture_failures.append(f"{target.index_city}:DISCOVERY:{type(exc).__name__}:{getattr(exc, 'code', '')}")
                continue
            contracts_discovered += len(contracts)

            by_target: dict[str, list[ParsedCohortContract]] = {}
            for item in contracts:
                by_target.setdefault(item.parsed.observation_time_utc, []).append(item)

            for target_time, items in sorted(by_target.items()):
                try:
                    bucket = lead_time_bucket(decision_time, target_time)
                except Exception as exc:
                    capture_failures.append(f"{target.index_city}:{target_time}:BUCKET:{type(exc).__name__}")
                    continue
                if bucket not in CAPTURE_BUCKETS:
                    continue
                sample_key = cohort_sample_key(target.index_city, target_time, bucket)
                if sample_key in existing_keys:
                    skipped += 1
                    continue

                # One threshold contract per weather target/bucket is enough to
                # generate an unbiased temperature residual. Threshold siblings
                # would duplicate the exact same forecast error sample.
                chosen = min(items, key=lambda item: item.ticker)
                try:
                    capture = capture_hourly_shadow(
                        client=db,
                        ticker=chosen.ticker,
                        index_city=target.index_city,
                        expected_location=target.expected_location,
                        forecast_latitude=target.forecast_latitude,
                        forecast_longitude=target.forecast_longitude,
                        decision_time=decision_time,
                        http=http,
                    )
                except Exception as exc:
                    capture_failures.append(
                        f"{target.index_city}:{target_time}:{bucket}:{chosen.ticker}:{type(exc).__name__}:{getattr(exc, 'code', '')}"
                    )
                    continue

                captured += 1
                existing_keys.add(sample_key)

                # Supplemental sources are evidence-only. Failure here never
                # invalidates an otherwise valid core NWS/Open-Meteo capture.
                try:
                    supplemental = capture_hourly_supplemental_sources(
                        client=db,
                        rule_snapshot_id=str(capture["rule_snapshot_id"]),
                        forecast_latitude=target.forecast_latitude,
                        forecast_longitude=target.forecast_longitude,
                        target_time_utc=target_time,
                        retrieved_at=decision_time,
                        http=http,
                    )
                    supplemental_captured += len(supplemental.get("source_snapshot_ids") or [])
                    supplemental_failures.extend(
                        f"{target.index_city}:{target_time}:{failure}"
                        for failure in (supplemental.get("failures") or [])
                    )
                except Exception as exc:
                    supplemental_failures.append(
                        f"{target.index_city}:{target_time}:SUPPLEMENTAL:{type(exc).__name__}:{getattr(exc, 'code', '')}"
                    )

        settled = _settle_ready_predictions(db, http, decision_time, settlement_failures)
    finally:
        http.close()

    return CohortRunResult(
        status="EMPIRICAL_SHADOW_COHORT_PASS" if not capture_failures else "EMPIRICAL_SHADOW_COHORT_PARTIAL",
        targets_checked=sum(1 for target in targets if target.enabled),
        contracts_discovered=contracts_discovered,
        samples_captured=captured,
        samples_skipped_existing=skipped,
        supplemental_snapshots_captured=supplemental_captured,
        predictions_settled=settled,
        capture_failures=tuple(capture_failures),
        supplemental_failures=tuple(supplemental_failures),
        settlement_failures=tuple(settlement_failures),
    )


async def run_shadow_cohort_loop(
    *,
    db_client_fn: Callable[[], object],
    logger: logging.Logger,
    interval_seconds: int = 900,
) -> None:
    interval = max(300, int(interval_seconds))
    logger.warning(
        "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=STARTED interval_seconds=%s probability_publishable=false can_execute=false",
        interval,
    )
    while True:
        try:
            result = await asyncio.to_thread(run_hourly_shadow_cohort_once, db_client_fn=db_client_fn)
            logger.warning(
                "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=%s targets=%s discovered=%s captured=%s skipped=%s supplemental=%s settled=%s capture_failures=%s supplemental_failures=%s settlement_failures=%s probability_publishable=false can_execute=false",
                result.status,
                result.targets_checked,
                result.contracts_discovered,
                result.samples_captured,
                result.samples_skipped_existing,
                result.supplemental_snapshots_captured,
                result.predictions_settled,
                len(result.capture_failures),
                len(result.supplemental_failures),
                len(result.settlement_failures),
            )
        except Exception as exc:
            logger.error(
                "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=FAILED error_type=%s probability_publishable=false can_execute=false",
                type(exc).__name__,
            )
        await asyncio.sleep(interval)


def build_calibration_report(*, client, minimum_n: int = 30) -> Mapping[str, Any]:
    predictions = _select_rows(client, "wow_kalshi_weather_predictions", limit=5000)
    outcomes = _select_rows(client, "wow_kalshi_weather_outcomes", limit=5000)
    outcome_by_prediction = {str(row.get("prediction_id")): row for row in outcomes if row.get("prediction_id")}

    groups: dict[tuple[str, str, str], dict[str, tuple[str, ForecastResidual]]] = {}
    for prediction in predictions:
        prediction_id = str(prediction.get("prediction_id") or "")
        outcome = outcome_by_prediction.get(prediction_id)
        if not outcome or prediction.get("central_estimate_f") is None or outcome.get("settled_value") is None:
            continue
        model_payload = prediction.get("model_payload") if isinstance(prediction.get("model_payload"), Mapping) else {}
        contract = model_payload.get("contract") if isinstance(model_payload.get("contract"), Mapping) else {}
        lane = str(contract.get("lane") or "").upper()
        if lane != "HOURLY_TEMPERATURE":
            continue
        station_id = str(contract.get("settlement_location_code") or "")
        observation_window = str(contract.get("observation_window") or "")
        prefix = "POINT_IN_TIME:"
        if not station_id or not observation_window.startswith(prefix):
            continue
        target_time = observation_window[len(prefix):]
        bucket = str(model_payload.get("lead_time_bucket") or "").upper()
        decision_time = _normalized_z(str(prediction.get("decision_time") or ""))
        settlement_time = _normalized_z(str(outcome.get("settled_at") or ""))
        if not bucket or not decision_time or not settlement_time:
            continue
        sample_key = cohort_sample_key(station_id.removeprefix("KALSHI_WEATHER_INDEX:"), target_time, bucket)
        residual = ForecastResidual(
            forecast_value_f=float(prediction["central_estimate_f"]),
            settled_value_f=float(outcome["settled_value"]),
            forecast_as_of=decision_time,
            settlement_time=settlement_time,
        )
        key = (station_id, lane, bucket)
        current = groups.setdefault(key, {}).get(sample_key)
        if current is None or decision_time < current[0]:
            groups[key][sample_key] = (decision_time, residual)

    reports: list[dict[str, Any]] = []
    for (station_id, lane, bucket), keyed_rows in sorted(groups.items()):
        rows = [item[1] for item in sorted(keyed_rows.values(), key=lambda item: item[0])]
        full_fit = fit_candidate_calibration_profile(
            rows,
            station_id=station_id,
            lane=lane,
            lead_time_bucket=bucket,
            minimum_n=minimum_n,
        )

        validation: dict[str, Any] = {
            "status": "FORWARD_VALIDATION_INSUFFICIENT",
            "train_n": 0,
            "validation_n": 0,
            "validation_mae_f": None,
            "validation_bias_f": None,
            "within_1sigma_rate": None,
        }
        if len(rows) >= minimum_n + 10:
            validation_n = max(10, int(round(len(rows) * 0.20)))
            train_rows = rows[:-validation_n]
            validation_rows = rows[-validation_n:]
            train_fit = fit_candidate_calibration_profile(
                train_rows,
                station_id=station_id,
                lane=lane,
                lead_time_bucket=bucket,
                minimum_n=minimum_n,
            )
            if train_fit.profile is not None:
                bias = float(train_fit.profile.bias_f)
                sigma = float(train_fit.profile.sigma_f)
                corrected_errors = [row.residual_f - bias for row in validation_rows]
                validation = {
                    "status": "FORWARD_VALIDATION_REPORTED",
                    "train_n": len(train_rows),
                    "validation_n": len(validation_rows),
                    "validation_mae_f": mean(abs(value) for value in corrected_errors),
                    "validation_bias_f": mean(corrected_errors),
                    "within_1sigma_rate": mean(1.0 if abs(value) <= sigma else 0.0 for value in corrected_errors),
                }

        reports.append(
            {
                "station_id": station_id,
                "lane": lane,
                "lead_time_bucket": bucket,
                "unique_sample_n": len(rows),
                "candidate_status": full_fit.status,
                "candidate_blockers": list(full_fit.blockers),
                "candidate_profile": asdict(full_fit.profile) if full_fit.profile is not None else None,
                "forward_validation": validation,
                "certification_ready": False,
                "certification_note": "Research candidate only. Separate acceptance thresholds and governance approval are required before certified=true or capability promotion.",
            }
        )

    return {
        "status": "EMPIRICAL_CALIBRATION_REPORT",
        "minimum_candidate_n": minimum_n,
        "groups": reports,
        "probability_publishable": False,
        "capability_promotion_allowed": False,
        "can_execute": False,
    }


def cohort_sample_key(index_city: str, target_time_utc: str, bucket: str) -> str:
    return content_id(
        "kalshi-weather-cohort-sample",
        {
            "index_city": str(index_city).strip().lower(),
            "target_time_utc": _iso(_parse_utc(target_time_utc)),
            "lead_time_bucket": str(bucket).strip().upper(),
            "model_version": MODEL_VERSION,
        },
    )


def _discover_target_contracts(
    http: ReadOnlyJsonClient,
    target: HourlyCohortTarget,
    decision_time: str,
) -> tuple[ParsedCohortContract, ...]:
    discovery = KalshiWeatherMarketDiscovery(http.get_json)
    acquirer = KalshiContractRuleAcquirer(http.get_json)
    out: list[ParsedCohortContract] = []
    for series in discovery.candidate_series(expected_location=target.expected_location):
        for market in discovery.open_markets(series_ticker=series.ticker):
            ticker = str(market.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            try:
                rules = acquirer.acquire(ticker, acquired_at=decision_time)
                parsed = parse_hourly_temperature_rule(
                    rules,
                    index_city=target.index_city,
                    expected_location=target.expected_location,
                )
            except Exception:
                continue
            if _parse_utc(parsed.observation_time_utc) <= _parse_utc(decision_time):
                continue
            out.append(ParsedCohortContract(target=target, ticker=ticker, parsed=parsed))
    return tuple(out)


def _existing_sample_keys(client) -> set[str]:
    keys: set[str] = set()
    for prediction in _select_rows(client, "wow_kalshi_weather_predictions", limit=5000):
        model_payload = prediction.get("model_payload") if isinstance(prediction.get("model_payload"), Mapping) else {}
        contract = model_payload.get("contract") if isinstance(model_payload.get("contract"), Mapping) else {}
        if str(contract.get("lane") or "").upper() != "HOURLY_TEMPERATURE":
            continue
        station_id = str(contract.get("settlement_location_code") or "")
        window = str(contract.get("observation_window") or "")
        bucket = str(model_payload.get("lead_time_bucket") or "")
        prefix = "POINT_IN_TIME:"
        if not station_id.startswith("KALSHI_WEATHER_INDEX:") or not window.startswith(prefix) or not bucket:
            continue
        keys.add(cohort_sample_key(station_id.split(":", 1)[1], window[len(prefix):], bucket))
    return keys


def _settle_ready_predictions(client, http: ReadOnlyJsonClient, now: str, failures: list[str]) -> int:
    outcome_prediction_ids = {
        str(row.get("prediction_id"))
        for row in _select_rows(client, "wow_kalshi_weather_outcomes", limit=5000)
        if row.get("prediction_id")
    }
    settled = 0
    cutoff = _parse_utc(now) - timedelta(minutes=SETTLEMENT_GRACE_MINUTES)
    for prediction in _select_rows(client, "wow_kalshi_weather_predictions", limit=5000):
        prediction_id = str(prediction.get("prediction_id") or "")
        if not prediction_id or prediction_id in outcome_prediction_ids:
            continue
        model_payload = prediction.get("model_payload") if isinstance(prediction.get("model_payload"), Mapping) else {}
        contract = model_payload.get("contract") if isinstance(model_payload.get("contract"), Mapping) else {}
        if str(contract.get("lane") or "").upper() != "HOURLY_TEMPERATURE":
            continue
        window = str(contract.get("observation_window") or "")
        station_id = str(contract.get("settlement_location_code") or "")
        prefix = "POINT_IN_TIME:"
        if not window.startswith(prefix) or not station_id.startswith("KALSHI_WEATHER_INDEX:"):
            continue
        target_time = _parse_utc(window[len(prefix):])
        if target_time > cutoff:
            continue
        index_city = station_id.split(":", 1)[1]
        try:
            settle_hourly_prediction(
                client=client,
                prediction_id=prediction_id,
                index_city=index_city,
                settled_at=now,
                http=http,
            )
        except Exception as exc:
            failures.append(f"{prediction_id}:{type(exc).__name__}:{getattr(exc, 'code', '')}")
            continue
        settled += 1
        outcome_prediction_ids.add(prediction_id)
    return settled


def _select_rows(client, table: str, *, limit: int) -> list[Mapping[str, Any]]:
    try:
        result = client.table(table).select("*").limit(limit).execute()
    except Exception:
        return []
    return [dict(row) for row in (result.data or []) if isinstance(row, Mapping)]


def _parse_or_now(value: str | None) -> datetime:
    return datetime.now(timezone.utc) if value is None else _parse_utc(value)


def _parse_utc(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return parsed.astimezone(timezone.utc)


def _normalized_z(value: str) -> str | None:
    try:
        return _iso(_parse_utc(value))
    except Exception:
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
