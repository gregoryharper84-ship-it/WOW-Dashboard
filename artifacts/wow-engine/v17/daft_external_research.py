"""DAFT external evidence intake and exact-contract shadow analysis for WOW V17.

Research only. Source estimates are NOT certified sporting probabilities. No
Discord connection, provider fetch, model invocation, ledger mutation or bet
execution occurs here. Historical paid material is never bundled with this code.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite, log
import json
from typing import Any, Mapping, Sequence

from v17.rundown_market_value import american_implied_probability, american_to_decimal

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
MODE = "RESEARCH_SHADOW_ONLY"
KINDS = frozenset({"VIP_PICK", "VIP_RECAP", "SHARP_ALERT", "SHARP_UPDATE", "SHARP_RESULT"})
CLAIM_KINDS = frozenset({
    "ROLE_USAGE_REDISTRIBUTION", "MINUTES_ROTATION_WORKLOAD",
    "DEFENSIVE_ASSIGNMENT", "SHOT_PROFILE_COVERAGE",
    "BENEFICIARY_ALLOCATION", "CORRELATION_EXPOSURE",
})
EXACT_KEYS = (
    "sport", "league", "official_event_id", "market_family", "period",
    "selection", "exact_line", "settlement_rule", "outcome_space",
)


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if isfinite(value) else None


def exact_contract_key(row: Mapping[str, Any]) -> tuple[str, ...] | None:
    """Do not fuzzy-match team names, adjacent lines, or settlement variants."""
    parts = []
    for field in EXACT_KEYS:
        value = row.get(field)
        if value is None or isinstance(value, bool) or str(value).strip() == "":
            return None
        parts.append(str(value).strip().casefold())
    return tuple(parts)


def normalize_research_record(raw: Mapping[str, Any], *, capture_at: str) -> dict[str, Any]:
    """Normalize private intake without inventing published, quote, or event times."""
    kind = str(raw.get("kind") or "").upper()
    if kind not in KINDS:
        raise ValueError("Unsupported research input kind")
    if not raw.get("message_id") or not raw.get("channel") or _time(capture_at) is None:
        raise ValueError("Message identity, channel, and aware capture time required")
    published = _time(raw.get("published_at"))
    captured = _time(capture_at)
    event = _time(raw.get("event_start_at"))
    assert captured is not None
    attested = raw.get("capture_attested") is True
    prospective = bool(
        attested and published is not None and event is not None
        and published <= captured < event
        and exact_contract_key(raw) is not None
    )
    # Source bodies remain only in the private intake. Do not emit or log them.
    version_payload = {
        "source": raw.get("source") or "DAFT",
        "channel": raw["channel"], "message_id": str(raw["message_id"]),
        "edit_at": raw.get("edit_at"), "version": raw.get("version"),
        "body": raw.get("body"), "market": [raw.get(k) for k in EXACT_KEYS],
        "source_result": raw.get("source_reported_result"),
    }
    digest = sha256(json.dumps(version_payload, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "source": raw.get("source") or "DAFT",
        "kind": kind, "channel": raw["channel"],
        "message_id": str(raw["message_id"]), "message_url": raw.get("message_url"),
        "published_at": raw.get("published_at"), "capture_at": capture_at,
        "edit_at": raw.get("edit_at"), "version": raw.get("version"),
        "version_sha256": digest, "alert_id": raw.get("alert_id"),
        "event_start_at": raw.get("event_start_at"),
        "contract": {k: raw.get(k) for k in EXACT_KEYS},
        "exact_contract_key": exact_contract_key(raw),
        "direction": raw.get("direction"),
        "odds": raw.get("odds"), "stake_as_reported": raw.get("stake_as_reported"),
        "quote_timestamp": raw.get("quote_timestamp"),
        "source_market_probability": raw.get("source_market_probability"),
        "source_reported_result": raw.get("source_reported_result"),
        "independently_reconciled_result": raw.get("independently_reconciled_result"),
        "source_reasoning_claims": raw.get("source_reasoning_claims") or [],
        "evidence_quality": raw.get("evidence_quality"),
        "capture_attested": attested, "prospective_eligible": prospective,
        "research_eligibility": "PROSPECTIVE_SHADOW" if prospective else "RETROSPECTIVE_OR_UNRESOLVED",
        "prediction_authority": False, "can_execute": False,
    }


def dedupe_messages_and_theses(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Message versions and independent theses are different dedupe dimensions."""
    versions: dict[tuple[str, str, str], dict[str, Any]] = {}
    theses: dict[tuple[str, ...], list[str]] = {}
    unresolved = []
    for item in records:
        key = (str(item["channel"]), str(item["message_id"]), str(item["version_sha256"]))
        versions.setdefault(key, dict(item))
        contract = item.get("exact_contract_key")
        if contract:
            theses.setdefault(tuple(contract), []).append(str(item["message_id"]))
        else:
            unresolved.append(str(item["message_id"]))
    return {
        "unique_versions": list(versions.values()),
        "thesis_message_ids": {"|".join(key): sorted(set(ids)) for key, ids in theses.items()},
        "unresolved_contract_message_ids": sorted(set(unresolved)),
        "duplicate_version_instances": len(records) - len(versions),
    }


