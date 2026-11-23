"""Fail-closed all-position, all-market context-change impact *handoff*.

The news/research layer can identify a pregame change and request a new
specialist receipt. It is NOT a fitted predictor, probability recalibrator,
terminal gate, or proof that a downstream scorer actually consumed the change.
Unknown dependencies are conservatively scoped to every role and market for
the exact event, rather than silently dropping non-starters or specialists.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Mapping

try:
    from v17.matchup_discovery import profile_for
except ModuleNotFoundError:
    from matchup_discovery import profile_for

SCHEMA_VERSION = "wow.v17.context_change_impact.v1"
ALL_MARKET_FAMILIES = (
    "MONEYLINE_WINNER",
    "SPREAD_HANDICAP",
    "GAME_TOTAL_OVER",
    "GAME_TOTAL_UNDER",
    "TEAM_TOTAL_OVER",
    "TEAM_TOTAL_UNDER",
    "PARTICIPANT_PROP_OVER",
    "PARTICIPANT_PROP_UNDER",
    "PARTICIPANT_PROP_OTHER",
    "UPSET_INTELLIGENCE",
    "MULTI_LEG_CORRELATION_RESEARCH",
)
ALL_POSITION_SCOPE = "ALL_STARTERS_RESERVES_SPECIALISTS_AND_OPPONENT_ROLES"
_VALID_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError):
        return None
    if timestamp.utcoffset() is None:
        return None
    return timestamp.astimezone(timezone.utc)


def _digest(value: Any) -> bool:
    return isinstance(value, str) and _VALID_DIGEST.fullmatch(value) is not None


def candidate_event_start(candidate: Mapping[str, Any]) -> Any:
    """Preserve all supported Scout start formats, including nested requests."""
    nested = candidate.get("event_request")
    nested = nested if isinstance(nested, Mapping) else {}
    return (candidate.get("event_start_utc")
            or candidate.get("commence_time")
            or candidate.get("event_start_time_utc")
            or nested.get("event_start_time_utc"))


def assess_context_change_impact(
    candidate: Mapping[str, Any],
    news_research_handoff: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a bounded, read-only *research* invalidation/rescore request.

    Coverage deliberately uses the complete event as a conservative fallback:
    roster or individual position filters must not accidentally skip affected
    team markets, opponent positions, kickers, goalies, bench roles or props.
    The scorer/terminal must separately enforce stale-receipt rejection.
    """
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("CONTEXT_IMPACT_NOW_MUST_BE_AWARE")
    now = now.astimezone(timezone.utc)
    sport = str(candidate.get("sport_key") or candidate.get("sport") or "")
    event_id = str(candidate.get("official_event_id") or "")
    event_start = _utc(candidate_event_start(candidate))
    handoff = news_research_handoff if isinstance(news_research_handoff, Mapping) else {}
    status = "NO_MATERIAL_EVENT_RECEIPT"
    material = []
    trigger_receipt = None
    invalidation_requested = False

    if not event_id or event_start is None:
        status = "CONTEXT_EVENT_IDENTITY_OR_START_UNRESOLVED"
        invalidation_requested = True
    elif profile_for(sport) is None:
        status = "CONTEXT_SPORT_NOT_MAPPED"
        invalidation_requested = True
    elif event_start <= now:
        # Never turn later information into a new *pregame* receipt.
        status = "PREGAME_WINDOW_CLOSED"
    elif handoff:
        trigger_receipt = handoff.get("receipt_sha256")
        source_start = _utc(handoff.get("event_start_utc"))
        if (str(handoff.get("official_event_id") or "") != event_id
                or str(handoff.get("sport_key") or "") != sport
                or source_start != event_start):
            status = "CONTEXT_EVIDENCE_EVENT_IDENTITY_MISMATCH"
            invalidation_requested = True
        elif not _digest(trigger_receipt):
            status = "CONTEXT_RECEIPT_DIGEST_INVALID"
            invalidation_requested = True
        else:
            research_status = handoff.get("research_status")
            rows = handoff.get("news_context")
            material = ([row for row in rows
                         if isinstance(row, Mapping) and row.get("material_change") is True]
                        if isinstance(rows, list) else [])
            if (research_status == "NEWS_RESEARCH_RESCORE_REQUESTED"
                    and handoff.get("research_rescore_requested") is True):
                if not material or any(
                    not row.get("source_url") or not row.get("independent_review_ref")
                    or (reviewed := _utc(row.get("independently_reviewed_at_utc"))) is None
                    or reviewed >= event_start or reviewed > now
                    for row in material
                ):
                    status = "CONTEXT_MATERIAL_EVIDENCE_INVALID"
                else:
                    status = "CONTEXT_STALE_RECALC_REQUIRED"
                invalidation_requested = True
            elif research_status in (
                "NEWS_SOURCE_CONFLICT", "NEWS_HIGH_IMPACT_CONFIRMATION_REQUIRED",
                "NEWS_CANONICAL_EVENT_UNVERIFIED", "NEWS_DISCOVERY_TIME_INVALID",
            ) or material:
                status = "CONTEXT_UNRESOLVED_REVIEW_REQUIRED"
                invalidation_requested = True
    return {
        "schema_version": SCHEMA_VERSION,
        "sport_key": sport,
        "official_event_id": event_id or None,
        "event_start_utc": event_start.isoformat() if event_start else None,
        "impact_status": status,
        "position_scope": ALL_POSITION_SCOPE,
        "no_position_exclusions": True,
        "market_scope_policy": "EVENT_WIDE_CONSERVATIVE_FAIL_CLOSED",
        "affected_market_families": (list(ALL_MARKET_FAMILIES)
                                      if invalidation_requested else []),
        "source_subject_ids": sorted({
            str(row.get("subject_id")) for row in material if row.get("subject_id")
        }),
        "material_change_count": len(material),
        "trigger_research_receipt_sha256": (
            trigger_receipt if _digest(trigger_receipt) else None
        ),
        "research_invalidation_requested": invalidation_requested,
        "research_rescore_requested": status == "CONTEXT_STALE_RECALC_REQUIRED",
        "fitted_specialist_rescore_verified": False,
        "terminal_invalidation_enforced_by_this_module": False,
        "model_probability_created": False,
        "prediction_authority": False,
        "can_execute": False,
    }


