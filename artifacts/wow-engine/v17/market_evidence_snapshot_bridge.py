"""Attach captured market-evidence snapshot rows to matching V17 Scout candidates.

This is an evidence-transport boundary only. It never computes probability,
edge, qualification, stake, or execution instructions. Snapshot events attach
only when sport, both team identities, and commencement time agree within a
tight tolerance. Ambiguous matches are left unattached rather than guessed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    from v17.nightly_multiscout import bookmaker_rows, is_prop_market
except ModuleNotFoundError:
    from nightly_multiscout import bookmaker_rows, is_prop_market

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


def _event_rows(event: dict[str, Any], *, generated_at: Any) -> list[dict[str, Any]]:
    marker = event.get("_wow_market_evidence") or event.get("_wow_secondary_source") or {}
    marker = marker if isinstance(marker, dict) else {}
    provider = str(marker.get("provider") or "MARKET_EVIDENCE_SNAPSHOT")
    rows: list[dict[str, Any]] = []
    for raw in bookmaker_rows(event):
        row = dict(raw)
        row.update({
            "source_provider": provider,
            "source_provider_detail": marker.get("provider_detail") or provider,
            "source_class": "SPORTSBOOK_FEED",
            "source_tier": marker.get("source_tier") or "TIER_3_ESTABLISHED_DATA",
            "source_capability": marker.get("capability"),
            "captured_at": row.get("market_last_update") or row.get("bookmaker_last_update") or generated_at,
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


def _attach_rows(candidate: dict[str, Any], rows: list[dict[str, Any]], *, lane: str) -> int:
    if lane == "team_event_candidates":
        additions = [row for row in rows if not is_prop_market(str(row.get("market_key") or ""))]
    else:
        # Prop candidates already carry the specific player/market identity from
        # discovery. Snapshot rows may augment them only when that exact identity
        # is preserved; never infer a player from a team-level market row.
        existing = _existing_rows(candidate)
        if not existing:
            return 0
        reference = existing[0]
        ref_market = str(reference.get("market_key") or "")
        ref_description = _norm(reference.get("description") or reference.get("outcome_name"))
        additions = [
            row for row in rows
            if is_prop_market(str(row.get("market_key") or ""))
            and str(row.get("market_key") or "") == ref_market
            and _norm(row.get("description") or row.get("outcome_name")) == ref_description
        ]

    combined = _existing_rows(candidate)
    seen = {_row_key(row) for row in combined}
    attached = 0
    for row in additions:
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
    return attached


def attach_snapshot_evidence(
    handoff: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    tolerance_minutes: float = MATCH_TOLERANCE_MINUTES,
) -> dict[str, Any]:
    """Return a copied handoff with unambiguous snapshot evidence attached."""
    if handoff.get("can_execute") is not False:
        raise ValueError("SCOUT_HANDOFF_CAN_EXECUTE_MUST_BE_FALSE")
    if snapshot.get("can_execute") is not False:
        raise ValueError("MARKET_EVIDENCE_CAN_EXECUTE_MUST_BE_FALSE")
    if snapshot.get("prediction_authority") is not False:
        raise ValueError("MARKET_EVIDENCE_PREDICTION_AUTHORITY_MUST_BE_FALSE")

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
        event_rows = _event_rows(event, generated_at=generated_at)
        delta = _attach_rows(candidate, event_rows, lane=lane)
        if delta:
            matched_events += 1
            attached_rows += delta
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
        "identity_policy": "EXACT_NORMALIZED_TEAMS_PLUS_SPORT_AND_TIME_TOLERANCE",
        "tolerance_minutes": tolerance_minutes,
        "sportsbook_evidence_only": True,
        "prediction_authority": False,
        "can_execute": False,
    }
    out["can_execute"] = False
    return out


__all__ = ["MATCH_TOLERANCE_MINUTES", "attach_snapshot_evidence"]
