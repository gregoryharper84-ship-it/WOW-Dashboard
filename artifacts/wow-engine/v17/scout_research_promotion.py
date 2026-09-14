"""Evidence-only Scout research promotion for WOW V17.

Evaluates already-discovered Scout candidates using research evidence quality.
It never creates model probabilities, never qualifies a wager, and never
executes. Final probability remains the responsibility of the controlling
specialist and can_execute is always false.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from v17.scout_source_policy import evidence_quality
except ModuleNotFoundError:
    from scout_source_policy import evidence_quality

RESEARCH_STATUSES = {
    "WATCH",
    "RESEARCH_INTEREST_LOW",
    "RESEARCH_INTEREST_MEDIUM",
    "RESEARCH_INTEREST_HIGH",
    "QUARANTINED",
    "NO_INTEREST",
}


def _aware(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _age_minutes(value: Any, *, now: datetime) -> float | None:
    parsed = _aware(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 60.0)


def _market_rows(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = candidate.get("market_evidence")
    if isinstance(evidence, dict):
        return [evidence]
    if isinstance(evidence, list):
        return [row for row in evidence if isinstance(row, dict)]
    return []


def evaluate_candidate(candidate: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Return a non-predictive research disposition for one Scout candidate."""
    now = now or datetime.now(timezone.utc)
    rows = _market_rows(candidate)
    blockers = list(candidate.get("market_evidence_source_blockers") or [])
    contradictions = [str(x) for x in (candidate.get("contradictory_evidence") or [])]
    red_flags = [str(x) for x in (candidate.get("red_team_flags") or [])]

    usable = 0
    fresh = 0
    books: set[str] = set()
    freshness_scores: list[float] = []
    for row in rows:
        source_class = str(row.get("source_class") or "SPORTSBOOK_FEED")
        captured_at = row.get("market_last_update") or row.get("bookmaker_last_update") or row.get("captured_at")
        age = _age_minutes(captured_at, now=now)
        quality = evidence_quality(source_class, age_minutes=age, confirmed=True, evidence_type="MARKET_EVIDENCE")
        if quality["research_usable"]:
            usable += 1
        if not quality["stale"]:
            fresh += 1
        if age is not None:
            max_age = max(float(quality["max_age_minutes"]), 1.0)
            freshness_scores.append(max(0.0, min(100.0, 100.0 * (1.0 - age / max_age))))
        book = row.get("bookmaker") or row.get("source_provider") or row.get("bookmaker_title")
        if book:
            books.add(str(book))

    identity_fields = (
        candidate.get("official_event_id"),
        candidate.get("sport_key"),
        candidate.get("commence_time"),
        candidate.get("home_team"),
        candidate.get("away_team"),
    )
    identity_complete = all(v not in (None, "") for v in identity_fields)
    evidence_count = len(rows)
    completeness_parts = [identity_complete, evidence_count > 0, usable > 0]
    data_completeness = round(100.0 * sum(bool(v) for v in completeness_parts) / len(completeness_parts), 2)
    source_freshness_score = round(sum(freshness_scores) / len(freshness_scores), 2) if freshness_scores else 0.0

    if contradictions or red_flags:
        status = "QUARANTINED"
        reason = "MATERIAL_CONFLICT_OR_RED_TEAM_FLAG"
    elif not identity_complete:
        status = "WATCH"
        reason = "IDENTITY_INCOMPLETE"
    elif evidence_count == 0:
        status = "WATCH"
        reason = "RESEARCH_EVIDENCE_MISSING"
    elif usable == 0 or fresh == 0:
        status = "WATCH"
        reason = "NO_FRESH_RESEARCH_USABLE_EVIDENCE"
    elif blockers:
        status = "RESEARCH_INTEREST_LOW"
        reason = "PARTIAL_SOURCE_BLOCKED"
    elif len(books) >= 3 and usable >= 3:
        status = "RESEARCH_INTEREST_HIGH"
        reason = "FRESH_MULTI_SOURCE_EVIDENCE"
    elif len(books) >= 2 and usable >= 2:
        status = "RESEARCH_INTEREST_MEDIUM"
        reason = "FRESH_CORROBORATED_EVIDENCE"
    else:
        status = "RESEARCH_INTEREST_LOW"
        reason = "FRESH_SINGLE_SOURCE_RESEARCH_EVIDENCE"

    return {
        "research_status": status,
        "research_reason": reason,
        "data_completeness": data_completeness,
        "source_freshness_score": source_freshness_score,
        "research_evidence_count": evidence_count,
        "research_usable_evidence_count": usable,
        "research_fresh_evidence_count": fresh,
        "research_bookmaker_count": len(books),
        "red_team": {
            "status": "QUARANTINED" if status == "QUARANTINED" else "PASSED",
            "flags": red_flags,
        },
        "probability": None,
        "prediction_authority": False,
        "can_execute": False,
    }


def promote_handoff(payload: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    if payload.get("can_execute") is not False:
        raise ValueError("SCOUT_HANDOFF_CAN_EXECUTE_MUST_BE_FALSE")
    out = dict(payload)
    handoff = dict(out.get("model_handoff") or {})
    counts = {status: 0 for status in RESEARCH_STATUSES}
    for lane in ("team_event_candidates", "prop_candidates"):
        updated: list[dict[str, Any]] = []
        for row in handoff.get(lane, []) or []:
            if not isinstance(row, dict):
                continue
            candidate = dict(row)
            evaluation = evaluate_candidate(candidate, now=now)
            candidate.update(evaluation)
            candidate["research_ceiling"] = "RESEARCH_INTEREST"
            candidate["probability_authority"] = False
            candidate["can_execute"] = False
            counts[evaluation["research_status"]] += 1
            updated.append(candidate)
        handoff[lane] = updated
    out["model_handoff"] = handoff
    out["research_promotion"] = {
        "status_counts": counts,
        "sportsbook_evidence_only": True,
        "final_probability_requires_controlling_specialist": True,
        "research_ceiling": "RESEARCH_INTEREST",
        "can_execute": False,
    }
    out["can_execute"] = False
    return out


__all__ = ["evaluate_candidate", "promote_handoff"]
