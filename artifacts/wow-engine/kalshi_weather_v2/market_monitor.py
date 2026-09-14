from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Mapping

from .fee_policy import (
    FeePolicyError,
    quote_new_order_single_fill_cash_fee,
    quote_trade_fee,
    resolve_fee_policy,
)
from .http_client import ReadOnlyJsonClient
from .kalshi_market_data import KalshiPublicMarketAdapter
from .models import MarketSnapshot
from .persistence import KalshiWeatherPersistence, content_id


@dataclass(frozen=True)
class MarketMonitorResult:
    predictions_checked: int
    market_snapshots_written: int
    closed_markets: int
    failures: tuple[str, ...]
    probability_changed: bool = False
    can_execute: bool = False


def refresh_open_weather_markets(
    *,
    client,
    retrieved_at: str,
    http: ReadOnlyJsonClient | None = None,
) -> MarketMonitorResult:
    """Append fresh quote/orderbook snapshots without changing model probability.

    A market-price move is market information only. This routine never creates
    or edits a Weather prediction and therefore cannot move P(YES)/P(NO).
    """
    own_http = http is None
    http = http or ReadOnlyJsonClient()
    persistence = KalshiWeatherPersistence(client)
    adapter = KalshiPublicMarketAdapter(http.get_json)
    failures: list[str] = []
    written = 0
    closed = 0

    try:
        predictions = _latest_prediction_per_ticker(client)
        for prediction in predictions.values():
            prediction_id = str(prediction.get("prediction_id") or "")
            ticker = str(prediction.get("ticker") or "").strip().upper()
            rule_snapshot_id = str(prediction.get("rule_snapshot_id") or "")
            if not prediction_id or not ticker or not rule_snapshot_id:
                continue
            try:
                evidence = adapter.snapshot(ticker, retrieved_at=retrieved_at)
            except Exception as exc:
                failures.append(f"{ticker}:MARKET:{type(exc).__name__}:{getattr(exc, 'code', '')}")
                continue
            if not evidence.market_open:
                closed += 1

            rule = persistence.load_rule(rule_snapshot_id)
            fee_policy_id = None
            fee_known = False
            friction_verified = False
            yes_break_even = None
            no_break_even = None
            fee_evidence: dict[str, Any] = {
                "status": "FEE_POLICY_NOT_RESOLVED",
                "can_execute": False,
            }
            if isinstance(rule, Mapping):
                try:
                    series = rule.get("raw_series") if isinstance(rule.get("raw_series"), Mapping) else {}
                    event = rule.get("raw_event") if isinstance(rule.get("raw_event"), Mapping) else {}
                    series_ticker = str(rule.get("series_ticker") or "").strip().upper()
                    event_ticker = str(rule.get("event_ticker") or "").strip().upper()
                    series_changes = adapter.get_series_fee_changes(series_ticker) if series_ticker else ()
                    event_changes = adapter.get_event_fee_changes(event_ticker) if event_ticker else ()
                    policy = resolve_fee_policy(
                        series=series,
                        event=event,
                        market=evidence.source_market,
                        fee_changes=series_changes,
                        event_fee_changes=event_changes,
                        resolved_at=retrieved_at,
                    )
                    fee_policy_id = content_id(
                        "kalshi-weather-fee-policy",
                        {
                            "policy": _jsonable(asdict(policy)),
                            "resolved_at": retrieved_at,
                        },
                    )
                    balance_quantum = _configured_balance_quantum()
                    yes_quote = _fee_quote(
                        policy,
                        evidence.yes_best_ask,
                        balance_quantum=balance_quantum,
                    )
                    no_quote = _fee_quote(
                        policy,
                        evidence.no_best_ask,
                        balance_quantum=balance_quantum,
                    )
                    yes_break_even = _break_even(yes_quote)
                    no_break_even = _break_even(no_quote)
                    fee_known = True
                    friction_verified = bool(
                        balance_quantum is not None
                        and (yes_quote is None or yes_quote.friction_model_verified)
                        and (no_quote is None or no_quote.friction_model_verified)
                    )
                    fee_evidence = {
                        "status": "FEE_POLICY_RESOLVED",
                        "fee_policy_id": fee_policy_id,
                        "policy": _jsonable(asdict(policy)),
                        "balance_quantum": str(balance_quantum) if balance_quantum is not None else None,
                        "cash_rounding_verified": friction_verified,
                        "yes_quote": _jsonable(asdict(yes_quote)) if yes_quote is not None else None,
                        "no_quote": _jsonable(asdict(no_quote)) if no_quote is not None else None,
                        "can_execute": False,
                    }
                except Exception as exc:
                    fee_evidence = {
                        "status": "FEE_POLICY_UNRESOLVED",
                        "error_type": type(exc).__name__,
                        "code": getattr(exc, "code", None),
                        "blockers": list(getattr(exc, "blockers", ()) or ()),
                        "can_execute": False,
                    }

            market = MarketSnapshot(
                yes_price=evidence.yes_best_ask,
                no_price=evidence.no_best_ask,
                price_time=evidence.retrieved_at,
                market_open=evidence.market_open,
                orderbook_nonempty=evidence.orderbook_nonempty,
                executable_price_verified=(
                    evidence.yes_best_ask is not None or evidence.no_best_ask is not None
                ),
                fee_known=fee_known,
                fee_per_share=None,
                friction_model_verified=friction_verified,
                yes_effective_break_even=yes_break_even,
                no_effective_break_even=no_break_even,
            )
            market_snapshot_id = content_id(
                "kalshi-weather-market-refresh",
                {
                    "prediction_id": prediction_id,
                    "ticker": ticker,
                    "retrieved_at": retrieved_at,
                    "yes_bid": evidence.yes_best_bid,
                    "no_bid": evidence.no_best_bid,
                    "yes_ask": evidence.yes_best_ask,
                    "no_ask": evidence.no_best_ask,
                    "fee_policy_id": fee_policy_id,
                    "friction_model_verified": friction_verified,
                },
            )
            raw_market = dict(evidence.source_market)
            raw_market["_wow_fee_evidence"] = fee_evidence
            try:
                persistence.persist_market_snapshot(
                    market_snapshot_id=market_snapshot_id,
                    prediction_id=prediction_id,
                    ticker=ticker,
                    retrieved_at=retrieved_at,
                    market_status=evidence.market_status,
                    yes_best_bid=evidence.yes_best_bid,
                    no_best_bid=evidence.no_best_bid,
                    market=market,
                    raw_market=raw_market,
                    raw_orderbook=evidence.source_orderbook,
                    fee_policy_id=fee_policy_id,
                )
                written += 1
            except Exception as exc:
                failures.append(f"{ticker}:PERSIST:{type(exc).__name__}:{getattr(exc, 'code', '')}")
    finally:
        if own_http:
            http.close()

    return MarketMonitorResult(
        predictions_checked=len(predictions),
        market_snapshots_written=written,
        closed_markets=closed,
        failures=tuple(failures),
        probability_changed=False,
        can_execute=False,
    )