def hold_scored_after_material_change(
    scored: Mapping[str, Any],
    *,
    latest_material_update_timestamp: Any,
) -> dict[str, Any]:
    """Downgrade a score if its fitted timestamp predates known material context.

    This narrow publication guard does NOT authenticate the source timestamp,
    prove the specialist consumed new lineup features, or issue a new terminal
    disposition. Missing update data remains explicitly unverified. It cannot
    make any held or provisional score publishable.
    """
    out = dict(scored)
    source_raw = latest_material_update_timestamp
    if source_raw in (None, ""):
        out["material_context_chronology_status"] = "LATEST_MATERIAL_UPDATE_NOT_AVAILABLE"
        out["can_execute"] = False
        return out
    source_at = _utc(source_raw)
    model_at = _utc(out.get("immutable_model_timestamp") or out.get("model_timestamp"))
    if source_at is not None and model_at is not None and model_at >= source_at:
        out["material_context_chronology_status"] = "CHRONOLOGY_OK_CONSUMPTION_NOT_PROVEN"
        out["can_execute"] = False
        return out
    out["material_context_chronology_status"] = "CONTEXT_STALE_RECALC_REQUIRED"
    if "prepublication_claim" not in out:
        out["prepublication_claim"] = {
            "probability_publishable": out.get("probability_publishable"),
            "rank_eligible": out.get("rank_eligible"),
        }
    out["probability_publishable"] = False
    out["rank_eligible"] = False
    existing = out.get("publication_blockers")
    prior = existing if isinstance(existing, list) else []
    out["publication_blockers"] = list(dict.fromkeys([
        *prior, "CONTEXT_STALE_RECALC_REQUIRED",
    ]))
    out["can_execute"] = False
    return out


__all__ = ["assess_context_change_impact", "hold_scored_after_material_change", "ALL_MARKET_FAMILIES", "ALL_POSITION_SCOPE"]
