from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, median, stdev
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlencode

from .http_client import ReadOnlyJsonClient
from .persistence import content_id

KALSHI_API_BASE = "https://external-api.kalshi.com/trade-api/v2"
OPEN_METEO_SINGLE_RUN_BASE = "https://single-runs-api.open-meteo.com/v1/forecast"
REPLAY_MODEL_VERSION = "KALSHI_WEATHER_V2_OM_SINGLE_RUN_R2"
ACCEPTANCE_SPEC_VERSION = "KALSHI_WEATHER_R2_ACCEPTANCE_20260914"
REPLAY_LEAD_TIME_BUCKET = "H13_24"
REPLAY_DECISION_LEAD_HOURS = 18
SAFE_GLOBAL_RUN_LAG_HOURS = 6
TRAIN_N = 30
HOLDOUT_N = 20
TOTAL_N = TRAIN_N + HOLDOUT_N
MAX_ABS_HOLDOUT_MEAN_ERROR_F = 0.75
MAX_HOLDOUT_MAE_F = 2.5
MIN_ONE_SIGMA_COVERAGE = 0.50
MAX_ONE_SIGMA_COVERAGE = 0.85
MAX_BRIER_DEGRADATION = 0.01
MAX_BUCKET_CALIBRATION_GAP = 0.25
MIN_BUCKET_N = 10
MODEL_NAMES = ("gfs_seamless", "ecmwf_ifs025")


@dataclass(frozen=True)
class ReplayTarget:
    index_city: str
    expected_rule_location: str
    series_ticker: str
    latitude: float
    longitude: float

    @property
    def station_id(self) -> str:
        return f"KALSHI_WEATHER_INDEX:{self.index_city}"


REPLAY_TARGETS = (
    ReplayTarget("miami", "Miami, FL", "KXTEMPMIAH", 25.7617, -80.1918),
    ReplayTarget("chicago", "Chicago Metro Area", "KXTEMPCHIHS", 41.8781, -87.6298),
    ReplayTarget("la-coastal", "Coastal Los Angeles", "KXTEMPLAXHS", 33.9500, -118.4000),
)


@dataclass(frozen=True)
class PredicateResult:
    ticker: str
    strike_type: str
    floor_strike: float | None
    cap_strike: float | None
    yes_outcome: bool


@dataclass(frozen=True)
class SettledWeatherEvent:
    station_id: str
    series_ticker: str
    event_ticker: str
    observation_time_utc: str
    settlement_source_name: str
    settled_value_f: float
    predicates: tuple[PredicateResult, ...]
    raw_markets: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ReplaySample:
    sample_id: str
    station_id: str
    series_ticker: str
    event_ticker: str
    observation_time_utc: str
    settlement_source_name: str
    settled_value_f: float
    decision_time: str
    run_initialization: str
    safe_available_at: str
    gfs_f: float
    ecmwf_f: float
    central_estimate_f: float
    archive_source_url: str
    archive_payload_hash: str
    predicates: tuple[PredicateResult, ...]
    no_lookahead_verified: bool
    source_identity_verified: bool

    @property
    def residual_f(self) -> float:
        return self.central_estimate_f - self.settled_value_f


class HistoricalCertificationError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def replay_targets() -> tuple[ReplayTarget, ...]:
    return REPLAY_TARGETS


def select_safe_run_initialization(decision_time: str | datetime) -> datetime:
    decision = _dt(decision_time)
    cutoff = decision - timedelta(hours=SAFE_GLOBAL_RUN_LAG_HOURS)
    cycle_hour = (cutoff.hour // 6) * 6
    return cutoff.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)


