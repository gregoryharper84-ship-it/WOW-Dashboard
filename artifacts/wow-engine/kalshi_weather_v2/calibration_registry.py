"""Research-candidate calibration registry for Kalshi Weather V2.

This module closes the gap between immutable residual evidence and a
reproducible numeric probability:

    immutable predictions + official outcomes
        -> unique target/bucket residuals
        -> candidate (uncertified) station/lane/lead-time profile
        -> numeric research P(YES)/P(NO) recorded at decision time
        -> Brier / log-loss scoring against the official settlement

Nothing here can certify a profile, publish a probability, or authorize
execution. Candidate profiles are persisted with ``certified=False`` and are
only readable by the shadow research lane; the certified-profile lookup used by
publication stays separate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .calibration_fit import ForecastResidual, fit_candidate_calibration_profile
from .persistence import KalshiWeatherPersistence, KalshiWeatherPersistenceError, content_id


MINIMUM_CANDIDATE_SAMPLES = 30
ROW_LIMIT = 5000
POINT_IN_TIME_PREFIX = "POINT_IN_TIME:"

SETTLEMENT_IDENTITY_STATION = "SETTLEMENT_STATION"
SETTLEMENT_IDENTITY_INDEX_CITY = "INDEX_CITY"
SETTLEMENT_IDENTITY_UNVERIFIED = "UNVERIFIED"


def settlement_identity(contract: Mapping[str, Any]) -> tuple[str, str]:
    """Return ``(grouping_id, identity_basis)`` for one contract snapshot.

    A Kalshi Weather Index city is the settlement *object* for these contracts;
    it is not evidence of an exact physical station. The basis is carried with
    the sample so a city-level code can never be read as station identity, and
    station-specific promotion stays blocked until a real station id exists.
    """
    contract = contract if isinstance(contract, Mapping) else {}
    station_id = str(contract.get("settlement_station_id") or "").strip()
    location_code = str(contract.get("settlement_location_code") or "").strip()
    if location_code:
        if station_id:
            basis = SETTLEMENT_IDENTITY_STATION
        elif location_code.startswith("KALSHI_WEATHER_INDEX:"):
            basis = SETTLEMENT_IDENTITY_INDEX_CITY
        else:
            basis = SETTLEMENT_IDENTITY_UNVERIFIED
        return location_code, basis
    if station_id:
        return station_id, SETTLEMENT_IDENTITY_STATION
    return "", SETTLEMENT_IDENTITY_UNVERIFIED


@dataclass(frozen=True)
class ResidualSample:
    sample_key: str
    decision_time: str
    settlement_time: str
    residual: ForecastResidual


def collect_unique_residual_samples(
    predictions: Iterable[Mapping[str, Any]],
    outcomes: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str, str], list[ResidualSample]]:
    """Group immutable predictions into one residual per exact weather target.

    Threshold siblings for the same index location/target timestamp/lead bucket
    describe one forecast error, not several. The earliest decision timestamp is
    kept so a later revision can never improve the research sample.
    """
    outcome_by_prediction = {
        str(row.get("prediction_id")): row
        for row in outcomes
        if isinstance(row, Mapping) and row.get("prediction_id")
    }
    groups: dict[tuple[str, str, str], dict[str, ResidualSample]] = {}

    for prediction in predictions:
        if not isinstance(prediction, Mapping):
            continue
        prediction_id = str(prediction.get("prediction_id") or "")
        outcome = outcome_by_prediction.get(prediction_id)
        if not prediction_id or not outcome:
            continue
        payload = prediction.get("model_payload")
        payload = payload if isinstance(payload, Mapping) else {}
        contract = payload.get("contract")
        contract = contract if isinstance(contract, Mapping) else {}
        lane = str(contract.get("lane") or "").strip().upper()
        station_id, _identity_basis = settlement_identity(contract)
        window = str(contract.get("observation_window") or "").strip()
        bucket = str(payload.get("lead_time_bucket") or "").strip().upper()
        if lane != "HOURLY_TEMPERATURE" or not station_id or not bucket:
            continue
        if not window.startswith(POINT_IN_TIME_PREFIX):
            continue
        forecast = _finite_float(prediction.get("central_estimate_f"))
        settled = _finite_float(outcome.get("settled_value"))
        decision_time = _utc_z(prediction.get("decision_time"))
        settlement_time = _utc_z(outcome.get("settled_at"))
        if forecast is None or settled is None or not decision_time or not settlement_time:
            continue
        if decision_time >= settlement_time:
            continue

        sample_key = content_id(
            "kalshi-weather-residual-sample",
            {
                "station_id": station_id,
                "target_time_utc": window[len(POINT_IN_TIME_PREFIX):],
                "lead_time_bucket": bucket,
            },
        )
        sample = ResidualSample(
            sample_key=sample_key,
            decision_time=decision_time,
            settlement_time=settlement_time,
            residual=ForecastResidual(
                forecast_value_f=forecast,
                settled_value_f=settled,
                forecast_as_of=decision_time,
                settlement_time=settlement_time,
            ),
        )
        keys = groups.setdefault((station_id, lane, bucket), {})
        existing = keys.get(sample_key)
        if existing is None or sample.decision_time < existing.decision_time:
            keys[sample_key] = sample

    return {
        key: sorted(value.values(), key=lambda item: item.decision_time)
        for key, value in groups.items()
    }


def fit_candidate_profile_sets(
    predictions: Iterable[Mapping[str, Any]],
    outcomes: Iterable[Mapping[str, Any]],
    *,
    minimum_n: int = MINIMUM_CANDIDATE_SAMPLES,
) -> list[dict[str, Any]]:
    """Fit one research candidate per station/lane/lead-time group."""
    groups = collect_unique_residual_samples(predictions, outcomes)
    fitted: list[dict[str, Any]] = []
    for (station_id, lane, bucket), samples in sorted(groups.items()):
        fit = fit_candidate_calibration_profile(
            (sample.residual for sample in samples),
            station_id=station_id,
            lane=lane,
            lead_time_bucket=bucket,
            minimum_n=minimum_n,
        )
        fitted.append(
            {
                "station_id": station_id,
                "lane": lane,
                "lead_time_bucket": bucket,
                "samples": samples,
                "fit": fit,
            }
        )
    return fitted


def persist_candidate_calibration_profiles(
    client,
    *,
    model_version: str,
    fitted_as_of: str,
    minimum_n: int = MINIMUM_CANDIDATE_SAMPLES,
) -> Mapping[str, Any]:
    """Fit and append research candidate profiles from immutable evidence.

    Every append is deterministic: identical residual evidence reuses the same
    ``calibration_profile_id`` (no-op retry), while new evidence necessarily
    produces a new id because the immutable ledger forbids in-place updates.
    """
    as_of = _utc_z(fitted_as_of)
    if not as_of:
        raise ValueError("fitted_as_of must be a timezone-aware timestamp")
    if not str(model_version).strip():
        raise ValueError("model_version is required")

    predictions, outcomes = load_scoring_evidence_rows(client)
    store = KalshiWeatherPersistence(client)

    persisted: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    for group in fit_candidate_profile_sets(predictions, outcomes, minimum_n=minimum_n):
        fit = group["fit"]
        samples: list[ResidualSample] = group["samples"]
        if fit.profile is None:
            held.append(
                {
                    "station_id": group["station_id"],
                    "lane": group["lane"],
                    "lead_time_bucket": group["lead_time_bucket"],
                    "unique_sample_n": len(samples),
                    "blockers": list(fit.blockers),
                }
            )
            continue
        if samples and samples[-1].decision_time >= as_of:
            held.append(
                {
                    "station_id": group["station_id"],
                    "lane": group["lane"],
                    "lead_time_bucket": group["lead_time_bucket"],
                    "unique_sample_n": len(samples),
                    "blockers": ["CALIBRATION_FIT_NOT_AFTER_SAMPLE_DECISION_TIME"],
                }
            )
            continue

        profile = fit.profile
        sample_evidence = [
            {
                "sample_key": sample.sample_key,
                "decision_time": sample.decision_time,
                "settlement_time": sample.settlement_time,
                "residual_f": round(sample.residual.residual_f, 9),
            }
            for sample in samples
        ]
        profile_id = content_id(
            "kalshi-weather-calibration-candidate",
            {
                "station_id": profile.station_id,
                "lane": profile.lane,
                "lead_time_bucket": profile.lead_time_bucket,
                "model_version": str(model_version),
                "method": profile.method,
                "samples": sample_evidence,
            },
        )
        evidence = {
            "profile_scope": "RESEARCH_CANDIDATE_UNCERTIFIED",
            "certified": False,
            "certification_required": True,
            "minimum_candidate_samples": minimum_n,
            "minimum_forward_validation_samples": 50,
            "unique_sample_n": len(samples),
            "latest_sample_decision_time": samples[-1].decision_time,
            "earliest_sample_decision_time": samples[0].decision_time,
            "fitted_as_of": as_of,
            "model_version": str(model_version),
            "residual_method": profile.method,
            "threshold_siblings_deduplicated": True,
            "lookahead_rejected": True,
            "probability_publishable": False,
            "capability_promotion_allowed": False,
            "can_execute": False,
        }
        existing = store.load_calibration_profile_by_id(profile_id)
        reused_existing = existing is not None
        if existing is not None:
            expected_identity = {
                "calibration_profile_id": profile_id,
                "station_id": profile.station_id,
                "lane": profile.lane,
                "lead_time_bucket": profile.lead_time_bucket,
                "model_version": str(model_version),
                "method": profile.method,
                "bias_f": profile.bias_f,
                "sigma_f": profile.sigma_f,
                "lower_sigma_f": profile.lower_sigma_f,
                "upper_sigma_f": profile.upper_sigma_f,
                "sample_n": profile.sample_n,
                "certified": False,
                "can_execute": False,
            }
            for key, expected_value in expected_identity.items():
                actual_value = existing.get(key)
                if isinstance(expected_value, float):
                    actual_number = _finite_float(actual_value)
                    matches = actual_number is not None and math.isclose(
                        actual_number, expected_value, rel_tol=1e-12, abs_tol=1e-12
                    )
                else:
                    matches = actual_value == expected_value
                if not matches:
                    raise KalshiWeatherPersistenceError(
                        "KALSHI_WEATHER_IDENTITY_COLLISION",
                        f"wow_kalshi_weather_calibration_profiles:{profile_id}:{key}",
                    )
            existing_evidence = existing.get("certification_evidence")
            if (
                not isinstance(existing_evidence, Mapping)
                or existing_evidence.get("profile_scope") != "RESEARCH_CANDIDATE_UNCERTIFIED"
                or existing_evidence.get("probability_publishable") is not False
            ):
                raise KalshiWeatherPersistenceError(
                    "KALSHI_WEATHER_IDENTITY_COLLISION",
                    f"wow_kalshi_weather_calibration_profiles:{profile_id}:certification_evidence",
                )
            row = dict(existing)
        else:
            row = store.persist_calibration_profile(
                calibration_profile_id=profile_id,
                profile=profile,
                model_version=str(model_version),
                fitted_as_of=as_of,
                certification_evidence=evidence,
            )

        scoring = _score_group(predictions, outcomes, group["station_id"], group["lane"], group["lead_time_bucket"])
        persisted.append(
            {
                "calibration_profile_id": str(row.get("calibration_profile_id") or profile_id),
                "station_id": profile.station_id,
                "lane": profile.lane,
                "lead_time_bucket": profile.lead_time_bucket,
                "unique_sample_n": profile.sample_n,
                "bias_f": profile.bias_f,
                "sigma_f": profile.sigma_f,
                "certified": False,
                "reused_existing": reused_existing,
                "scoring": scoring,
            }
        )

    return {
        "status": "CALIBRATION_CANDIDATES_PERSISTED" if persisted else "CALIBRATION_CANDIDATES_HELD",
        "model_version": str(model_version),
        "fitted_as_of": as_of,
        "minimum_candidate_samples": minimum_n,
        "profiles_persisted": persisted,
        "profiles_held": held,
        "probability_publishable": False,
        "capability_promotion_allowed": False,
        "can_execute": False,
    }


def probability_scoring_summary(
    predictions: Iterable[Mapping[str, Any]],
    outcomes: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Score recorded decision-time probabilities against official settlements.

    ``no_skill`` is the base-rate reference forecast for the same rows, so a
    candidate that adds nothing is visible rather than flattered.
    """
    outcome_by_prediction = {
        str(row.get("prediction_id")): row
        for row in outcomes
        if isinstance(row, Mapping) and row.get("prediction_id")
    }
    pooled: list[tuple[str, float, float]] = []
    for prediction in predictions:
        if not isinstance(prediction, Mapping):
            continue
        outcome = outcome_by_prediction.get(str(prediction.get("prediction_id") or ""))
        if not outcome:
            continue
        probability = _finite_float(prediction.get("p_yes"))
        yes_outcome = outcome.get("yes_outcome")
        if probability is None or not (0.0 < probability < 1.0):
            continue
        if not isinstance(yes_outcome, bool):
            continue
        payload = prediction.get("model_payload")
        payload = payload if isinstance(payload, Mapping) else {}
        bucket = str(payload.get("lead_time_bucket") or "").strip().upper()
        pooled.append((bucket, probability, 1.0 if yes_outcome else 0.0))

    buckets = sorted({bucket for bucket, _, _ in pooled})
    return {
        "status": "RESEARCH_PROBABILITY_SCORING",
        "sample_n": len(pooled),
        "pooled": _score(pooled),
        "by_lead_bucket": {
            bucket: _score([row for row in pooled if row[0] == bucket]) for bucket in buckets
        },
        "certified": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _score_group(
    predictions: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    station_id: str,
    lane: str,
    bucket: str,
) -> Mapping[str, Any]:
    subset = [
        prediction
        for prediction in predictions
        if _group_key(prediction) == (station_id, lane, bucket)
    ]
    summary = probability_scoring_summary(subset, outcomes)
    return summary["pooled"]


def _group_key(prediction: Mapping[str, Any]) -> tuple[str, str, str]:
    payload = prediction.get("model_payload")
    payload = payload if isinstance(payload, Mapping) else {}
    contract = payload.get("contract")
    contract = contract if isinstance(contract, Mapping) else {}
    grouping_id, _identity_basis = settlement_identity(contract)
    return (
        grouping_id,
        str(contract.get("lane") or "").strip().upper(),
        str(payload.get("lead_time_bucket") or "").strip().upper(),
    )


def _score(rows: Sequence[tuple[str, float, float]]) -> dict[str, Any]:
    if not rows:
        return {
            "sample_n": 0,
            "brier_score": None,
            "log_loss": None,
            "no_skill_brier_score": None,
            "no_skill_log_loss": None,
            "brier_skill_score": None,
        }
    count = len(rows)
    base_rate = sum(y for _, _, y in rows) / count
    brier = sum((probability - y) ** 2 for _, probability, y in rows) / count
    log_loss = sum(
        -(y * math.log(probability) + (1.0 - y) * math.log(1.0 - probability))
        for _, probability, y in rows
    ) / count
    no_skill_brier = sum((base_rate - y) ** 2 for _, _, y in rows) / count
    no_skill_log_loss = None
    if 0.0 < base_rate < 1.0:
        no_skill_log_loss = sum(
            -(y * math.log(base_rate) + (1.0 - y) * math.log(1.0 - base_rate))
            for _, _, y in rows
        ) / count
    skill = None
    if no_skill_brier > 0.0:
        skill = 1.0 - brier / no_skill_brier
    return {
        "sample_n": count,
        "brier_score": round(brier, 9),
        "log_loss": round(log_loss, 9),
        "no_skill_brier_score": round(no_skill_brier, 9),
        "no_skill_log_loss": round(no_skill_log_loss, 9) if no_skill_log_loss is not None else None,
        "brier_skill_score": round(skill, 9) if skill is not None else None,
        "yes_base_rate": round(base_rate, 9),
    }


def load_scoring_evidence_rows(
    client,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Load complete, deterministically ordered immutable scoring inputs."""
    return (
        _select_rows(client, "wow_kalshi_weather_predictions"),
        _select_rows(client, "wow_kalshi_weather_outcomes"),
    )


def _select_rows(client, table: str) -> list[Mapping[str, Any]]:
    primary_key = {
        "wow_kalshi_weather_predictions": "prediction_id",
        "wow_kalshi_weather_outcomes": "outcome_id",
    }.get(table)
    if primary_key is None:
        raise ValueError(f"unsupported calibration evidence table: {table}")
    try:
        result = (
            client.table(table)
            .select("*")
            .order(primary_key)
            .limit(ROW_LIMIT)
            .execute()
        )
    except Exception as exc:
        raise KalshiWeatherPersistenceError(
            "KALSHI_WEATHER_CALIBRATION_LEDGER_READ_FAILED",
            f"{table}:{type(exc).__name__}",
        ) from exc
    rows = [dict(row) for row in (result.data or []) if isinstance(row, Mapping)]
    if len(rows) >= ROW_LIMIT:
        raise KalshiWeatherPersistenceError(
            "KALSHI_WEATHER_CALIBRATION_INPUT_TRUNCATED",
            f"{table}:row_limit={ROW_LIMIT}",
        )
    return rows


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _utc_z(value: Any) -> str | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
