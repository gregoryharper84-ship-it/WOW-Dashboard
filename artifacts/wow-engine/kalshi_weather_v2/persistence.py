from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from typing import Any, Mapping, Sequence

from .contract_rule_acquisition import FrozenContractRulePackage
from .models import MarketSnapshot, ProbabilityPackage, TerminalDecision, WeatherEvidenceSnapshot
from .probability_core import CalibrationProfile
from .source_adapters import ProviderSnapshot


class KalshiWeatherPersistenceError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class KalshiWeatherPersistence:
    """Append-only persistence bridge for the six governed weather ledgers.

    The database migration also blocks UPDATE/DELETE. This adapter adds an
    application-side identity-collision check so retries are idempotent only
    when the already-persisted row is materially identical.
    """

    def __init__(self, client):
        self.client = client

    def persist_rule_package(self, package: FrozenContractRulePackage) -> Mapping[str, Any]:
        market = package.market_rules
        row = {
            "rule_snapshot_id": market.rule_snapshot_id,
            "contract_package_id": package.package_id,
            "ticker": market.ticker,
            "event_ticker": package.event_ticker,
            "series_ticker": package.series_ticker,
            "acquired_at": package.acquired_at,
            "settlement_source_name": package.settlement_source.name,
            "settlement_source_url": package.settlement_source.url,
            "contract_url": package.contract_url,
            "contract_terms_url": package.contract_terms_url,
            "rules_primary": market.rules_primary,
            "rules_secondary": market.rules_secondary,
            "raw_market": dict(market.raw_market),
            "raw_event": dict(package.raw_event),
            "raw_series": dict(package.raw_series),
            "can_execute": False,
        }
        return self._insert_exact("wow_kalshi_weather_contract_rules", "rule_snapshot_id", row)

    def persist_source_snapshot(
        self,
        *,
        rule_snapshot_id: str,
        source_snapshot_id: str,
        snapshot: ProviderSnapshot,
    ) -> Mapping[str, Any]:
        row = {
            "source_snapshot_id": source_snapshot_id,
            "rule_snapshot_id": rule_snapshot_id,
            "provider": snapshot.provider,
            "source_role": snapshot.role,
            "source_identifier": snapshot.source_id,
            "evidence_time": snapshot.issued_at or snapshot.retrieved_at,
            "issued_at": snapshot.issued_at,
            "retrieved_at": snapshot.retrieved_at,
            "valid_times": list(snapshot.valid_times),
            "payload": dict(snapshot.payload),
        }
        return self._insert_exact("wow_kalshi_weather_source_snapshots", "source_snapshot_id", row)

    def persist_calibration_profile(
        self,
        *,
        calibration_profile_id: str,
        profile: CalibrationProfile,
        model_version: str,
        fitted_as_of: str,
        certification_evidence: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        row = {
            "calibration_profile_id": calibration_profile_id,
            "station_id": profile.station_id,
            "lane": profile.lane,
            "lead_time_bucket": profile.lead_time_bucket,
            "model_version": model_version,
            "method": profile.method,
            "bias_f": profile.bias_f,
            "sigma_f": profile.sigma_f,
            "lower_sigma_f": profile.lower_sigma_f,
            "upper_sigma_f": profile.upper_sigma_f,
            "sample_n": profile.sample_n,
            "certified": bool(profile.certified),
            "certification_evidence": dict(certification_evidence),
            "fitted_as_of": fitted_as_of,
            "can_execute": False,
        }
        return self._insert_exact("wow_kalshi_weather_calibration_profiles", "calibration_profile_id", row)

    def persist_prediction(
        self,
        *,
        prediction_id: str,
        rule_snapshot_id: str,
        ticker: str,
        decision_time: str,
        model_version: str,
        calibration_profile_id: str | None,
        probability: ProbabilityPackage,
        evidence: WeatherEvidenceSnapshot,
        probability_status: str,
        probability_publishable: bool,
        blockers: Sequence[str],
        warnings: Sequence[str],
        model_payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        row = {
            "prediction_id": prediction_id,
            "rule_snapshot_id": rule_snapshot_id,
            "ticker": ticker,
            "decision_time": decision_time,
            "model_version": model_version,
            "calibration_profile_id": calibration_profile_id,
            "p_yes": probability.p_yes,
            "p_no": probability.p_no,
            "lower_bound_yes": probability.lower_bound_yes,
            "upper_bound_yes": probability.upper_bound_yes,
            "central_estimate_f": evidence.central_estimate,
            "threshold_distance": probability.threshold_distance,
            "probability_status": probability_status,
            "probability_publishable": bool(probability_publishable),
            "blockers": list(dict.fromkeys(str(x) for x in blockers if str(x))),
            "warnings": list(dict.fromkeys(str(x) for x in warnings if str(x))),
            "evidence_snapshot_ids": list(evidence.source_snapshot_ids),
            "model_payload": dict(model_payload),
            "can_execute": False,
        }
        return self._insert_exact("wow_kalshi_weather_predictions", "prediction_id", row)

    def persist_market_snapshot(
        self,
        *,
        market_snapshot_id: str,
        prediction_id: str,
        ticker: str,
        retrieved_at: str,
        market_status: str,
        yes_best_bid: float | None,
        no_best_bid: float | None,
        market: MarketSnapshot,
        raw_market: Mapping[str, Any],
        raw_orderbook: Mapping[str, Any],
        fee_policy_id: str | None = None,
    ) -> Mapping[str, Any]:
        row = {
            "market_snapshot_id": market_snapshot_id,
            "prediction_id": prediction_id,
            "ticker": ticker,
            "retrieved_at": retrieved_at,
            "market_status": market_status,
            "yes_best_bid": yes_best_bid,
            "no_best_bid": no_best_bid,
            "yes_best_ask": market.yes_price,
            "no_best_ask": market.no_price,
            "fee_policy_id": fee_policy_id,
            "yes_effective_break_even": market.yes_effective_break_even,
            "no_effective_break_even": market.no_effective_break_even,
            "executable_price_verified": bool(market.executable_price_verified),
            "friction_model_verified": bool(market.friction_model_verified),
            "raw_market": dict(raw_market),
            "raw_orderbook": dict(raw_orderbook),
        }
        return self._insert_exact("wow_kalshi_weather_market_snapshots", "market_snapshot_id", row)

    def persist_outcome(
        self,
        *,
        outcome_id: str,
        prediction_id: str,
        settled_at: str,
        settlement_source_name: str,
        settlement_source_url: str | None,
        settled_value: float | None,
        yes_outcome: bool | None,
        settlement_payload: Mapping[str, Any],
        p_yes: float | None,
    ) -> Mapping[str, Any]:
        brier_score = None
        log_loss = None
        if yes_outcome is not None and p_yes is not None and 0.0 < float(p_yes) < 1.0:
            y = 1.0 if yes_outcome else 0.0
            p = float(p_yes)
            brier_score = (p - y) ** 2
            log_loss = -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))

        row = {
            "outcome_id": outcome_id,
            "prediction_id": prediction_id,
            "settled_at": settled_at,
            "settlement_source_name": settlement_source_name,
            "settlement_source_url": settlement_source_url,
            "settled_value": settled_value,
            "yes_outcome": yes_outcome,
            "settlement_payload": dict(settlement_payload),
            "brier_score": brier_score,
            "log_loss": log_loss,
        }
        return self._insert_exact("wow_kalshi_weather_outcomes", "outcome_id", row)

    def load_prediction(self, prediction_id: str) -> Mapping[str, Any] | None:
        return self._select_one("wow_kalshi_weather_predictions", "prediction_id", prediction_id)

    def load_rule(self, rule_snapshot_id: str) -> Mapping[str, Any] | None:
        return self._select_one("wow_kalshi_weather_contract_rules", "rule_snapshot_id", rule_snapshot_id)

    def load_runtime_capability(self, capability_key: str = "KALSHI_WEATHER_PROBABILITY") -> Mapping[str, Any]:
        row = self._select_one("wow_runtime_capabilities", "capability_key", capability_key)
        if not row:
            return {
                "capability_key": capability_key,
                "capability_status": "UNAVAILABLE",
                "evidence": {"reason": "RUNTIME_CAPABILITY_ROW_MISSING"},
                "can_execute": False,
            }
        out = dict(row)
        out["can_execute"] = False
        return out

    def load_latest_certified_calibration(
        self,
        *,
        station_id: str,
        lane: str,
        lead_time_bucket: str,
        fitted_before: str,
    ) -> tuple[str, CalibrationProfile] | None:
        try:
            result = (
                self.client.table("wow_kalshi_weather_calibration_profiles")
                .select("*")
                .eq("station_id", station_id)
                .eq("lane", lane)
                .eq("lead_time_bucket", lead_time_bucket)
                .eq("certified", True)
                .lte("fitted_as_of", fitted_before)
                .order("fitted_as_of", desc=True)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            raise KalshiWeatherPersistenceError("KALSHI_WEATHER_CALIBRATION_READ_FAILED", type(exc).__name__) from exc
        rows = result.data or []
        if not rows:
            return None
        row = dict(rows[0])
        profile = CalibrationProfile(
            station_id=str(row["station_id"]),
            lane=str(row["lane"]),
            lead_time_bucket=str(row["lead_time_bucket"]),
            bias_f=float(row["bias_f"]),
            sigma_f=float(row["sigma_f"]),
            lower_sigma_f=float(row["lower_sigma_f"]),
            upper_sigma_f=float(row["upper_sigma_f"]),
            sample_n=int(row["sample_n"]),
            method=str(row["method"]),
            certified=bool(row["certified"]),
        )
        return str(row["calibration_profile_id"]), profile

    def outcome_exists_for_prediction(self, prediction_id: str) -> bool:
        return self._select_one("wow_kalshi_weather_outcomes", "prediction_id", prediction_id) is not None

    def _select_one(self, table: str, key: str, value: Any) -> Mapping[str, Any] | None:
        try:
            result = self.client.table(table).select("*").eq(key, value).limit(1).execute()
        except Exception as exc:
            raise KalshiWeatherPersistenceError("KALSHI_WEATHER_LEDGER_READ_FAILED", f"{table}:{type(exc).__name__}") from exc
        rows = result.data or []
        return dict(rows[0]) if rows else None

    def _insert_exact(self, table: str, primary_key: str, row: Mapping[str, Any]) -> Mapping[str, Any]:
        existing = self._select_one(table, primary_key, row[primary_key])
        if existing is not None:
            for key, expected in row.items():
                if _canonical(existing.get(key)) != _canonical(expected):
                    raise KalshiWeatherPersistenceError(
                        "KALSHI_WEATHER_IDENTITY_COLLISION",
                        f"{table}:{row[primary_key]}:{key}",
                    )
            return existing

        try:
            result = self.client.table(table).insert(dict(row)).execute()
        except Exception as exc:
            raise KalshiWeatherPersistenceError("KALSHI_WEATHER_LEDGER_INSERT_FAILED", f"{table}:{type(exc).__name__}") from exc
        rows = result.data or []
        if not rows:
            raise KalshiWeatherPersistenceError("KALSHI_WEATHER_LEDGER_INSERT_UNCONFIRMED", table)
        persisted = dict(rows[0])
        if str(persisted.get(primary_key)) != str(row[primary_key]):
            raise KalshiWeatherPersistenceError("KALSHI_WEATHER_LEDGER_INSERT_IDENTITY_MISMATCH", table)
        return persisted


def content_id(prefix: str, payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest[:24]}"


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