def fetch_synoptic_regime_settled_events(
    *,
    target: ReplayTarget,
    http: ReadOnlyJsonClient,
    max_pages: int = 4,
    include_historical: bool = False,
) -> tuple[SettledWeatherEvent, ...]:
    """Fetch exact settled hourly events for the current Synoptic rule regime.

    Older AccuWeather events are deliberately rejected by the rule-source check
    rather than mixed into the current calibration population.
    """
    rows: dict[str, Mapping[str, Any]] = {}
    sources = [(f"{KALSHI_API_BASE}/markets", True)]
    if include_historical:
        sources.append((f"{KALSHI_API_BASE}/historical/markets", False))
    for base, live in sources:
        cursor = ""
        for _ in range(max_pages):
            params: dict[str, Any] = {"series_ticker": target.series_ticker, "limit": 1000}
            if live:
                params.update({"status": "settled", "mve_filter": "exclude"})
            if cursor:
                params["cursor"] = cursor
            payload = http.get_json(f"{base}?{urlencode(params)}")
            markets = payload.get("markets")
            if not isinstance(markets, Sequence) or isinstance(markets, (str, bytes, bytearray)):
                raise HistoricalCertificationError("KALSHI_MARKET_LIST_INVALID", target.series_ticker)
            for market in markets:
                if not isinstance(market, Mapping):
                    continue
                ticker = str(market.get("ticker") or "").strip().upper()
                if ticker:
                    rows[ticker] = dict(market)
            cursor = str(payload.get("cursor") or "").strip()
            if not cursor:
                break

    groups: dict[str, list[Mapping[str, Any]]] = {}
    for market in rows.values():
        event_ticker = str(market.get("event_ticker") or "").strip().upper()
        if event_ticker:
            groups.setdefault(event_ticker, []).append(market)

    accepted: list[SettledWeatherEvent] = []
    for event_ticker, markets in groups.items():
        try:
            accepted.append(_build_settled_event(target, event_ticker, markets))
        except HistoricalCertificationError:
            continue
    accepted.sort(key=lambda row: row.observation_time_utc)
    return tuple(accepted)


def build_replay_samples(
    *,
    target: ReplayTarget,
    events: Sequence[SettledWeatherEvent],
    http: ReadOnlyJsonClient,
    existing_observation_times: set[str] | None = None,
    max_new_samples: int | None = None,
) -> tuple[ReplaySample, ...]:
    """Replay the exact R2 model using archived model runs available pre-decision."""
    run_payloads: dict[str, Mapping[str, Any]] = {}
    out: list[ReplaySample] = []
    existing_times = set(existing_observation_times or set())
    limit = None if max_new_samples is None else max(0, int(max_new_samples))
    for event in sorted(events, key=lambda row: row.observation_time_utc):
        if event.observation_time_utc in existing_times:
            continue
        if limit is not None and len(out) >= limit:
            break
        observation = _dt(event.observation_time_utc)
        decision = observation - timedelta(hours=REPLAY_DECISION_LEAD_HOURS)
        run_init = select_safe_run_initialization(decision)
        safe_available = run_init + timedelta(hours=SAFE_GLOBAL_RUN_LAG_HOURS)
        if safe_available > decision:
            raise HistoricalCertificationError("REPLAY_LOOKAHEAD_GUARD_FAILED", event.event_ticker)
        run_key = _iso(run_init)
        payload = run_payloads.get(run_key)
        source_url = _single_run_url(target=target, run_init=run_init)
        if payload is None:
            payload = http.get_json(source_url)
            run_payloads[run_key] = payload
        gfs, ecmwf = _extract_model_temperatures(payload, observation)
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        central = float(median((gfs, ecmwf)))
        sample_payload = {
            "station_id": event.station_id,
            "event_ticker": event.event_ticker,
            "observation_time_utc": event.observation_time_utc,
            "model_version": REPLAY_MODEL_VERSION,
            "lead_time_bucket": REPLAY_LEAD_TIME_BUCKET,
            "decision_time": _iso(decision),
            "run_initialization": run_key,
        }
        out.append(
            ReplaySample(
                sample_id=content_id("kalshi-weather-replay-sample", sample_payload),
                station_id=event.station_id,
                series_ticker=event.series_ticker,
                event_ticker=event.event_ticker,
                observation_time_utc=event.observation_time_utc,
                settlement_source_name=event.settlement_source_name,
                settled_value_f=event.settled_value_f,
                decision_time=_iso(decision),
                run_initialization=run_key,
                safe_available_at=_iso(safe_available),
                gfs_f=gfs,
                ecmwf_f=ecmwf,
                central_estimate_f=central,
                archive_source_url=source_url,
                archive_payload_hash=payload_hash,
                predicates=event.predicates,
                no_lookahead_verified=safe_available <= decision,
                source_identity_verified=event.settlement_source_name == "Synoptic Data",
            )
        )
    return tuple(out)


