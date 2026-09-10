from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


class RuleSnapshotError(ValueError):
    def __init__(self, code: str, blockers: tuple[str, ...]):
        self.code = code
        self.blockers = blockers
        super().__init__(f"{code}: {', '.join(blockers)}")


@dataclass(frozen=True)
class FrozenRuleSnapshot:
    rule_snapshot_id: str
    ticker: str
    acquired_at: str
    title: str
    subtitle: str
    rules_primary: str
    rules_secondary: str
    strike_type: str | None
    floor_strike: float | None
    cap_strike: float | None
    functional_strike: str | None
    close_time: str | None
    expiration_time: str | None
    raw_market: Mapping[str, Any]

    @property
    def combined_rule_text(self) -> str:
        return "\n".join(part for part in (self.rules_primary, self.rules_secondary) if part).strip()


def freeze_market_rules(market: Mapping[str, Any], *, acquired_at: str) -> FrozenRuleSnapshot:
    """Freeze exact Kalshi market rule evidence before semantic interpretation.

    The identifier hashes the rule-bearing market fields plus acquisition time.
    No station, source, timezone, or settlement semantics are inferred here.
    """
    blockers: list[str] = []
    ticker = _required_text(market, "ticker", blockers)
    title = _required_text(market, "title", blockers)
    rules_primary = _required_text(market, "rules_primary", blockers)
    subtitle = _optional_text(market.get("subtitle")) or ""
    rules_secondary = _optional_text(market.get("rules_secondary")) or ""
    if not acquired_at or not str(acquired_at).strip():
        blockers.append("RULE_SNAPSHOT_ACQUISITION_TIME_MISSING")

    if blockers:
        raise RuleSnapshotError("NO_PLAY_SETTLEMENT_AMBIGUITY", tuple(dict.fromkeys(blockers)))

    canonical = {
        "ticker": ticker,
        "title": title,
        "subtitle": subtitle,
        "rules_primary": rules_primary,
        "rules_secondary": rules_secondary,
        "strike_type": market.get("strike_type"),
        "floor_strike": market.get("floor_strike"),
        "cap_strike": market.get("cap_strike"),
        "functional_strike": market.get("functional_strike"),
        "close_time": market.get("close_time"),
        "expiration_time": market.get("expiration_time"),
        "acquired_at": acquired_at,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()

    return FrozenRuleSnapshot(
        rule_snapshot_id=f"kalshi-rule-{digest[:24]}",
        ticker=ticker.upper(),
        acquired_at=str(acquired_at),
        title=title,
        subtitle=subtitle,
        rules_primary=rules_primary,
        rules_secondary=rules_secondary,
        strike_type=_optional_text(market.get("strike_type")),
        floor_strike=_optional_float(market.get("floor_strike")),
        cap_strike=_optional_float(market.get("cap_strike")),
        functional_strike=_optional_text(market.get("functional_strike")),
        close_time=_optional_text(market.get("close_time")),
        expiration_time=_optional_text(market.get("expiration_time")),
        raw_market=dict(market),
    )


def _required_text(obj: Mapping[str, Any], key: str, blockers: list[str]) -> str:
    text = _optional_text(obj.get(key))
    if not text:
        blockers.append(f"RULE_FIELD_MISSING:{key}")
        return ""
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
