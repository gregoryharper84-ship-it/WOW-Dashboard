"""DAFT external signals: exact contracts, provenance, and research-only market math.

All inputs are caller-supplied captures. No Discord access or betting execution.
Market probabilities are *never* governed sporting probabilities.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CHANNELS = frozenset({
    "VIP_PICKS", "VIP_RECAPS", "SHARP_US_FOOTBALL", "SHARP_BASKETBALL",
    "SHARP_MLB", "SHARP_SOCCER", "SHARP_NHL", "SHARP_GENERAL",
})
CONTRACT_FIELDS = (
    "official_event_id", "market_family", "period", "selection",
    "line", "side", "settlement_basis", "overtime_rule",
)
MARKET_FIELDS = ("official_event_id", "market_family", "period", "line",
                 "settlement_basis", "overtime_rule")
HYPOTHESIS_PATTERNS = {
    "ROLE_USAGE_REDISTRIBUTION": r"\b(injur|out\b|absen|usage|touch|role|starting)\w*",
    "MINUTES_ROTATION_WORKLOAD": r"\b(minutes|rotation|workload|restriction|bench)\b",
    "DEFENSIVE_ASSIGNMENT_COVERAGE": r"\b(defender|assignment|drop coverage|switch|help defense)\b",
    "SHOT_PROFILE_MATCHUP": r"\b(catch.and.shoot|pull.up|rim|three.point|shot profile|matchup)\b",
    "BENEFICIARY_IDENTIFICATION": r"\b(benefit|replacement|opportunit|vacated|potential assists)\w*",
    "CORRELATION_EXPOSURE": r"\b(correlat|ladder|combo|parlay|same.game|exposure)\w*",
}


def timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None


def finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def contract_key(contract: Mapping[str, Any] | None) -> str | None:
    if not isinstance(contract, Mapping):
        return None
    if any(contract.get(f) is None or str(contract.get(f)).strip() == ""
           for f in CONTRACT_FIELDS if f != "line"):
        return None
    # For moneylines, an absent line is meaningful; for spreads/totals it is not.
    line = finite_number(contract.get("line"))
    family = str(contract["market_family"]).upper()
    if family in {"SPREAD", "TOTAL", "PLAYER_PROP", "ALTERNATE_SPREAD",
                  "ALTERNATE_TOTAL"} and line is None:
        return None
    canonical = [str(contract[field]).strip().casefold()
                 if field != "line" else (str(line) if line is not None else "NONE")
                 for field in CONTRACT_FIELDS]
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()[:32]


def same_market(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    # Missing settlement, period, or overtime semantics cannot compare as exact.
    for field in MARKET_FIELDS:
        if field == "line":
            continue
        if not a.get(field) or not b.get(field):
            return False
        if str(a[field]).strip().casefold() != str(b[field]).strip().casefold():
            return False
    if str(a.get("market_family")).upper() in {"SPREAD", "TOTAL", "PLAYER_PROP",
                                                "ALTERNATE_SPREAD", "ALTERNATE_TOTAL"}:
        return finite_number(a.get("line")) is not None and finite_number(a.get("line")) == finite_number(b.get("line"))
    return a.get("line") is None and b.get("line") is None


def same_contract(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    ka, kb = contract_key(a), contract_key(b)
    return ka is not None and ka == kb


def extract_prop_hypotheses(text: str | None) -> list[dict[str, Any]]:
    """Keyword-assisted extraction; every claim remains unverified research."""
    if not text:
        return []
    return [
        {"kind": kind, "claim_excerpt": text[:500], "verification_status": "UNVERIFIED",
         "independent_evidence_id": None, "model_input_effect": None}
        for kind, pattern in HYPOTHESIS_PATTERNS.items()
        if re.search(pattern, text, re.IGNORECASE)
    ]


def normalize_capture(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize one private input; preserve unknowns, never infer timestamps.

    Returns None only for the explicitly excluded VIP Chat.
    """
    channel = str(row.get("channel") or "").strip().upper()
    if channel == "VIP_CHAT":
        return None
    if channel not in CHANNELS:
        raise ValueError("Unsupported research channel")
    source_id = str(row.get("message_id") or "").strip()
    if not source_id:
        raise ValueError("Message ID required for evidence provenance")
    kind = str(row.get("kind") or "").upper()
    if kind not in {"ORIGINAL_ALERT", "UPDATE", "SOURCE_RESULT", "VIP_PICK", "VIP_RECAP"}:
        raise ValueError("Unsupported message kind")
    capture = timestamp(row.get("captured_at"))
    if capture is None:
        raise ValueError("Timezone-aware capture timestamp required")
    published = timestamp(row.get("published_at"))
    edited = timestamp(row.get("edited_at"))
    start = timestamp(row.get("event_start_at"))
    prospective = (
        published is not None and start is not None and
        published <= capture < start and
        (edited is None or published <= edited <= capture) and
        (row.get("edit_history_complete") is True or not row.get("edit_indicated"))
    )
    contract = row.get("contract")
    key = contract_key(contract)
    version = row.get("version")  # absence is a real unknown, not 'v1'
    version_key = hashlib.sha256(json.dumps(
        [channel, source_id, version, kind],
        separators=(",", ":"), default=str).encode()).hexdigest()[:32]
    instance = hashlib.sha256(json.dumps(
        [version_key, row.get("captured_at")],
        separators=(",", ":"), default=str).encode()).hexdigest()[:32]
    known = {"channel", "message_id", "kind", "captured_at", "published_at", "edited_at",
             "event_start_at", "edit_history_complete", "edit_indicated",
             "contract", "version", "message_url", "parent_alert_id",
             "source_estimated_probability", "source_reported_result",
             "independent_result", "text", "prices", "staking", "quality"}
    return {
        "schema_version": "WOW_DAFT_CAPTURE_V1", "instance_id": instance,
        "message_version_key": version_key,
        "source": "DAFT", "channel": channel, "message_id": source_id,
        "message_url": row.get("message_url"), "kind": kind,
        "parent_alert_id": row.get("parent_alert_id"), "version": version,
        "published_at": row.get("published_at"), "captured_at": row["captured_at"],
        "edited_at": row.get("edited_at"), "edit_history_complete": row.get("edit_history_complete"),
        "event_start_at": row.get("event_start_at"),
        "research_eligibility": "PROSPECTIVE" if prospective else "RETROSPECTIVE_ONLY",
        "contract": dict(contract) if isinstance(contract, Mapping) else None,
        "thesis_key": key, "source_estimated_probability": row.get("source_estimated_probability"),
        "source_reported_result": row.get("source_reported_result"),
        "independent_result": row.get("independent_result"),
        "prices": row.get("prices"), "source_staking": row.get("staking"),
        "evidence_quality": row.get("quality"),
        "hypotheses": extract_prop_hypotheses(row.get("text")) if channel == "VIP_PICKS" else [],
        "private_source_text": row.get("text"),
        "unknown_source_fields": {k: v for k, v in row.items() if k not in known},
        "probability_authority": False, "can_execute": False,
    }


