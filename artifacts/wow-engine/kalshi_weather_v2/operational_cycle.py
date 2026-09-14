from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from .contract_rule_acquisition import KalshiContractRuleAcquirer
from .hourly_index import KalshiWeatherIndexAdapter
from .hourly_rule_semantics import ParsedHourlyTemperatureRule, parse_hourly_temperature_rule
from .http_client import ReadOnlyJsonClient
from .market_discovery import KalshiWeatherMarketDiscovery
from .market_monitor import MarketMonitorResult, refresh_open_weather_markets
from .persistence import KalshiWeatherPersistence, content_id
from .runtime import MODEL_VERSION, capture_hourly_shadow, lead_time_bucket
from .shadow_cohort import CAPTURE_BUCKETS, _settle_ready_predictions
from .source_adapters import ProviderSnapshot
from .supplemental_capture import capture_hourly_supplemental_sources


@dataclass(frozen=True)
class OperationalTarget:
    index_city: str
    expected_location: str
    forecast_latitude: float
    forecast_longitude: float
    forecast_reference_note: str
    enabled: bool = True


# Exact settlement-index slugs are verified against Kalshi's public Weather
# Index API. Coordinates are forecast-grid references only and are never treated
# as settlement stations/sites.
VERIFIED_HOURLY_TARGETS = (
    OperationalTarget(
        index_city="miami",
        expected_location="Miami",
        forecast_latitude=25.7617,
        forecast_longitude=-80.1918,
        forecast_reference_note=(
            "Miami city forecast-grid reference only; exact settlement identity "
            "is KALSHI_WEATHER_INDEX:miami."
        ),
    ),
    OperationalTarget(
        index_city="chicago",
        expected_location="Chicago Metro Area",
        forecast_latitude=41.8781,
        forecast_longitude=-87.6298,
        forecast_reference_note=(
            "Chicago forecast-grid reference only; exact settlement identity "
            "is KALSHI_WEATHER_INDEX:chicago."
        ),
    ),
    OperationalTarget(
        index_city="la-coastal",
        expected_location="Coastal Los Angeles",
        forecast_latitude=33.9500,
        forecast_longitude=-118.4000,
        forecast_reference_note=(
            "Coastal-LA forecast-grid reference only; exact settlement identity "
            "is KALSHI_WEATHER_INDEX:la-coastal."
        ),
    ),
)


@dataclass(frozen=True)
class DiscoveredContract:
    target: OperationalTarget
    ticker: str
    parsed: ParsedHourlyTemperatureRule


@dataclass(frozen=True)
class DiscoveryDiagnostic:
    target: str
    series: tuple[str, ...]
    markets_seen: int
    future_contracts_accepted: int
    expired_or_current_skipped: int
    rejected_contracts: int
    rejection_reasons: tuple[str, ...]
    discovery_paths: tuple[str, ...]


@dataclass(frozen=True)
class OperationalCycleResult:
    status: str
    targets_checked: int
    markets_seen: int
    contracts_discovered: int
    predictions_captured: int
    predictions_skipped_existing: int
    supplemental_snapshots_captured: int
    official_trajectory_snapshots_captured: int
    predictions_settled: int
    market_snapshots_refreshed: int
    discovery_diagnostics: tuple[DiscoveryDiagnostic, ...]
    capture_failures: tuple[str, ...]
    supplemental_failures: tuple[str, ...]
    trajectory_failures: tuple[str, ...]
    settlement_failures: tuple[str, ...]
    market_monitor_failures: tuple[str, ...]
    probability_publishable: bool = False
    can_execute: bool = False


