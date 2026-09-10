from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .kalshi_market_data import KALSHI_BASE_URL, KalshiMarketDataError, KalshiPublicMarketAdapter
from .rule_snapshot import FrozenRuleSnapshot, freeze_market_rules


class ContractRuleAcquisitionError(ValueError):
    def __init__(self, code: str, blockers: tuple[str, ...]):
        self.code = code
        self.blockers = blockers
        super().__init__(f"{code}: {', '.join(blockers)}")


@dataclass(frozen=True)
class SettlementSourceEvidence:
    name: str
    url: str | None
    source: str


@dataclass(frozen=True)
class FrozenContractRulePackage:
    package_id: str
    acquired_at: str
    market_rules: FrozenRuleSnapshot
    event_ticker: str
    series_ticker: str
    settlement_source: SettlementSourceEvidence
    raw_event: Mapping[str, Any]
    raw_series: Mapping[str, Any]
    can_execute: bool = False


class KalshiContractRuleAcquirer:
    """Acquire and freeze market -> event -> series rule evidence.

    Series settlement_sources are treated as authoritative structured metadata,
    but ambiguity still fails closed. Market title alone never chooses a source.
    """

    def __init__(self, get_json):
        self.get_json = get_json
        self.market_adapter = KalshiPublicMarketAdapter(get_json)

    def acquire(self, ticker: str, *, acquired_at: str) -> FrozenContractRulePackage:
        market = self.market_adapter.get_market(ticker)
        market_rules = freeze_market_rules(market, acquired_at=acquired_at)

        event_ticker = _required_text(market, "event_ticker", "EVENT_TICKER_MISSING")
        event_payload = self.get_json(f"{KALSHI_BASE_URL}/events/{event_ticker}", None)
        event = event_payload.get("event") if isinstance(event_payload, Mapping) else None
        if not isinstance(event, Mapping):
            raise ContractRuleAcquisitionError("NO_PLAY_SETTLEMENT_AMBIGUITY", ("EVENT_PAYLOAD_INVALID",))
        if str(event.get("event_ticker") or "").strip().upper() != event_ticker.upper():
            raise ContractRuleAcquisitionError("NO_PLAY_SETTLEMENT_AMBIGUITY", ("EVENT_IDENTITY_MISMATCH",))

        series_ticker = _required_text(event, "series_ticker", "SERIES_TICKER_MISSING")
        series_payload = self.get_json(f"{KALSHI_BASE_URL}/series/{series_ticker}", None)
        series = series_payload.get("series") if isinstance(series_payload, Mapping) else None
        if not isinstance(series, Mapping):
            raise ContractRuleAcquisitionError("NO_PLAY_SETTLEMENT_AMBIGUITY", ("SERIES_PAYLOAD_INVALID",))
        if str(series.get("ticker") or "").strip().upper() != series_ticker.upper():
            raise ContractRuleAcquisitionError("NO_PLAY_SETTLEMENT_AMBIGUITY", ("SERIES_IDENTITY_MISMATCH",))

        settlement_source = resolve_settlement_source(
            series.get("settlement_sources"),
            market_rule_text=market_rules.combined_rule_text,
        )

        canonical = {
            "market_rule_snapshot_id": market_rules.rule_snapshot_id,
            "event_ticker": event_ticker.upper(),
            "series_ticker": series_ticker.upper(),
            "settlement_source_name": settlement_source.name,
            "settlement_source_url": settlement_source.url,
            "acquired_at": acquired_at,
        }
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        return FrozenContractRulePackage(
            package_id=f"kalshi-contract-rules-{digest[:24]}",
            acquired_at=acquired_at,
            market_rules=market_rules,
            event_ticker=event_ticker.upper(),
            series_ticker=series_ticker.upper(),
            settlement_source=settlement_source,
            raw_event=dict(event),
            raw_series=dict(series),
            can_execute=False,
        )


def resolve_settlement_source(raw_sources: Any, *, market_rule_text: str) -> SettlementSourceEvidence:
    """Resolve exact structured settlement source without series-family guessing.

    One valid series source is accepted directly because Kalshi documents the
    series settlement_sources field as defining the applied settlement source.
    If multiple sources exist, the exact market rule text must disambiguate by
    naming exactly one of them. Otherwise publication fails closed.
    """
    sources = _normalize_sources(raw_sources)
    if not sources:
        raise ContractRuleAcquisitionError(
            "NO_PLAY_SETTLEMENT_AMBIGUITY", ("SETTLEMENT_SOURCE_MISSING",)
        )
    if len(sources) == 1:
        return sources[0]

    text = (market_rule_text or "").casefold()
    named = [source for source in sources if source.name.casefold() in text]
    if len(named) == 1:
        source = named[0]
        return SettlementSourceEvidence(name=source.name, url=source.url, source="SERIES_PLUS_MARKET_RULE_DISAMBIGUATION")

    raise ContractRuleAcquisitionError(
        "NO_PLAY_SETTLEMENT_AMBIGUITY",
        ("MULTIPLE_SETTLEMENT_SOURCES_UNRESOLVED",),
    )


def _normalize_sources(raw_sources: Any) -> tuple[SettlementSourceEvidence, ...]:
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)):
        return ()
    out: list[SettlementSourceEvidence] = []
    for raw in raw_sources:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        url = str(raw.get("url") or "").strip() or None
        out.append(SettlementSourceEvidence(name=name, url=url, source="SERIES_SETTLEMENT_SOURCES"))
    # De-duplicate exact source identity without collapsing different URLs.
    deduped: dict[tuple[str, str | None], SettlementSourceEvidence] = {}
    for source in out:
        deduped[(source.name.casefold(), source.url)] = source
    return tuple(deduped.values())


def _required_text(obj: Mapping[str, Any], field: str, blocker: str) -> str:
    value = str(obj.get(field) or "").strip()
    if not value:
        raise ContractRuleAcquisitionError("NO_PLAY_SETTLEMENT_AMBIGUITY", (blocker,))
    return value