def _latest_prediction_per_ticker(client) -> dict[str, Mapping[str, Any]]:
    try:
        rows = client.table("wow_kalshi_weather_predictions").select("*").limit(5000).execute().data or []
    except Exception:
        return {}
    latest: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        decision_time = str(row.get("decision_time") or "")
        if not ticker:
            continue
        current = latest.get(ticker)
        if current is None or decision_time > str(current.get("decision_time") or ""):
            latest[ticker] = dict(row)
    return latest


def _configured_balance_quantum() -> Decimal | None:
    raw = str(os.getenv("WOW_KALSHI_BALANCE_QUANTUM", "") or "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except Exception:
        return None


def _fee_quote(policy, price: float | None, *, balance_quantum: Decimal | None):
    if price is None:
        return None
    if balance_quantum is not None:
        return quote_new_order_single_fill_cash_fee(
            policy,
            price=price,
            quantity=1,
            balance_quantum=balance_quantum,
            liquidity_role="TAKER",
        )
    return quote_trade_fee(
        policy,
        price=price,
        quantity=1,
        liquidity_role="TAKER",
    )


def _break_even(quote) -> float | None:
    if quote is None:
        return None
    value = quote.effective_break_even if quote.effective_break_even is not None else quote.pre_cash_rounding_break_even
    return float(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value
