from __future__ import annotations

import logging
from typing import Callable, Sequence

from .http_client import ReadOnlyJsonClient
from .runtime import capture_hourly_shadow, lead_time_bucket
from .shadow_cohort import (
    CAPTURE_BUCKETS,
    CohortRunResult,
    HourlyCohortTarget,
    _discover_target_contracts,
    _existing_sample_keys,
    _iso,
    _parse_or_now,
    cohort_sample_key,
)
from .supplemental_capture import capture_hourly_supplemental_sources


_logger = logging.getLogger("wow.kalshi_weather_v2.bounded_cohort")


def run_bounded_capture_only_cohort_once(
    *,
    db_client_fn: Callable[[], object],
    targets: Sequence[HourlyCohortTarget],
    now: str | None = None,
) -> CohortRunResult:
    """Run a bounded empirical capture pass without sweeping settlement backlog.

    This function exists specifically for the shared low-resource web process.
    It preserves the same exact-rule parsing, model-blind weather acquisition,
    one-sibling-per-target/bucket sampling, immutable persistence, and fail-closed
    publication semantics as ``run_hourly_shadow_cohort_once``. It deliberately
    does not settle historical predictions; backlog settlement is a separate
    workload boundary so an old cohort cannot monopolize or recycle the API.
    """
    decision_time = _iso(_parse_or_now(now))
    db = db_client_fn()
    existing_keys = _existing_sample_keys(db)
    capture_failures: list[str] = []
    supplemental_failures: list[str] = []
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
                failure = (
                    f"{target.index_city}:DISCOVERY:{type(exc).__name__}:"
                    f"{getattr(exc, 'code', '')}"
                )
                capture_failures.append(failure)
                _logger.warning(
                    "WOW_KALSHI_WEATHER_CAPTURE_FAILURE stage=DISCOVERY target=%s failure=%s probability_publishable=false can_execute=false",
                    target.index_city,
                    failure,
                )
                continue

            contracts_discovered += len(contracts)
            by_target: dict[str, list] = {}
            for item in contracts:
                by_target.setdefault(item.parsed.observation_time_utc, []).append(item)

            for target_time, items in sorted(by_target.items()):
                try:
                    bucket = lead_time_bucket(decision_time, target_time)
                except Exception as exc:
                    failure = f"{target.index_city}:{target_time}:BUCKET:{type(exc).__name__}"
                    capture_failures.append(failure)
                    _logger.warning(
                        "WOW_KALSHI_WEATHER_CAPTURE_FAILURE stage=BUCKET target=%s target_time=%s failure=%s probability_publishable=false can_execute=false",
                        target.index_city,
                        target_time,
                        failure,
                    )
                    continue

                if bucket not in CAPTURE_BUCKETS:
                    continue
                sample_key = cohort_sample_key(target.index_city, target_time, bucket)
                if sample_key in existing_keys:
                    skipped += 1
                    continue

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
                    failure = (
                        f"{target.index_city}:{target_time}:{bucket}:{chosen.ticker}:"
                        f"{type(exc).__name__}:{getattr(exc, 'code', '')}"
                    )
                    capture_failures.append(failure)
                    _logger.warning(
                        "WOW_KALSHI_WEATHER_CAPTURE_FAILURE stage=CORE_CAPTURE target=%s target_time=%s bucket=%s ticker=%s error_type=%s error_code=%s probability_publishable=false can_execute=false",
                        target.index_city,
                        target_time,
                        bucket,
                        chosen.ticker,
                        type(exc).__name__,
                        getattr(exc, "code", ""),
                    )
                    continue

                captured += 1
                existing_keys.add(sample_key)
                _logger.warning(
                    "WOW_KALSHI_WEATHER_CAPTURE_PROGRESS status=CORE_CAPTURED target=%s target_time=%s bucket=%s ticker=%s prediction_id=%s probability_publishable=false can_execute=false",
                    target.index_city,
                    target_time,
                    bucket,
                    chosen.ticker,
                    capture.get("prediction_id"),
                )

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
                    supplemental_captured += len(
                        supplemental.get("source_snapshot_ids") or []
                    )
                    for failure in supplemental.get("failures") or []:
                        message = f"{target.index_city}:{target_time}:{failure}"
                        supplemental_failures.append(message)
                        _logger.warning(
                            "WOW_KALSHI_WEATHER_CAPTURE_FAILURE stage=SUPPLEMENTAL target=%s target_time=%s failure=%s probability_publishable=false can_execute=false",
                            target.index_city,
                            target_time,
                            message,
                        )
                except Exception as exc:
                    failure = (
                        f"{target.index_city}:{target_time}:SUPPLEMENTAL:"
                        f"{type(exc).__name__}:{getattr(exc, 'code', '')}"
                    )
                    supplemental_failures.append(failure)
                    _logger.warning(
                        "WOW_KALSHI_WEATHER_CAPTURE_FAILURE stage=SUPPLEMENTAL target=%s target_time=%s error_type=%s error_code=%s probability_publishable=false can_execute=false",
                        target.index_city,
                        target_time,
                        type(exc).__name__,
                        getattr(exc, "code", ""),
                    )
    finally:
        http.close()

    return CohortRunResult(
        status=(
            "EMPIRICAL_SHADOW_COHORT_PASS"
            if not capture_failures
            else "EMPIRICAL_SHADOW_COHORT_PARTIAL"
        ),
        targets_checked=sum(1 for target in targets if target.enabled),
        contracts_discovered=contracts_discovered,
        samples_captured=captured,
        samples_skipped_existing=skipped,
        supplemental_snapshots_captured=supplemental_captured,
        predictions_settled=0,
        capture_failures=tuple(capture_failures),
        supplemental_failures=tuple(supplemental_failures),
        settlement_failures=(),
    )