def run_weather_operational_cycle_once(
    *,
    db_client_fn: Callable[[], object],
    targets: Sequence[OperationalTarget] = VERIFIED_HOURLY_TARGETS,
    now: str | None = None,
) -> OperationalCycleResult:
    decision_time = _iso(_parse_or_now(now))
    db = db_client_fn()
    http = ReadOnlyJsonClient()
    existing_keys = _existing_prediction_keys(db)
    capture_failures: list[str] = []
    supplemental_failures: list[str] = []
    trajectory_failures: list[str] = []
    settlement_failures: list[str] = []
    diagnostics: list[DiscoveryDiagnostic] = []
    markets_seen = 0
    accepted_count = 0
    captured = 0
    skipped = 0
    supplemental_count = 0
    trajectory_count = 0

    try:
        for target in targets:
            if not target.enabled:
                continue
            contracts, diagnostic = discover_target_contracts(
                http=http,
                target=target,
                decision_time=decision_time,
            )
            diagnostics.append(diagnostic)
            markets_seen += diagnostic.markets_seen
            accepted_count += len(contracts)

            supplemental_done: set[tuple[str, str]] = set()
            trajectory_rule_id: str | None = None
            for item in contracts:
                target_time = item.parsed.observation_time_utc
                try:
                    bucket = lead_time_bucket(decision_time, target_time)
                except Exception as exc:
                    capture_failures.append(
                        f"{target.index_city}:{item.ticker}:BUCKET:{type(exc).__name__}"
                    )
                    continue
                if bucket not in CAPTURE_BUCKETS:
                    continue
                prediction_key = operational_prediction_key(
                    ticker=item.ticker,
                    target_time_utc=target_time,
                    bucket=bucket,
                )
                if prediction_key in existing_keys:
                    skipped += 1
                    continue

                try:
                    capture = capture_hourly_shadow(
                        client=db,
                        ticker=item.ticker,
                        index_city=target.index_city,
                        expected_location=target.expected_location,
                        forecast_latitude=target.forecast_latitude,
                        forecast_longitude=target.forecast_longitude,
                        decision_time=decision_time,
                        http=http,
                    )
                except Exception as exc:
                    capture_failures.append(
                        f"{target.index_city}:{item.ticker}:{bucket}:{type(exc).__name__}:{getattr(exc, 'code', '')}"
                    )
                    continue

                captured += 1
                existing_keys.add(prediction_key)
                rule_snapshot_id = str(capture.get("rule_snapshot_id") or "")
                if rule_snapshot_id and trajectory_rule_id is None:
                    trajectory_rule_id = rule_snapshot_id

                supplemental_key = (target_time, bucket)
                if rule_snapshot_id and supplemental_key not in supplemental_done:
                    supplemental_done.add(supplemental_key)
                    try:
                        supplemental = capture_hourly_supplemental_sources(
                            client=db,
                            rule_snapshot_id=rule_snapshot_id,
                            forecast_latitude=target.forecast_latitude,
                            forecast_longitude=target.forecast_longitude,
                            target_time_utc=target_time,
                            retrieved_at=decision_time,
                            http=http,
                        )
                        supplemental_count += len(supplemental.get("source_snapshot_ids") or [])
                        supplemental_failures.extend(
                            f"{target.index_city}:{target_time}:{failure}"
                            for failure in (supplemental.get("failures") or [])
                        )
                    except Exception as exc:
                        supplemental_failures.append(
                            f"{target.index_city}:{target_time}:{type(exc).__name__}:{getattr(exc, 'code', '')}"
                        )

            if trajectory_rule_id:
                try:
                    _capture_index_trajectory(
                        client=db,
                        http=http,
                        target=target,
                        rule_snapshot_id=trajectory_rule_id,
                        retrieved_at=decision_time,
                    )
                    trajectory_count += 1
                except Exception as exc:
                    trajectory_failures.append(
                        f"{target.index_city}:{type(exc).__name__}:{getattr(exc, 'code', '')}"
                    )

        settled = _settle_ready_predictions(db, http, decision_time, settlement_failures)
        market_monitor = refresh_open_weather_markets(
            client=db,
            retrieved_at=decision_time,
            http=http,
        )
    finally:
        http.close()

    severe_discovery_failure = any(
        diagnostic.markets_seen > 0
        and diagnostic.future_contracts_accepted == 0
        and diagnostic.rejected_contracts > 0
        for diagnostic in diagnostics
    )
    failures_exist = bool(
        capture_failures
        or settlement_failures
        or market_monitor.failures
        or severe_discovery_failure
    )
    status = "WEATHER_OPERATIONAL_CYCLE_PARTIAL" if failures_exist else "WEATHER_OPERATIONAL_CYCLE_PASS"

    return OperationalCycleResult(
        status=status,
        targets_checked=sum(1 for target in targets if target.enabled),
        markets_seen=markets_seen,
        contracts_discovered=accepted_count,
        predictions_captured=captured,
        predictions_skipped_existing=skipped,
        supplemental_snapshots_captured=supplemental_count,
        official_trajectory_snapshots_captured=trajectory_count,
        predictions_settled=settled,
        market_snapshots_refreshed=market_monitor.market_snapshots_written,
        discovery_diagnostics=tuple(diagnostics),
        capture_failures=tuple(capture_failures),
        supplemental_failures=tuple(supplemental_failures),
        trajectory_failures=tuple(trajectory_failures),
        settlement_failures=tuple(settlement_failures),
        market_monitor_failures=market_monitor.failures,
        probability_publishable=False,
        can_execute=False,
    )