def build_certification_report(samples: Sequence[ReplaySample]) -> Mapping[str, Any]:
    """Apply the frozen chronological certification gates to R2 samples."""
    deduped: dict[str, ReplaySample] = {}
    for sample in samples:
        key = f"{sample.station_id}|{sample.observation_time_utc}|{REPLAY_LEAD_TIME_BUCKET}|{REPLAY_MODEL_VERSION}"
        deduped.setdefault(key, sample)
    ordered = sorted(deduped.values(), key=lambda row: row.observation_time_utc)
    station_ids = {row.station_id for row in ordered}
    blockers: list[str] = []
    if len(station_ids) > 1:
        blockers.append("PROFILE_IDENTITY_MIXED_STATIONS")
    if any(not row.no_lookahead_verified for row in ordered):
        blockers.append("LOOKAHEAD_ROW_PRESENT")
    if any(not row.source_identity_verified for row in ordered):
        blockers.append("SETTLEMENT_SOURCE_IDENTITY_UNVERIFIED")
    if len(ordered) < TOTAL_N:
        return {
            "status": "CERTIFICATION_NOT_READY",
            "station_id": next(iter(station_ids)) if len(station_ids) == 1 else None,
            "model_version": REPLAY_MODEL_VERSION,
            "acceptance_spec_version": ACCEPTANCE_SPEC_VERSION,
            "lead_time_bucket": REPLAY_LEAD_TIME_BUCKET,
            "unique_sample_n": len(ordered),
            "required_sample_n": TOTAL_N,
            "remaining_sample_n": max(0, TOTAL_N - len(ordered)),
            "blockers": blockers + ["MINIMUM_50_UNIQUE_TARGETS_NOT_MET"],
            "promotion_eligible": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    frozen = ordered[:TOTAL_N]
    train = frozen[:TRAIN_N]
    holdout = frozen[TRAIN_N:TOTAL_N]
    train_residuals = [row.residual_f for row in train]
    bias = mean(train_residuals)
    sigma = stdev(train_residuals) if len(train_residuals) > 1 else 0.0
    if not math.isfinite(sigma) or sigma <= 0:
        blockers.append("TRAIN_SIGMA_INVALID")
        sigma = 0.000001
    corrected_errors = [row.residual_f - bias for row in holdout]
    holdout_mean_error = mean(corrected_errors)
    holdout_mae = mean(abs(value) for value in corrected_errors)
    one_sigma_coverage = mean(1.0 if abs(value) <= sigma else 0.0 for value in corrected_errors)

    residual_gates = {
        "absolute_holdout_mean_error": abs(holdout_mean_error) <= MAX_ABS_HOLDOUT_MEAN_ERROR_F,
        "holdout_mae": holdout_mae <= MAX_HOLDOUT_MAE_F,
        "one_sigma_coverage": MIN_ONE_SIGMA_COVERAGE <= one_sigma_coverage <= MAX_ONE_SIGMA_COVERAGE,
        "no_lookahead": not any(not row.no_lookahead_verified for row in frozen),
        "source_identity": not any(not row.source_identity_verified for row in frozen),
    }

    candidate_target_briers: list[float] = []
    baseline_target_briers: list[float] = []
    bucket_rows: dict[str, list[tuple[float, float]]] = {}
    baseline_sigma = math.sqrt(mean(value * value for value in train_residuals)) or sigma
    for sample in holdout:
        candidate_ps: list[tuple[float, float]] = []
        baseline_ps: list[tuple[float, float]] = []
        corrected_mu = sample.central_estimate_f - bias
        for predicate in sample.predicates:
            candidate_p = _predicate_probability(corrected_mu, sigma, predicate)
            baseline_p = _predicate_probability(sample.central_estimate_f, baseline_sigma, predicate)
            y = 1.0 if predicate.yes_outcome else 0.0
            candidate_ps.append((candidate_p, y))
            baseline_ps.append((baseline_p, y))
            bucket = _probability_bucket(candidate_p)
            bucket_rows.setdefault(bucket, []).append((candidate_p, y))
        if candidate_ps:
            candidate_target_briers.append(mean((p - y) ** 2 for p, y in candidate_ps))
            baseline_target_briers.append(mean((p - y) ** 2 for p, y in baseline_ps))

    candidate_brier = mean(candidate_target_briers) if candidate_target_briers else None
    baseline_brier = mean(baseline_target_briers) if baseline_target_briers else None
    calibration_buckets = []
    severe_bucket_miscalibration = False
    for bucket, rows in sorted(bucket_rows.items()):
        predicted = mean(p for p, _ in rows)
        observed = mean(y for _, y in rows)
        gap = abs(predicted - observed)
        severe = len(rows) >= MIN_BUCKET_N and gap > MAX_BUCKET_CALIBRATION_GAP
        severe_bucket_miscalibration = severe_bucket_miscalibration or severe
        calibration_buckets.append(
            {
                "bucket": bucket,
                "n": len(rows),
                "mean_predicted": predicted,
                "observed_rate": observed,
                "absolute_gap": gap,
                "severe": severe,
            }
        )

    probability_gates = {
        "brier_reported": candidate_brier is not None and baseline_brier is not None,
        "no_material_brier_degradation": (
            candidate_brier is not None
            and baseline_brier is not None
            and candidate_brier <= baseline_brier + MAX_BRIER_DEGRADATION
        ),
        "no_severe_bucket_miscalibration": not severe_bucket_miscalibration,
        "market_price_blind": True,
    }
    passed = all(residual_gates.values()) and all(probability_gates.values()) and not blockers
    if not passed:
        blockers.extend([f"RESIDUAL_GATE_FAILED:{name}" for name, ok in residual_gates.items() if not ok])
        blockers.extend([f"PROBABILITY_GATE_FAILED:{name}" for name, ok in probability_gates.items() if not ok])

    return {
        "status": "CERTIFICATION_ACCEPTANCE_PASS" if passed else "CERTIFICATION_ACCEPTANCE_FAIL",
        "station_id": frozen[0].station_id if frozen else None,
        "model_version": REPLAY_MODEL_VERSION,
        "acceptance_spec_version": ACCEPTANCE_SPEC_VERSION,
        "lead_time_bucket": REPLAY_LEAD_TIME_BUCKET,
        "unique_sample_n": len(ordered),
        "frozen_evaluation_sample_n": TOTAL_N,
        "train_n": TRAIN_N,
        "holdout_n": HOLDOUT_N,
        "train_window": [train[0].observation_time_utc, train[-1].observation_time_utc],
        "holdout_window": [holdout[0].observation_time_utc, holdout[-1].observation_time_utc],
        "bias_f": bias,
        "sigma_f": sigma,
        "lower_sigma_f": max(0.25, sigma * 0.85),
        "upper_sigma_f": sigma * 1.20,
        "holdout_mean_error_f": holdout_mean_error,
        "holdout_mae_f": holdout_mae,
        "one_sigma_coverage": one_sigma_coverage,
        "candidate_brier": candidate_brier,
        "baseline_brier": baseline_brier,
        "calibration_buckets": calibration_buckets,
        "residual_gates": residual_gates,
        "probability_gates": probability_gates,
        "blockers": list(dict.fromkeys(blockers)),
        "promotion_eligible": passed,
        "probability_publishable": False,
        "market_price_used_as_model_input": False,
        "can_execute": False,
    }


def persist_replay_samples(*, client: Any, samples: Sequence[ReplaySample]) -> int:
    persisted = 0
    for sample in samples:
        row = {
            "sample_id": sample.sample_id,
            "station_id": sample.station_id,
            "series_ticker": sample.series_ticker,
            "event_ticker": sample.event_ticker,
            "observation_time": sample.observation_time_utc,
            "settlement_source_name": sample.settlement_source_name,
            "settled_value_f": sample.settled_value_f,
            "model_version": REPLAY_MODEL_VERSION,
            "lead_time_bucket": REPLAY_LEAD_TIME_BUCKET,
            "decision_time": sample.decision_time,
            "run_initialization": sample.run_initialization,
            "safe_available_at": sample.safe_available_at,
            "gfs_f": sample.gfs_f,
            "ecmwf_f": sample.ecmwf_f,
            "central_estimate_f": sample.central_estimate_f,
            "archive_source_url": sample.archive_source_url,
            "archive_payload_hash": sample.archive_payload_hash,
            "predicates": [asdict(item) for item in sample.predicates],
            "no_lookahead_verified": sample.no_lookahead_verified,
            "source_identity_verified": sample.source_identity_verified,
            "can_execute": False,
        }
        existing = (
            client.table("wow_kalshi_weather_replay_samples")
            .select("*")
            .eq("sample_id", sample.sample_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if existing:
            _assert_materially_equal(existing[0], row)
            continue
        result = client.table("wow_kalshi_weather_replay_samples").insert(row).execute().data or []
        if not result:
            raise HistoricalCertificationError("REPLAY_SAMPLE_INSERT_UNCONFIRMED", sample.sample_id)
        persisted += 1
    return persisted


def load_replay_samples(*, client: Any, station_id: str) -> tuple[ReplaySample, ...]:
    rows = (
        client.table("wow_kalshi_weather_replay_samples")
        .select("*")
        .eq("station_id", station_id)
        .eq("model_version", REPLAY_MODEL_VERSION)
        .eq("lead_time_bucket", REPLAY_LEAD_TIME_BUCKET)
        .order("observation_time")
        .limit(5000)
        .execute()
        .data
        or []
    )
    return tuple(_sample_from_row(row) for row in rows if isinstance(row, Mapping))


def persist_certification_report(*, client: Any, report: Mapping[str, Any], evaluated_at: str | None = None) -> str:
    when = _iso(_dt(evaluated_at) if evaluated_at else datetime.now(timezone.utc))
    identity = {
        "station_id": report.get("station_id"),
        "model_version": REPLAY_MODEL_VERSION,
        "lead_time_bucket": REPLAY_LEAD_TIME_BUCKET,
        "train_window": report.get("train_window"),
        "holdout_window": report.get("holdout_window"),
        "status": report.get("status"),
        "unique_sample_n": report.get("unique_sample_n"),
    }
    run_id = content_id("kalshi-weather-certification-run", identity)
    row = {
        "certification_run_id": run_id,
        "station_id": report.get("station_id") or "UNRESOLVED",
        "model_version": REPLAY_MODEL_VERSION,
        "lead_time_bucket": REPLAY_LEAD_TIME_BUCKET,
        "evaluated_at": when,
        "train_n": int(report.get("train_n") or 0),
        "holdout_n": int(report.get("holdout_n") or 0),
        "status": str(report.get("status") or "UNKNOWN"),
        "promotion_eligible": bool(report.get("promotion_eligible")),
        "report": dict(report),
        "can_execute": False,
    }
    existing = (
        client.table("wow_kalshi_weather_certification_runs")
        .select("*")
        .eq("certification_run_id", run_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing:
        return run_id
    result = client.table("wow_kalshi_weather_certification_runs").insert(row).execute().data or []
    if not result:
        raise HistoricalCertificationError("CERTIFICATION_RUN_INSERT_UNCONFIRMED", run_id)
    return run_id


def run_certification_accelerator_once(
    *, db_client_fn: Callable[[], Any], target: ReplayTarget, max_new_samples: int = 4
) -> Mapping[str, Any]:
    db = db_client_fn()
    stored_before = load_replay_samples(client=db, station_id=target.station_id)
    existing_times = {row.observation_time_utc for row in stored_before}
    http = ReadOnlyJsonClient()
    try:
        events = fetch_synoptic_regime_settled_events(target=target, http=http)
        samples = build_replay_samples(
            target=target,
            events=events,
            http=http,
            existing_observation_times=existing_times,
            max_new_samples=max_new_samples,
        )
    finally:
        http.close()
    persisted = persist_replay_samples(client=db, samples=samples)
    stored = load_replay_samples(client=db, station_id=target.station_id)
    report = build_certification_report(stored)
    run_id = persist_certification_report(client=db, report=report) if report.get("station_id") else None
    return {
        "status": "CERTIFICATION_ACCELERATOR_PASS",
        "model_version": REPLAY_MODEL_VERSION,
        "acceptance_spec_version": ACCEPTANCE_SPEC_VERSION,
        "target": asdict(target),
        "settled_events_seen": len(events),
        "samples_replayed": len(samples),
        "samples_newly_persisted": persisted,
        "certification_run_id": run_id,
        "report": report,
        "probability_publishable": False,
        "can_execute": False,
    }


def _build_settled_event(
    target: ReplayTarget, event_ticker: str, markets: Sequence[Mapping[str, Any]]
) -> SettledWeatherEvent:
    if not markets:
        raise HistoricalCertificationError("SETTLED_EVENT_EMPTY", event_ticker)
    parsed_rows: list[tuple[datetime, float, PredicateResult, Mapping[str, Any]]] = []
    for market in markets:
        rule = " ".join(
            str(market.get(key) or "") for key in ("rules_primary", "rules_secondary")
        ).strip()
        if "synoptic data" not in rule.casefold():
            raise HistoricalCertificationError("SETTLEMENT_SOURCE_REGIME_MISMATCH", event_ticker)
        if target.expected_rule_location.casefold() not in rule.casefold():
            raise HistoricalCertificationError("SETTLEMENT_LOCATION_MISMATCH", event_ticker)
        observation = _observation_time_from_rule(rule)
        occurrence = _optional_dt(market.get("occurrence_datetime"))
        if occurrence is not None:
            lag = occurrence - observation
            if lag < timedelta(0) or lag > timedelta(minutes=15):
                raise HistoricalCertificationError("OCCURRENCE_RULE_TIME_MISMATCH", event_ticker)
        settled = _finite_float(market.get("expiration_value"), "EXPIRATION_VALUE_MISSING")
        result = str(market.get("result") or "").strip().lower()
        if result not in {"yes", "no"}:
            raise HistoricalCertificationError("SETTLED_RESULT_MISSING", event_ticker)
        strike_type = str(market.get("strike_type") or "").strip().lower()
        floor = _optional_float(market.get("floor_strike"))
        cap = _optional_float(market.get("cap_strike"))
        predicate = PredicateResult(
            str(market.get("ticker") or ""), strike_type, floor, cap, result == "yes"
        )
        if _predicate_truth(settled, predicate) != predicate.yes_outcome:
            raise HistoricalCertificationError("SETTLEMENT_PREDICATE_CONFLICT", predicate.ticker)
        parsed_rows.append((observation, settled, predicate, dict(market)))
    observations = {row[0] for row in parsed_rows}
    values = {round(row[1], 6) for row in parsed_rows}
    if len(observations) != 1 or len(values) != 1:
        raise HistoricalCertificationError("SETTLED_EVENT_IDENTITY_CONFLICT", event_ticker)
    observation = next(iter(observations))
    settled = next(iter(values))
    predicates = tuple(
        sorted((row[2] for row in parsed_rows), key=lambda item: (item.floor_strike or -9999, item.ticker))
    )
    return SettledWeatherEvent(
        station_id=target.station_id,
        series_ticker=target.series_ticker,
        event_ticker=event_ticker,
        observation_time_utc=_iso(observation),
        settlement_source_name="Synoptic Data",
        settled_value_f=float(settled),
        predicates=predicates,
        raw_markets=tuple(row[3] for row in parsed_rows),
    )


def _single_run_url(*, target: ReplayTarget, run_init: datetime) -> str:
    params = {
        "latitude": f"{target.latitude:.4f}",
        "longitude": f"{target.longitude:.4f}",
        "hourly": "temperature_2m",
        "models": ",".join(MODEL_NAMES),
        "temperature_unit": "fahrenheit",
        "timezone": "UTC",
        "run": run_init.strftime("%Y-%m-%dT%H:%M"),
        "forecast_hours": 60,
    }
    return f"{OPEN_METEO_SINGLE_RUN_BASE}?{urlencode(params)}"


def _fetch_single_run_payload(
    *, target: ReplayTarget, run_init: datetime, http: ReadOnlyJsonClient
) -> Mapping[str, Any]:
    return http.get_json(_single_run_url(target=target, run_init=run_init))


def _extract_model_temperatures(
    payload: Mapping[str, Any], target_time: datetime
) -> tuple[float, float]:
    if payload.get("utc_offset_seconds") not in (0, 0.0, "0"):
        raise HistoricalCertificationError("OPEN_METEO_REPLAY_NOT_UTC")
    hourly = payload.get("hourly")
    if not isinstance(hourly, Mapping):
        raise HistoricalCertificationError("OPEN_METEO_REPLAY_HOURLY_MISSING")
    times = hourly.get("time")
    if not isinstance(times, Sequence) or isinstance(times, (str, bytes, bytearray)):
        raise HistoricalCertificationError("OPEN_METEO_REPLAY_TIMES_MISSING")
    matches = [i for i, value in enumerate(times) if _open_meteo_time(str(value)) == target_time]
    if len(matches) != 1:
        raise HistoricalCertificationError("OPEN_METEO_REPLAY_TARGET_MISSING", _iso(target_time))
    i = matches[0]
    gfs_values = hourly.get("temperature_2m_gfs_seamless")
    ecmwf_values = hourly.get("temperature_2m_ecmwf_ifs025")
    if not isinstance(gfs_values, Sequence) or not isinstance(ecmwf_values, Sequence):
        raise HistoricalCertificationError("OPEN_METEO_REPLAY_MODEL_SERIES_MISSING")
    return (
        _finite_float(gfs_values[i], "OPEN_METEO_REPLAY_GFS_VALUE_INVALID"),
        _finite_float(ecmwf_values[i], "OPEN_METEO_REPLAY_ECMWF_VALUE_INVALID"),
    )


def _observation_time_from_rule(rule_text: str) -> datetime:
    import re

    date_match = re.search(
        r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+([0-3]?\d),?\s+(20\d{2})\b",
        rule_text,
        re.I,
    )
    clock_match = re.search(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b", rule_text, re.I)
    tz_match = re.search(r"\b(EST|EDT|CST|CDT|MST|MDT|PST|PDT)\b", rule_text, re.I)
    if not date_match or not clock_match or not tz_match:
        raise HistoricalCertificationError("RULE_OBSERVATION_TIME_UNRESOLVED")
    months = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    offsets = {"EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6, "PST": -8, "PDT": -7}
    month = months[date_match.group(1)[:3].lower()]
    day = int(date_match.group(2))
    year = int(date_match.group(3))
    hour = int(clock_match.group(1)) % 12
    if clock_match.group(3).lower() == "pm":
        hour += 12
    minute = int(clock_match.group(2) or 0)
    tz_token = tz_match.group(1).upper()
    local = datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=timezone(timedelta(hours=offsets[tz_token])),
    )
    return local.astimezone(timezone.utc)


def _predicate_truth(value: float, predicate: PredicateResult) -> bool:
    if predicate.strike_type == "greater" and predicate.floor_strike is not None:
        return value > predicate.floor_strike
    if predicate.strike_type == "less" and predicate.cap_strike is not None:
        return value < predicate.cap_strike
    if (
        predicate.strike_type == "between"
        and predicate.floor_strike is not None
        and predicate.cap_strike is not None
    ):
        return predicate.floor_strike <= value <= predicate.cap_strike
    raise HistoricalCertificationError("UNSUPPORTED_PREDICATE", predicate.ticker)


def _predicate_probability(mu: float, sigma: float, predicate: PredicateResult) -> float:
    sigma = max(float(sigma), 0.000001)
    if predicate.strike_type == "greater" and predicate.floor_strike is not None:
        return 1.0 - _normal_cdf((predicate.floor_strike - mu) / sigma)
    if predicate.strike_type == "less" and predicate.cap_strike is not None:
        return _normal_cdf((predicate.cap_strike - mu) / sigma)
    if (
        predicate.strike_type == "between"
        and predicate.floor_strike is not None
        and predicate.cap_strike is not None
    ):
        return max(
            0.0,
            min(
                1.0,
                _normal_cdf((predicate.cap_strike - mu) / sigma)
                - _normal_cdf((predicate.floor_strike - mu) / sigma),
            ),
        )
    raise HistoricalCertificationError("UNSUPPORTED_PREDICATE", predicate.ticker)


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _probability_bucket(p: float) -> str:
    index = min(9, max(0, int(float(p) * 10)))
    return f"{index/10:.1f}-{(index+1)/10:.1f}"


def _sample_from_row(row: Mapping[str, Any]) -> ReplaySample:
    predicates = tuple(PredicateResult(**item) for item in (row.get("predicates") or []))
    return ReplaySample(
        sample_id=str(row["sample_id"]),
        station_id=str(row["station_id"]),
        series_ticker=str(row["series_ticker"]),
        event_ticker=str(row["event_ticker"]),
        observation_time_utc=_iso(_dt(str(row["observation_time"]))),
        settlement_source_name=str(row["settlement_source_name"]),
        settled_value_f=float(row["settled_value_f"]),
        decision_time=_iso(_dt(str(row["decision_time"]))),
        run_initialization=_iso(_dt(str(row["run_initialization"]))),
        safe_available_at=_iso(_dt(str(row["safe_available_at"]))),
        gfs_f=float(row["gfs_f"]),
        ecmwf_f=float(row["ecmwf_f"]),
        central_estimate_f=float(row["central_estimate_f"]),
        archive_source_url=str(row.get("archive_source_url") or ""),
        archive_payload_hash=str(row.get("archive_payload_hash") or ""),
        predicates=predicates,
        no_lookahead_verified=bool(row["no_lookahead_verified"]),
        source_identity_verified=bool(row["source_identity_verified"]),
    )


def _assert_materially_equal(existing: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    timestamp_keys = {"observation_time", "decision_time", "run_initialization", "safe_available_at"}
    for key, value in expected.items():
        current = existing.get(key)
        if key in timestamp_keys:
            try:
                current = _iso(_dt(str(current)))
                value = _iso(_dt(str(value)))
            except Exception:
                pass
        if json.dumps(current, sort_keys=True, separators=(",", ":"), default=str) != json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=str
        ):
            raise HistoricalCertificationError(
                "REPLAY_SAMPLE_IDENTITY_COLLISION", f"{expected.get('sample_id')}:{key}"
            )


def _finite_float(value: Any, code: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HistoricalCertificationError(code) from exc
    if not math.isfinite(number):
        raise HistoricalCertificationError(code)
    return number


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return _finite_float(value, "STRIKE_VALUE_INVALID")


def _optional_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return _dt(str(value))


def _open_meteo_time(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise HistoricalCertificationError("OPEN_METEO_REPLAY_TIME_INVALID")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise HistoricalCertificationError("TIMEZONE_AWARE_TIMESTAMP_REQUIRED", str(value))
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
