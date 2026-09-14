"""Attach captured market-evidence snapshot rows to matching V17 Scout candidates.

This is an evidence-transport boundary only. It never computes probability,
edge, qualification, stake, or execution instructions. Snapshot events attach
only when sport, both team identities, and commencement time agree within a
tight tolerance. Ambiguous matches are left unattached rather than guessed.

Current evidence is freshness-checked at consumption time. Stale/unknown rows
and historical opener rows are retained as diagnostics but are never inserted
into the active ``market_evidence`` collection that can advance Scout research.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from v17.nightly_multiscout import bookmaker_rows, is_prop_market
    from v17 import market_evidence_hardening as hardening
except ModuleNotFoundError:
    from nightly_multiscout import bookmaker_rows, is_prop_market
    import market_evidence_hardening as hardening

MATCH_TOLERANCE_MINUTES = 180.0


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


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


def _same_event(candidate: dict[str, Any], event: dict[str, Any], *, tolerance_minutes: float) -> bool:
    if str(candidate.get("sport_key") or "") != str(event.get("sport_key") or ""):
        return False
    for field in ("home_team", "away_team"):
        left = _norm(candidate.get(field))
        right = _norm(event.get(field))
        if not left or not right or left != right:
            return False
    candidate_time = _aware(candidate.get("commence_time"))
    event_time = _aware(event.get("commence_time"))
    if candidate_time is None or event_time is None:
        return False
    delta = abs((candidate_time - event_time).total_seconds()) / 60.0
    return delta <= tolerance_minutes


def _event_rows(event: dict[str, Any], *, generated_at: Any, now: datetime) -> list[dict[str, Any]]:
    marker = event.get("_wow_market_evidence") or event.get("_wow_secondary_source") or {}
    marker = marker if isinstance(marker, dict) else {}
    provider = str(marker.get("provider") or "MARKET_EVIDENCE_SNAPSHOT")
    capability = marker.get("provider_detail") or marker.get("capability")
    historical = str(capability or "").lower() == "openers"
    rows: list[dict[str, Any]] = []
    for raw in bookmaker_rows(event):
        row = dict(raw)
        captured_at = row.get("market_last_update") or row.get("bookmaker_last_update")
        freshness = hardening.classify_freshness(
            captured_at,
            now=now,
            max_age_minutes=hardening.DEFAULT_MAX_AGE_MINUTES,
            historical=historical,
        )
        row.update({
            "source_provider": provider,
            "source_provider_detail": marker.get("provider_detail") or provider,
            "source_class": "SPORTSBOOK_FEED",
            "source_tier": marker.get("source_tier") or "TIER_3_ESTABLISHED_DATA",
            "source_capability": capability,
            "captured_at": captured_at or generated_at,
            "freshness_state": freshness["state"],
            "freshness_age_minutes": freshness["age_minutes"],
            "freshness_max_age_minutes": freshness["max_age_minutes"],
            "research_usable": freshness["research_usable"],
            "current_market_evidence": freshness["current_market_evidence"],
            "prediction_authority": False,
            "exact_line_authority": False,
            "research_only": True,
            "can_execute": False,
        })
        rows.append(row)
    return rows


def _row_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(key) or "") for key in (
        "source_provider", "bookmaker", "market_key", "outcome_name", "description", "point", "price",
    ))


def _existing_rows(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = candidate.get("market_evidence")
    if isinstance(evidence, dict):
        return [dict(evidence)] if evidence else []
    if isinstance(evidence, list):
        return [dict(row) for row in evidence if isinstance(row, dict) and row]
    return []


def _append_diagnostic_rows(candidate: dict[str, Any], field: str, rows: list[dict[str, Any]]) -> None:
    existing = [dict(row) for row in candidate.get(field, []) or [] if isinstance(row, dict)]
    seen = {_row_key(row) for row in existing}
    for row in rows:
        key = _row_key(row)
        if key not in seen:
            seen.add(key)
            existing.append(row)
    if existing:
        candidate[field] = existing


def _add_blocker(candidate: dict[str, Any], blocker: str) -> None:
    blockers = [str(value) for value in candidate.get("market_evidence_source_blockers") or []]
    if blocker not in blockers:
        blockers.append(blocker)
    candidate["market_evidence_source_blockers"] = blockers


def _attach_rows(candidate: dict[str, Any], rows: list[dict[str, Any]], *, lane: str) -> dict[str, int]:
    if lane == "team_event_candidates":
        eligible = [row for row in rows if not is_prop_market(str(row.get("market_key") or ""))]
    else:
        # Prop candidates already carry the specific player/market identity from
        # discovery. Snapshot rows may augment them only when that exact identity
        # is preserved; never infer a player from a team-level market row.
        existing = _existing_rows(candidate)
        if not existing:
            return {"attached": 0, "stale": 0, "historical": 0}
        reference = existing[0]
        ref_market = str(reference.get("market_key") or "")
        ref_description = _norm(reference.get("description") or reference.get("outcome_name"))
        eligible = [
            row for row in rows
            if is_prop_market(str(row.get("market_key") or ""))
            and str(row.get("market_key") or "") == ref_market
            and _norm(row.get("description") or row.get("outcome_name")) == ref_description
        ]

    active = [row for row in eligible if row.get("freshness_state") in {hardening.FRESH, hardening.AGING}]
    stale = [row for row in eligible if row.get("freshness_state") in {hardening.STALE, hardening.UNAVAILABLE}]
    historical = [row for row in eligible if row.get("freshness_state") == hardening.HISTORICAL]

    _append_diagnostic_rows(candidate, "market_evidence_stale", stale)
    _append_diagnostic_rows(candidate, "market_evidence_historical", historical)
    if stale:
        _add_blocker(candidate, "MARKET_EVIDENCE_STALE_AT_CONSUMPTION")
    if historical and not active and not _existing_rows(candidate):
        _add_blocker(candidate, "MARKET_EVIDENCE_HISTORICAL_ONLY")

    combined = _existing_rows(candidate)
    seen = {_row_key(row) for row in combined}
    attached = 0
    for row in active:
        key = _row_key(row)
        if key in seen:
            continue
        seen.add(key)
        combined.append(row)
        attached += 1
    if attached:
        candidate["market_evidence"] = combined
        blockers = list(candidate.get("market_evidence_source_blockers") or [])
        candidate["market_evidence_status"] = "PARTIAL_SOURCE_BLOCKED" if blockers else "AVAILABLE"
        candidate["snapshot_market_evidence_attached"] = True
    elif stale or historical:
        candidate["market_evidence_status"] = "STALE_OR_HISTORICAL_ONLY"

    return {"attached": attached, "stale": len(stale), "historical": len(historical)}


def attach_snapshot_evidence(
    handoff: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    tolerance_minutes: float = MATCH_TOLERANCE_MINUTES,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a copied handoff with unambiguous, current snapshot evidence attached."""
    if handoff.get("can_execute") is not False:
        raise ValueError("SCOUT_HANDOFF_CAN_EXECUTE_MUST_BE_FALSE")
    if snapshot.get("can_execute") is not False:
        raise ValueError("MARKET_EVIDENCE_CAN_EXECUTE_MUST_BE_FALSE")
    if snapshot.get("prediction_authority") is not False:
        raise ValueError("MARKET_EVIDENCE_PREDICTION_AUTHORITY_MUST_BE_FALSE")

    now = now or datetime.now(timezone.utc)
    out = dict(handoff)
    model = dict(out.get("model_handoff") or {})
    lanes: dict[str, list[dict[str, Any]]] = {}
    candidate_refs: list[tuple[str, int, dict[str, Any]]] = []
    for lane in ("team_event_candidates", "prop_candidates"):
        rows = [dict(row) for row in model.get(lane, []) or [] if isinstance(row, dict)]
        lanes[lane] = rows
        candidate_refs.extend((lane, idx, row) for idx, row in enumerate(rows))

    events = [event for event in snapshot.get("events", []) or [] if isinstance(event, dict)]
    generated_at = snapshot.get("generated_at")
    matched_events = 0
    ambiguous_events = 0
    unmatched_events = 0
    attached_rows = 0
    stale_rows = 0
    historical_rows = 0
    candidates_touched: set[tuple[str, int]] = set()

    for event in events:
        matches = [
            (lane, idx, candidate)
            for lane, idx, candidate in candidate_refs
            if _same_event(candidate, event, tolerance_minutes=tolerance_minutes)
        ]
        if len(matches) != 1:
            if len(matches) > 1:
                ambiguous_events += 1
            else:
                unmatched_events += 1
            continue
        lane, idx, candidate = matches[0]
        event_rows = _event_rows(event, generated_at=generated_at, now=now)
        result = _attach_rows(candidate, event_rows, lane=lane)
        attached_rows += result["attached"]
        stale_rows += result["stale"]
        historical_rows += result["historical"]
        if result["attached"]:
            matched_events += 1
            candidates_touched.add((lane, idx))

    for lane, rows in lanes.items():
        model[lane] = rows
    out["model_handoff"] = model
    out["market_evidence_snapshot_bridge"] = {
        "snapshot_status": snapshot.get("status"),
        "snapshot_rows": (snapshot.get("reconciliation") or {}).get("captured_rows", len(events)),
        "snapshot_events": len(events),
        "matched_events": matched_events,
        "ambiguous_events": ambiguous_events,
        "unmatched_events": unmatched_events,
        "candidates_touched": len(candidates_touched),
        "evidence_rows_attached": attached_rows,
        "stale_or_unknown_rows_quarantined": stale_rows,
        "historical_opener_rows_quarantined": historical_rows,
        "source_disagreement_alert_count": snapshot.get("source_disagreement_alert_count", 0),
        "identity_policy": "EXACT_NORMALIZED_TEAMS_PLUS_SPORT_AND_TIME_TOLERANCE",
        "tolerance_minutes": tolerance_minutes,
        "freshness_policy": "SPORTSBOOK_FEED_MAX_AGE_15_MINUTES_AT_CONSUMPTION",
        "sportsbook_evidence_only": True,
        "prediction_authority": False,
        "can_execute": False,
    }
    out["source_disagreement_alerts"] = list(snapshot.get("source_disagreement_alerts") or [])
    out["can_execute"] = False
    return out


__all__ = ["MATCH_TOLERANCE_MINUTES", "attach_snapshot_evidence"]