def discover_target_contracts(
    *,
    http: ReadOnlyJsonClient,
    target: OperationalTarget,
    decision_time: str,
) -> tuple[tuple[DiscoveredContract, ...], DiscoveryDiagnostic]:
    discovery = KalshiWeatherMarketDiscovery(http.get_json)
    acquirer = KalshiContractRuleAcquirer(http.get_json)
    accepted: list[DiscoveredContract] = []
    series_seen: list[str] = []
    paths: list[str] = []
    rejection_reasons: list[str] = []
    markets_seen = 0
    expired = 0

    try:
        series_rows = discovery.candidate_series(expected_location=target.expected_location)
    except Exception as exc:
        diagnostic = DiscoveryDiagnostic(
            target=target.index_city,
            series=(),
            markets_seen=0,
            future_contracts_accepted=0,
            expired_or_current_skipped=0,
            rejected_contracts=1,
            rejection_reasons=(_reason(exc, prefix="SERIES_DISCOVERY"),),
            discovery_paths=(),
        )
        return (), diagnostic

    for series in series_rows:
        series_seen.append(series.ticker)
        try:
            markets = discovery.open_markets(series_ticker=series.ticker)
            if discovery.last_market_discovery_path:
                paths.append(discovery.last_market_discovery_path)
        except Exception as exc:
            rejection_reasons.append(_reason(exc, prefix=f"{series.ticker}:MARKET_DISCOVERY"))
            continue
        markets_seen += len(markets)
        for market in markets:
            ticker = str(market.get("ticker") or "").strip().upper()
            if not ticker:
                rejection_reasons.append(f"{series.ticker}:MARKET_TICKER_MISSING")
                continue
            try:
                rules = acquirer.acquire(ticker, acquired_at=decision_time)
                parsed = parse_hourly_temperature_rule(
                    rules,
                    index_city=target.index_city,
                    expected_location=target.expected_location,
                )
            except Exception as exc:
                rejection_reasons.append(_reason(exc, prefix=ticker))
                continue
            if _parse_utc(parsed.observation_time_utc) <= _parse_utc(decision_time):
                expired += 1
                continue
            accepted.append(
                DiscoveredContract(
                    target=target,
                    ticker=ticker,
                    parsed=parsed,
                )
            )

    diagnostic = DiscoveryDiagnostic(
        target=target.index_city,
        series=tuple(series_seen),
        markets_seen=markets_seen,
        future_contracts_accepted=len(accepted),
        expired_or_current_skipped=expired,
        rejected_contracts=len(rejection_reasons),
        rejection_reasons=tuple(rejection_reasons[:50]),
        discovery_paths=tuple(dict.fromkeys(paths)),
    )
    return tuple(accepted), diagnostic


def operational_prediction_key(*, ticker: str, target_time_utc: str, bucket: str) -> str:
    return content_id(
        "kalshi-weather-operational-prediction-key",
        {
            "ticker": str(ticker).strip().upper(),
            "target_time_utc": _iso(_parse_utc(target_time_utc)),
            "lead_time_bucket": str(bucket).strip().upper(),
            "model_version": MODEL_VERSION,
        },
    )


def _existing_prediction_keys(client) -> set[str]:
    keys: set[str] = set()
    try:
        rows = client.table("wow_kalshi_weather_predictions").select("*").limit(5000).execute().data or []
    except Exception:
        return keys
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        model_payload = row.get("model_payload") if isinstance(row.get("model_payload"), Mapping) else {}
        contract = model_payload.get("contract") if isinstance(model_payload.get("contract"), Mapping) else {}
        window = str(contract.get("observation_window") or "")
        bucket = str(model_payload.get("lead_time_bucket") or "").strip().upper()
        ticker = str(row.get("ticker") or "").strip().upper()
        prefix = "POINT_IN_TIME:"
        if not ticker or not bucket or not window.startswith(prefix):
            continue
        keys.add(
            operational_prediction_key(
                ticker=ticker,
                target_time_utc=window[len(prefix):],
                bucket=bucket,
            )
        )
    return keys


def _capture_index_trajectory(
    *,
    client,
    http: ReadOnlyJsonClient,
    target: OperationalTarget,
    rule_snapshot_id: str,
    retrieved_at: str,
) -> str:
    index = KalshiWeatherIndexAdapter(http.get_json).snapshot(
        target.index_city,
        retrieved_at=retrieved_at,
        detailed=True,
    )
    valid_times = tuple(
        datetime.fromtimestamp(point.timestamp_ms / 1000.0, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
        for point in index.points
    )
    snapshot = ProviderSnapshot(
        provider="KALSHI_WEATHER_INDEX",
        role="OFFICIAL_SETTLEMENT_TRAJECTORY",
        source_id=f"KALSHI_WEATHER_INDEX:{target.index_city}",
        retrieved_at=retrieved_at,
        issued_at=None,
        valid_times=valid_times,
        payload=index.raw,
    )
    snapshot_id = content_id(
        "kalshi-weather-index-trajectory",
        {
            "rule_snapshot_id": rule_snapshot_id,
            "index_city": target.index_city,
            "retrieved_at": retrieved_at,
            "config_version": index.config_version,
        },
    )
    KalshiWeatherPersistence(client).persist_source_snapshot(
        rule_snapshot_id=rule_snapshot_id,
        source_snapshot_id=snapshot_id,
        snapshot=snapshot,
    )
    return snapshot_id


def _reason(exc: Exception, *, prefix: str) -> str:
    code = str(getattr(exc, "code", "") or type(exc).__name__)
    blockers = getattr(exc, "blockers", ()) or ()
    if blockers:
        return f"{prefix}:{code}:{'|'.join(str(x) for x in blockers)}"
    detail = str(getattr(exc, "detail", "") or "")
    return f"{prefix}:{code}:{detail}".rstrip(":")


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


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
