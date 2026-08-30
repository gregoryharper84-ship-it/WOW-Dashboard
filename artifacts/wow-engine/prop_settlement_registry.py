"""Server-owned authoritative settlement-rule hydration for governed props.

The caller may identify the source platform/provider, but cannot define the
operative settlement semantics. Rules are loaded only from the reviewed
Supabase registry and are effective-dated against the event start time.
Failures are downstream settlement holds and never erase a completed model
probability. Execution remains disabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from prop_settlement import SETTLEMENT_RULE_UNRESOLVED, SettlementRule

SETTLEMENT_RULE_CONFLICT = "WOW_HOLD_SETTLEMENT_RULE_CONFLICT"
SETTLEMENT_PROVIDER_UNRESOLVED = "WOW_HOLD_SETTLEMENT_PROVIDER_UNRESOLVED"
SETTLEMENT_RULE_REGISTRY_UNAVAILABLE = "WOW_HOLD_SETTLEMENT_RULE_REGISTRY_UNAVAILABLE"

_PROVIDER_ALIASES = {
    "PRIZE PICKS": "PRIZEPICKS",
    "PRIZE_PICKS": "PRIZEPICKS",
    "PRIZEPICKS": "PRIZEPICKS",
}


@dataclass(frozen=True)
class SettlementRuleResolution:
    status: str
    blocker: Optional[str]
    rule: Optional[SettlementRule]
    authority: Optional[str]
    provider: Optional[str]
    rule_id: Optional[str]
    rule_version: Optional[str]
    source_ref: Optional[str]
    source_hash: Optional[str]
    money_semantics: Optional[str]
    observed_rule_status: str
    can_execute: bool = False


def normalize_provider(value: object) -> Optional[str]:
    raw = " ".join(str(value or "").strip().upper().replace("-", " ").split())
    if not raw:
        return None
    return _PROVIDER_ALIASES.get(raw, raw.replace(" ", "_"))


def _parse_ts(value: object) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _norm(value: object) -> str:
    return str(value or "").strip().upper()


def _hold(blocker: str, *, provider: Optional[str], observed_rule_status: str = "NOT_SUPPLIED") -> SettlementRuleResolution:
    return SettlementRuleResolution(
        status="HOLD",
        blocker=blocker,
        rule=None,
        authority=None,
        provider=provider,
        rule_id=None,
        rule_version=None,
        source_ref=None,
        source_hash=None,
        money_semantics=None,
        observed_rule_status=observed_rule_status,
        can_execute=False,
    )


def _semantic_tuple(rule: SettlementRule) -> tuple[str, str, str, str]:
    return (
        _norm(rule.settlement_basis),
        _norm(rule.boundary_operator),
        _norm(rule.equality_treatment),
        _norm(rule.void_treatment),
    )


def resolve_prop_settlement_rule(
    *,
    client: Any,
    provider: object,
    sport: object,
    stat_type: object,
    period: object,
    direction: object,
    event_start_time: object,
    observed_rule: Optional[SettlementRule] = None,
) -> SettlementRuleResolution:
    canonical_provider = normalize_provider(provider)
    if canonical_provider is None:
        return _hold(SETTLEMENT_PROVIDER_UNRESOLVED, provider=None)

    event_time = _parse_ts(event_start_time)
    if event_time is None:
        return _hold(SETTLEMENT_RULE_UNRESOLVED, provider=canonical_provider)

    canonical_sport = _norm(sport)
    canonical_stat = _norm(stat_type)
    canonical_period = _norm(period)
    canonical_direction = _norm(direction)
    if canonical_direction not in {"MORE", "LESS"}:
        return _hold(SETTLEMENT_RULE_UNRESOLVED, provider=canonical_provider)

    try:
        response = (
            client.table("wow_prop_settlement_rule_registry")
            .select("rule_id,provider,sport,stat_type,period,direction,settlement_basis,boundary_operator,equality_treatment,void_treatment,money_semantics,rule_version,source_ref,source_hash,effective_from,effective_to,lifecycle_state,can_execute")
            .eq("provider", canonical_provider)
            .eq("sport", canonical_sport)
            .eq("period", canonical_period)
            .eq("direction", canonical_direction)
            .eq("lifecycle_state", "REVIEWED_CERTIFIED")
            .execute()
        )
        rows = list(getattr(response, "data", None) or [])
    except Exception:
        return _hold(SETTLEMENT_RULE_REGISTRY_UNAVAILABLE, provider=canonical_provider)

    eligible: list[dict[str, Any]] = []
    for row in rows:
        stat = _norm(row.get("stat_type"))
        if stat not in {canonical_stat, "*"}:
            continue
        start = _parse_ts(row.get("effective_from"))
        end = _parse_ts(row.get("effective_to")) if row.get("effective_to") else None
        if start is None or event_time < start or (end is not None and event_time >= end):
            continue
        if row.get("can_execute") is not False:
            continue
        eligible.append(row)

    exact = [row for row in eligible if _norm(row.get("stat_type")) == canonical_stat]
    candidates = exact or [row for row in eligible if _norm(row.get("stat_type")) == "*"]
    if not candidates:
        return _hold(SETTLEMENT_RULE_UNRESOLVED, provider=canonical_provider)

    # Multiple active rows are acceptable only if semantics are identical; use
    # the latest effective rule deterministically. Differing active semantics
    # are a hard conflict and must not be guessed through.
    candidates.sort(key=lambda row: str(row.get("effective_from") or ""), reverse=True)
    semantic_keys = {
        (
            _norm(row.get("settlement_basis")),
            _norm(row.get("boundary_operator")),
            _norm(row.get("equality_treatment")),
            _norm(row.get("void_treatment")),
            _norm(row.get("money_semantics")),
        )
        for row in candidates
    }
    if len(semantic_keys) != 1:
        return _hold(SETTLEMENT_RULE_CONFLICT, provider=canonical_provider)

    selected = candidates[0]
    rule = SettlementRule(
        settlement_basis=str(selected["settlement_basis"]),
        boundary_operator=str(selected["boundary_operator"]),
        equality_treatment=str(selected["equality_treatment"]),
        void_treatment=str(selected["void_treatment"]),
        rule_version=str(selected["rule_version"]),
        source=f"SERVER_REGISTRY:{selected['rule_id']}:{selected['source_ref']}",
        void_probability_mass=0.0,
        money_semantics=str(selected.get("money_semantics") or "FIXED_ODDS_RETURN_STAKE"),
    )

    observed_status = "NOT_SUPPLIED"
    if observed_rule is not None:
        observed_status = "MATCH"
        if _semantic_tuple(observed_rule) != _semantic_tuple(rule):
            return _hold(
                SETTLEMENT_RULE_CONFLICT,
                provider=canonical_provider,
                observed_rule_status="CONFLICT_QUARANTINED",
            )

    return SettlementRuleResolution(
        status="PASS",
        blocker=None,
        rule=rule,
        authority="SERVER_REGISTRY_REVIEWED_CERTIFIED",
        provider=canonical_provider,
        rule_id=str(selected["rule_id"]),
        rule_version=str(selected["rule_version"]),
        source_ref=str(selected["source_ref"]),
        source_hash=str(selected["source_hash"]),
        money_semantics=str(selected.get("money_semantics") or "FIXED_ODDS_RETURN_STAKE"),
        observed_rule_status=observed_status,
        can_execute=False,
    )