def deduplicate_captures(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Version dedupe is distinct from exact-contract/thesis clustering."""
    versions: dict[str, dict[str, Any]] = {}
    for row in rows:
        normalized = normalize_capture(row)
        if normalized is not None:
            versions[normalized["instance_id"]] = normalized
    theses: dict[str, list[str]] = {}
    version_groups: dict[str, list[str]] = {}
    for item in versions.values():
        version_groups.setdefault(item["message_version_key"], []).append(item["instance_id"])
        # Unknown exact contracts never get merged as a guessed thesis.
        k = item["thesis_key"] or ("UNRESOLVED:" + item["instance_id"])
        theses.setdefault(k, []).append(item["instance_id"])
    return {"source_rows": len(rows), "distinct_capture_instances": len(versions),
            "distinct_message_versions": len(version_groups),
            "message_versions": version_groups,
            "messages": list(versions.values()), "thesis_clusters": theses,
            "can_execute": False}


def decimal_implied_probability(odds: Any) -> float | None:
    price = finite_number(odds)
    return 1.0 / price if price is not None and price > 1.0 else None


def normalized_no_vig(prices: Mapping[str, Any], outcomes: tuple[str, ...]) -> dict[str, float] | None:
    """Simple proportional de-vig; requires the *complete* two/three-way outcome set."""
    if len(outcomes) not in (2, 3) or set(prices) != set(outcomes):
        return None
    implied = {key: decimal_implied_probability(prices.get(key)) for key in outcomes}
    if any(value is None for value in implied.values()):
        return None
    denominator = sum(implied.values())
    if denominator <= 0:
        return None
    return {key: value / denominator for key, value in implied.items()}


def expected_return(p_win: Any, p_loss: Any, decimal_odds: Any) -> float | None:
    """Correct for push: EV = p(win)*(decimal-1) - p(loss)."""
    win, loss, odds = map(finite_number, (p_win, p_loss, decimal_odds))
    if (win is None or loss is None or odds is None or
        win < 0 or loss < 0 or win + loss > 1.0 + 1e-10 or odds <= 1.0):
        return None
    return win * (odds - 1.0) - loss


def quote_age_seconds(updated_at: Any, now: str) -> float | None:
    observed, reference = timestamp(updated_at), timestamp(now)
    if observed is None or reference is None or observed > reference:
        return None
    return (reference - observed).total_seconds()


def comparable_clv(entry: Mapping[str, Any], closing: Mapping[str, Any]) -> float | None:
    if not same_contract(entry.get("contract"), closing.get("contract")):
        return None
    entry_odds, closing_odds = (finite_number(entry.get("decimal_odds")),
                                finite_number(closing.get("decimal_odds")))
    if entry_odds is None or closing_odds is None or min(entry_odds, closing_odds) <= 1:
        return None
    if timestamp(entry.get("quote_at")) is None or timestamp(closing.get("quote_at")) is None:
        return None
    if timestamp(entry["quote_at"]) >= timestamp(closing["quote_at"]):
        return None
    return entry_odds - closing_odds


def analyze_sharp_alert(alert: Mapping[str, Any], reference: Mapping[str, Any],
                        offered: Mapping[str, Any], *, now: str,
                        closing: Mapping[str, Any] | None = None,
                        max_quote_age_seconds: int = 120) -> dict[str, Any]:
    """No alternative-line interpolation, no verification of market-moving cause."""
    contract = alert.get("contract")
    exact = same_contract(contract, offered.get("contract"))
    reference_match = same_market(contract or {}, reference.get("contract") or {})
    age = quote_age_seconds(offered.get("quote_at"), now)
    fresh = age is not None and age <= max_quote_age_seconds
    outcomes = tuple(reference.get("outcomes") or ())
    family = str((contract or {}).get("market_family") or "").upper()
    three_way = family in {"1X2", "SOCCER_1X2", "THREE_WAY_MONEYLINE"}
    complete_space = (set(outcomes) == {"home", "draw", "away"} if three_way
                      else len(outcomes) == 2)
    source_probs = (normalized_no_vig(reference.get("prices") or {}, outcomes)
                    if reference_match and complete_space else None)
    side = str(contract.get("side") or "").lower() if isinstance(contract, Mapping) else None
    market_p = source_probs.get(side) if source_probs else None
    # Market-only EV is permitted only for push-free binary, exact contracts.
    push_free = bool(contract.get("push_possible") is False) if isinstance(contract, Mapping) else False
    market_ev = (expected_return(market_p, 1.0 - market_p, offered.get("decimal_odds"))
                 if exact and fresh and push_free and len(outcomes) == 2 and market_p is not None else None)
    return {
        "contract_match": "EXACT" if exact else "UNRESOLVED_OR_DIFFERENT",
        "reference_market_match": reference_match, "quote_age_seconds": age,
        "quote_fresh": fresh, "source_market_probability": alert.get("source_estimated_probability"),
        "independent_market_no_vig": source_probs,
        "independent_market_ev_push_free_only": market_ev,
        "closing_line_value_decimal": comparable_clv(offered, closing) if closing else None,
        "method": "PROPORTIONAL_DEVIG_COMPLETE_OUTCOME_SET",
        "sharp_money_proven": False, "alternative_line_conversion_used": False,
        "model_probability_mutated": False, "can_execute": False,
    }
