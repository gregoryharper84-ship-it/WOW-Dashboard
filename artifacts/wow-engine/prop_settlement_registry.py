"""Server-owned authoritative settlement-rule hydration for governed props.

The caller may identify the source platform/provider, but cannot define the
operative settlement semantics. Reviewed rules are code-certified so the live
runtime does not depend on database reachability. A synchronized Supabase
registry may supply an equally governed reviewed rule when available.

Failures are downstream settlement holds and never erase a completed model
probability. Execution remains disabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
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

# Reviewed code-certified rules. Source documents are official provider pages;
# these rows are effective-dated and versioned. They are deliberately narrow:
# only PrizePicks MLB/WNBA full-game player picks are certified here.
# Provider-specific reboot eligibility is retained as notes/provenance; the
# generic settlement mapper only consumes the shared tie/DNP/line semantics.
_BUILTIN_REVIEWED_RULES: tuple[dict[str, Any], ...] = (
    {
        "rule_id": "code:prizepicks:mlb:full_game:more:2026-08-11",
        "provider": "PRIZEPICKS",
        "sport": "MLB",
        "stat_type": "*",
        "period": "FULL_GAME",
        "direction": "MORE",
        "settlement_basis": "PRIZEPICKS_OFFICIAL_SCORING",
        "boundary_operator": "GT",
        "equality_treatment": "PUSH",
        "void_treatment": "REMOVE_LEG_REPRICE",
        "money_semantics": "LINEUP_CONTEXT_REQUIRED",
        "rule_version": "PRIZEPICKS_PLAYER_PICKS_2026_08_11",
        "source_ref": "https://www.prizepicks.com/help-center/dnps-reboots-and-ties",
        "source_refs": (
            "https://www.prizepicks.com/help-center/dnps-reboots-and-ties",
            "https://www.prizepicks.com/reboots",
            "https://www.prizepicks.com/help-center/official-scoring-providers",
        ),
        "source_observed_at": "2026-08-30T22:20:00+00:00",
        "effective_from": "2026-08-11T00:00:00+00:00",
        "effective_to": None,
        "lifecycle_state": "REVIEWED_CERTIFIED",
        "can_execute": False,
        "notes": "MLB MORE: full-game hitters may be reboot-eligible under provider conditions; pitchers are not reboot eligible.",
    },
    {
        "rule_id": "code:prizepicks:mlb:full_game:less:2026-08-11",
        "provider": "PRIZEPICKS",
        "sport": "MLB",
        "stat_type": "*",
        "period": "FULL_GAME",
        "direction": "LESS",
        "settlement_basis": "PRIZEPICKS_OFFICIAL_SCORING",
        "boundary_operator": "LT",
        "equality_treatment": "PUSH",
        "void_treatment": "REMOVE_LEG_REPRICE",
        "money_semantics": "LINEUP_CONTEXT_REQUIRED",
        "rule_version": "PRIZEPICKS_PLAYER_PICKS_2026_08_11",
        "source_ref": "https://www.prizepicks.com/help-center/dnps-reboots-and-ties",
        "source_refs": (
            "https://www.prizepicks.com/help-center/dnps-reboots-and-ties",
            "https://www.prizepicks.com/reboots",
            "https://www.prizepicks.com/help-center/official-scoring-providers",
        ),
        "source_observed_at": "2026-08-30T22:20:00+00:00",
        "effective_from": "2026-08-11T00:00:00+00:00",
        "effective_to": None,
        "lifecycle_state": "REVIEWED_CERTIFIED",
        "can_execute": False,
        "notes": "MLB LESS remains live under the provider reboot framework; DNP/tie removal reprices the lineup.",
    },
    {
        "rule_id": "code:prizepicks:wnba:full_game:more:2026-08-11",
        "provider": "PRIZEPICKS",
        "sport": "WNBA",
        "stat_type": "*",
        "period": "FULL_GAME",
        "direction": "MORE",
        "settlement_basis": "PRIZEPICKS_OFFICIAL_SCORING",
        "boundary_operator": "GT",
        "equality_treatment": "PUSH",
        "void_treatment": "REMOVE_LEG_REPRICE",
        "money_semantics": "LINEUP_CONTEXT_REQUIRED",
        "rule_version": "PRIZEPICKS_PLAYER_PICKS_2026_08_11",
        "source_ref": "https://www.prizepicks.com/help-center/dnps-reboots-and-ties",
        "source_refs": (
            "https://www.prizepicks.com/help-center/dnps-reboots-and-ties",
            "https://www.prizepicks.com/reboots",
            "https://www.prizepicks.com/help-center/official-scoring-providers",
        ),
        "source_observed_at": "2026-08-30T22:20:00+00:00",
        "effective_from": "2026-08-11T00:00:00+00:00",
        "effective_to": None,
        "lifecycle_state": "REVIEWED_CERTIFIED",
        "can_execute": False,
        "notes": "WNBA MORE reboot eligibility requires a first-half exit and no second-half return under current provider rules.",
    },
    {
        "rule_id": "code:prizepicks:wnba:full_game:less:2026-08-11",
        "provider": "PRIZEPICKS",
        "sport": "WNBA",
        "stat_type": "*",
        "period": "FULL_GAME",
        "direction": "LESS",
        "settlement_basis": "PRIZEPICKS_OFFICIAL_SCORING",
        "boundary_operator": "LT",
        "equality_treatment": "PUSH",
        "void_treatment": "REMOVE_LEG_REPRICE",
        "money_semantics": "LINEUP_CONTEXT_REQUIRED",
        "rule_version": "PRIZEPICKS_PLAYER_PICKS_2026_08_11",
        "source_ref": "https://www.prizepicks.com/help-center/dnps-reboots-and-ties",
        "source_refs": (
            "https://www.prizepicks.com/help-center/dnps-reboots-and-ties",
            "https://www.prizepicks.com/reboots",
            "https://www.prizepicks.com/help-center/official-scoring-providers",
        ),
        "source_observed_at": "2026-08-30T22:20:00+00:00",
        "effective_from": "2026-08-11T00:00:00+00:00",
        "effective_to": None,
        "lifecycle_state": "REVIEWED_CERTIFIED",
        "can_execute": False,
        "notes": "WNBA LESS remains live under the provider reboot framework; DNP/tie removal reprices the lineup.",
    },
)


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


def _semantic_tuple(rule: SettlementRule) -> tuple[str, str, str, str, str]:
    return (
        _norm(rule.settlement_basis),
        _norm(rule.boundary_operator),
        _norm(rule.equality_treatment),
        _norm(rule.void_treatment),
        _norm(rule.money_semantics),
    )


def _row_semantic_tuple(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        _norm(row.get("settlement_basis")),
        _norm(row.get("boundary_operator")),
        _norm(row.get("equality_treatment")),
        _norm(row.get("void_treatment")),
        _norm(row.get("money_semantics")),
    )


def _fingerprint(row: dict[str, Any]) -> str:
    payload = {
        "provider": row.get("provider"),
        "sport": row.get("sport"),
        "stat_type": row.get("stat_type"),
        "period": row.get("period"),
        "direction": row.get("direction"),
        "semantics": _row_semantic_tuple(row),
        "rule_version": row.get("rule_version"),
        "source_ref": row.get("source_ref"),
        "effective_from": row.get("effective_from"),
        "effective_to": row.get("effective_to"),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _eligible_rows(
    rows: list[dict[str, Any]],
    *,
    provider: str,
    sport: str,
    stat_type: str,
    period: str,
    direction: str,
    event_time: datetime,
) -> list[dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    for row in rows:
        if normalize_provider(row.get("provider")) != provider:
            continue
        if _norm(row.get("sport")) != sport or _norm(row.get("period")) != period:
            continue
        if _norm(row.get("direction")) != direction:
            continue
        if _norm(row.get("lifecycle_state")) != "REVIEWED_CERTIFIED":
            continue
        stat = _norm(row.get("stat_type"))
        if stat not in {stat_type, "*"}:
            continue
        start = _parse_ts(row.get("effective_from"))
        end = _parse_ts(row.get("effective_to")) if row.get("effective_to") else None
        if start is None or event_time < start or (end is not None and event_time >= end):
            continue
        if row.get("can_execute") is not False:
            continue
        eligible.append(dict(row))
    exact = [row for row in eligible if _norm(row.get("stat_type")) == stat_type]
    return exact or [row for row in eligible if _norm(row.get("stat_type")) == "*"]


def _select_rule(rows: list[dict[str, Any]], *, authority: str, provider: str, observed_rule: Optional[SettlementRule]) -> SettlementRuleResolution:
    if not rows:
        return _hold(SETTLEMENT_RULE_UNRESOLVED, provider=provider)
    rows.sort(key=lambda row: str(row.get("effective_from") or ""), reverse=True)
    semantic_keys = {_row_semantic_tuple(row) for row in rows}
    if len(semantic_keys) != 1:
        return _hold(SETTLEMENT_RULE_CONFLICT, provider=provider)

    selected = rows[0]
    rule = SettlementRule(
        settlement_basis=str(selected["settlement_basis"]),
        boundary_operator=str(selected["boundary_operator"]),
        equality_treatment=str(selected["equality_treatment"]),
        void_treatment=str(selected["void_treatment"]),
        rule_version=str(selected["rule_version"]),
        source=f"{authority}:{selected['rule_id']}:{selected['source_ref']}",
        void_probability_mass=0.0,
        money_semantics=str(selected.get("money_semantics") or "FIXED_ODDS_RETURN_STAKE"),
    )

    observed_status = "NOT_SUPPLIED"
    if observed_rule is not None:
        observed_status = "MATCH"
        if _semantic_tuple(observed_rule) != _semantic_tuple(rule):
            return _hold(
                SETTLEMENT_RULE_CONFLICT,
                provider=provider,
                observed_rule_status="CONFLICT_QUARANTINED",
            )

    return SettlementRuleResolution(
        status="PASS",
        blocker=None,
        rule=rule,
        authority=authority,
        provider=provider,
        rule_id=str(selected["rule_id"]),
        rule_version=str(selected["rule_version"]),
        source_ref=str(selected["source_ref"]),
        source_hash=str(selected.get("source_hash") or _fingerprint(selected)),
        money_semantics=str(selected.get("money_semantics") or "FIXED_ODDS_RETURN_STAKE"),
        observed_rule_status=observed_status,
        can_execute=False,
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

    # Prefer synchronized reviewed DB rules when the table is reachable and
    # contains an eligible rule. Database absence/unavailability is not allowed
    # to erase the code-certified rule authority.
    db_rows: list[dict[str, Any]] = []
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
        db_rows = list(getattr(response, "data", None) or [])
    except Exception:
        db_rows = []

    eligible_db = _eligible_rows(
        db_rows,
        provider=canonical_provider,
        sport=canonical_sport,
        stat_type=canonical_stat,
        period=canonical_period,
        direction=canonical_direction,
        event_time=event_time,
    )
    if eligible_db:
        return _select_rule(
            eligible_db,
            authority="SERVER_DB_REGISTRY_REVIEWED_CERTIFIED",
            provider=canonical_provider,
            observed_rule=observed_rule,
        )

    eligible_builtin = _eligible_rows(
        [dict(row) for row in _BUILTIN_REVIEWED_RULES],
        provider=canonical_provider,
        sport=canonical_sport,
        stat_type=canonical_stat,
        period=canonical_period,
        direction=canonical_direction,
        event_time=event_time,
    )
    if eligible_builtin:
        return _select_rule(
            eligible_builtin,
            authority="SERVER_CODE_REGISTRY_REVIEWED_CERTIFIED",
            provider=canonical_provider,
            observed_rule=observed_rule,
        )

    return _hold(SETTLEMENT_RULE_UNRESOLVED, provider=canonical_provider)