def structure_claims(claims: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """No free-text claim becomes verified merely because its mechanism sounds plausible."""
    out = []
    for claim in claims:
        kind = str(claim.get("mechanism") or "").upper()
        if kind not in CLAIM_KINDS:
            continue
        evidence = list(claim.get("independent_evidence") or [])
        contradictions = list(claim.get("contradicting_evidence") or [])
        verified = claim.get("verification_status") == "INDEPENDENTLY_VERIFIED" and bool(evidence)
        out.append({
            "mechanism": kind, "claim": claim.get("claim"),
            "verification_status": "INDEPENDENTLY_VERIFIED" if verified else "UNVERIFIED",
            "independent_evidence": evidence, "contradicting_evidence": contradictions,
            "predictive_improvement_demonstrated": False,
            "probability_effect": None,
        })
    return out


def no_vig_from_complete_quotes(
    quotes: Sequence[Mapping[str, Any]], *, outcome_space: Sequence[str],
) -> dict[str, Any]:
    """Proportional normalization, only one bookmaker/snapshot/contract."""
    expected = tuple(outcome_space)
    if len(expected) not in (2, 3) or len(set(expected)) != len(expected):
        return {"status": "INCOMPLETE_OUTCOME_SPACE", "probabilities": None}
    if len(quotes) != len(expected):
        return {"status": "INCOMPLETE_OUTCOME_SPACE", "probabilities": None}
    first = quotes[0] if quotes else {}
    contract = exact_contract_key(first)
    if contract is None or not first.get("bookmaker") or _time(first.get("quote_timestamp")) is None:
        return {"status": "UNVERIFIED_QUOTE_IDENTITY_OR_TIME", "probabilities": None}
    by_outcome = {}
    for quote in quotes:
        if (exact_contract_key(quote) != contract
                or quote.get("bookmaker") != first["bookmaker"]
                or quote.get("quote_timestamp") != first["quote_timestamp"]):
            return {"status": "UNVERIFIED_QUOTE_IDENTITY_OR_TIME", "probabilities": None}
        outcome = quote.get("outcome")
        probability = american_implied_probability(quote.get("american_odds"))
        if outcome not in expected or outcome in by_outcome or probability is None:
            return {"status": "INCOMPLETE_OUTCOME_SPACE", "probabilities": None}
        by_outcome[outcome] = probability
    if set(by_outcome) != set(expected):
        return {"status": "INCOMPLETE_OUTCOME_SPACE", "probabilities": None}
    denominator = sum(by_outcome.values())
    if denominator <= 0:
        return {"status": "INCOMPLETE_OUTCOME_SPACE", "probabilities": None}
    return {
        "status": "RESEARCH_ESTIMATE_ONLY",
        "method": "PROPORTIONAL_NORMALIZATION", "source_market_probability": None,
        "probabilities": {k: by_outcome[k] / denominator for k in expected},
        "raw_implied": by_outcome, "overround": denominator - 1,
        "quote_timestamp": first["quote_timestamp"], "bookmaker": first["bookmaker"],
        "prediction_authority": False, "can_execute": False,
    }


def expected_return_per_unit(*, p_win: float, p_loss: float, american_odds: float) -> float:
    """Pushes receive zero net return; supports binary and three-way contracts."""
    p_win, p_loss = _num(p_win), _num(p_loss)
    decimal = american_to_decimal(american_odds)
    if (p_win is None or p_loss is None or decimal is None
            or p_win < 0 or p_loss < 0 or p_win + p_loss > 1 + 1e-12):
        raise ValueError("Invalid settlement probabilities or offered odds")
    return p_win * (decimal - 1.0) - p_loss


def quote_age_seconds(quote_timestamp: str | None, observed_at: str) -> float | None:
    at, observed = _time(quote_timestamp), _time(observed_at)
    if at is None or observed is None or at > observed:
        return None
    return (observed - at).total_seconds()


def exact_contract_clv(entry: Mapping[str, Any], closing: Mapping[str, Any]) -> float | None:
    """American-odds implied probability delta at the same exact contract."""
    if exact_contract_key(entry) is None or exact_contract_key(entry) != exact_contract_key(closing):
        return None
    et, ct = _time(entry.get("quote_timestamp")), _time(closing.get("quote_timestamp"))
    e = american_implied_probability(entry.get("american_odds"))
    c = american_implied_probability(closing.get("american_odds"))
    if et is None or ct is None or ct < et or e is None or c is None:
        return None
    return c - e  # positive if chosen side shortened after entry


def reconcile_alert_result(alert: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    """Never grade a primary alert line using a different selected offer."""
    match = exact_contract_key(alert) is not None and exact_contract_key(alert) == exact_contract_key(result)
    return {
        "alert_id": alert.get("alert_id"),
        "result_alert_id": result.get("alert_id"),
        "exact_selected_offer_match": match and alert.get("alert_id") == result.get("alert_id"),
        "source_reported_result": result.get("source_reported_result"),
        "independently_verified_result": result.get("independently_reconciled_result"),
        "eligible_for_exact_grade": bool(
            match and alert.get("alert_id") == result.get("alert_id")
            and result.get("independently_reconciled_result") is not None
        ),
        "can_execute": False,
    }


def llp_research_annotation(
    governed_row: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
    *,
    observed_at: str,
    max_quote_age_seconds: int = 120,
) -> dict[str, Any]:
    """Appenditive shadow note: never changes the governed row or its rank."""
    key = exact_contract_key(governed_row)
    matches = [o for o in observations if key is not None and exact_contract_key(o.get("contract", o)) == key]
    prospective = [o for o in matches if o.get("prospective_eligible") is True]
    fresh = [
        o for o in prospective if o.get("quote_timestamp")
        and (age := quote_age_seconds(o.get("quote_timestamp"), observed_at)) is not None
        and age <= max_quote_age_seconds
    ]
    selected = fresh[-1] if fresh else None
    source_direction = selected.get("direction") if selected else None
    row_direction = governed_row.get("direction")
    agreement = ("AGREES" if source_direction == row_direction else "DISAGREES") if (
        source_direction and row_direction and selected
    ) else "NOT_COMPARABLE"
    return {
        "mode": MODE,
        "external_signal": "AVAILABLE" if selected else "UNAVAILABLE",
        "exact_contract_match": bool(selected),
        "directional_agreement": agreement,
        "quote_freshness_seconds": quote_age_seconds(selected["quote_timestamp"], observed_at) if selected else None,
        "source_market_probability": selected.get("source_market_probability") if selected else None,
        "market_estimate_is_governed_probability": False,
        "verified_claims": [
            {"mechanism": claim["mechanism"], "verification_status": claim["verification_status"]}
            for claim in structure_claims(selected.get("source_reasoning_claims") or [])
        ] if selected else [],
        "comparable_clv": None,
        "research_eligibility": "PROSPECTIVE_SHADOW" if selected else "UNRESOLVED",
        "unresolved_reason": None if selected else (
            "NO_EXACT_CONTRACT" if not matches else
            "NO_ATTESTED_PROSPECTIVE_CAPTURE_OR_FRESH_QUOTE"
        ),
        "probability_mutated": False,
        "terminal_mutated": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def evaluate_frozen_shadow(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Grade only exact, prospectively captured binary observations; no retrofitting."""
    count = 0
    brier = 0.0
    logloss = 0.0
    outcomes = set()
    for row in rows:
        if (row.get("prospective_eligible") is not True or
                row.get("exact_result_verified") is not True or
                row.get("same_contract_settlement") is not True):
            continue
        p = _num(row.get("governed_calibrated_probability"))
        y = row.get("outcome")
        if p is None or not 0 < p < 1 or type(y) is not int or y not in (0, 1):
            continue
        identity = (row.get("official_event_id"), row.get("canonical_thesis_id"))
        if any(x in (None, "") for x in identity) or identity in outcomes:
            continue
        outcomes.add(identity)
        count += 1
        brier += (p - y) ** 2
        logloss += -(y * log(p) + (1 - y) * log(1 - p))
    return {
        "graded_unique_theses": count,
        "brier": brier / count if count else None,
        "log_loss": logloss / count if count else None,
        "incremental_value_vs_llp": None,
        "promotable": False,
        "can_execute": False,
    }



def analyze_reference_and_offer(
    opening: Mapping[str, Any], current: Mapping[str, Any],
    offered: Mapping[str, Any], *,
    observed_at: str,
    max_quote_age_seconds: int = 120,
) -> dict[str, Any]:
    """Auditable same-contract Pinnacle/reference move vs available-book offer.

    A source-estimated fair price is never promoted to an independent estimate.
    Alternative lines and unmatched periods remain not comparable.
    """
    key = exact_contract_key(opening)
    if key is None or key != exact_contract_key(current) or key != exact_contract_key(offered):
        return {"status": "NOT_COMPARABLE", "reason": "EXACT_CONTRACT_MISMATCH",
                "can_execute": False}
    first = _time(opening.get("quote_timestamp"))
    now = _time(current.get("quote_timestamp"))
    age = quote_age_seconds(offered.get("quote_timestamp"), observed_at)
    p_first = american_implied_probability(opening.get("american_odds"))
    p_now = american_implied_probability(current.get("american_odds"))
    offered_decimal = american_to_decimal(offered.get("american_odds"))
    if first is None or now is None or now < first or p_first is None or p_now is None:
        return {"status": "NOT_COMPARABLE", "reason": "REFERENCE_QUOTE_UNRESOLVED",
                "can_execute": False}
    if age is None or age > max_quote_age_seconds or offered_decimal is None:
        return {"status": "NOT_COMPARABLE", "reason": "OFFER_QUOTE_STALE_OR_UNRESOLVED",
                "can_execute": False}
    return {
        "status": "MARKET_RESEARCH_ONLY",
        "reference_raw_implied_move_pp": (p_now - p_first) * 100.0,
        "reference_move_minutes": (now - first).total_seconds() / 60.0,
        "offered_american_odds": offered["american_odds"],
        "offered_quote_age_seconds": age,
        "offered_decimal_odds": offered_decimal,
        "offered_break_even_probability": american_implied_probability(offered["american_odds"]),
        "source_market_probability": offered.get("source_market_probability"),
        "independent_fair_probability": None,
        "expected_return_per_unit": None,
        "alternative_line_conversion": "NOT_ATTEMPTED",
        "prediction_authority": False,
        "can_execute": False,
    }


def source_result_agreement(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Don't transform missing independent settlements into losses or wins."""
    comparable = [
        r for r in records if r.get("source_reported_result") is not None
        and r.get("independently_reconciled_result") is not None
        and r.get("exact_selected_offer_match") is True
    ]
    return {
        "source_rows": len(records),
        "verified_comparable_rows": len(comparable),
        "agreement_count": sum(
            r["source_reported_result"] == r["independently_reconciled_result"]
            for r in comparable
        ),
        "unresolved_count": len(records) - len(comparable),
        "can_execute": False,
    }
