"""Read-only, provisional team/event winner-card presentation integrity.

This is NOT a V17 scorer, fitted specialist, certifier, immutable receipt writer,
or terminal publisher. It validates externally researched card *presentation*
against a caller-supplied official slate. Official source authentication and
certified model admission remain solely with the governed LLP/V17 pipeline.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
ALLOWED_PROVENANCE = frozenset({"QUALITATIVE_RESEARCH", "MARKET_NO_VIG", "EXTERNAL_PROJECTION"})
FORBIDDEN_MODEL_FIELDS = frozenset({
    "model_probability", "calibrated_probability", "model_receipt_id",
    "model_lower_bound", "certification_status", "probability_publishable",
    "rank_eligible", "qualified", "full_model",
})


def _required(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"WINNER_CARD_{field}_REQUIRED")
    return value.strip()


def _instant(value: Any, field: str) -> datetime:
    raw = _required(value, field)
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"WINNER_CARD_{field}_INVALID") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"WINNER_CARD_{field}_TIMEZONE_REQUIRED")
    return result.astimezone(timezone.utc)


def _probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("WINNER_CARD_PROBABILITY_INVALID")
    numeric = float(value)
    if not isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        raise ValueError("WINNER_CARD_PROBABILITY_INVALID")
    return numeric


def _evidence(row: Mapping[str, Any], *, freeze: datetime, kickoff: datetime) -> dict[str, Any]:
    if any(field in row for field in FORBIDDEN_MODEL_FIELDS):
        raise ValueError("WINNER_CARD_UNVERIFIED_MODEL_CLAIM")
    source = _required(row.get("probability_source"), "PROBABILITY_SOURCE")
    if source not in ALLOWED_PROVENANCE:
        raise ValueError("WINNER_CARD_UNVERIFIED_MODEL_CLAIM")
    reference = _required(row.get("evidence_ref"), "EVIDENCE_REF")
    observed = _instant(row.get("observed_at"), "OBSERVED_AT")
    if observed > freeze or observed >= kickoff:
        raise ValueError("WINNER_CARD_POST_FREEZE_OR_KICKOFF_EVIDENCE")
    estimate = row.get("display_probability")
    if source == "QUALITATIVE_RESEARCH":
        if estimate is not None:
            raise ValueError("WINNER_CARD_UNSOURCED_NUMERIC_PROBABILITY")
    elif estimate is None:
        raise ValueError("WINNER_CARD_NUMERIC_PROBABILITY_REQUIRED")
    else:
        estimate = _probability(estimate)
    return {
        "probability_source": source,
        "display_probability": estimate,
        "display_probability_is_calibrated": False,
        "evidence_ref": reference,
        "observed_at": observed.isoformat(),
    }


def assemble_provisional_winner_card(
    official_events: Sequence[Mapping[str, Any]],
    primary_picks: Sequence[Mapping[str, Any]],
    *,
    frozen_at: str,
    upset_watch: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Enforce one research pick per event; upset watches cannot replace it.

    Each official event must contain event_id, sport, home, away, starts_at,
    settlement_basis, and eligible_outcomes (HOME/AWAY, plus DRAW if eligible).
    Only externally researched PROVISIONAL picks are accepted here. Revisions
    require a separate new card with explicit provenance, never an upset overlay.
    """
    freeze = _instant(frozen_at, "FROZEN_AT")
    if (any(not isinstance(event, Mapping) for event in official_events)
            or any(not isinstance(pick, Mapping) for pick in primary_picks)
            or any(not isinstance(watch, Mapping) for watch in upset_watch)):
        raise ValueError("WINNER_CARD_ROW_NOT_OBJECT")
    if not official_events:
        raise ValueError("WINNER_CARD_OFFICIAL_SLATE_EMPTY")
    event_by_id: dict[str, tuple[Mapping[str, Any], datetime, frozenset[str]]] = {}
    event_order: list[str] = []
    for event in official_events:
        event_id = _required(event.get("event_id"), "EVENT_ID")
        sport = _required(event.get("sport"), "SPORT").upper()
        _required(event.get("home"), "HOME")
        _required(event.get("away"), "AWAY")
        basis = _required(event.get("settlement_basis"), "SETTLEMENT_BASIS")
        kickoff = _instant(event.get("starts_at"), "STARTS_AT")
        values = event.get("eligible_outcomes")
        if not isinstance(values, (list, tuple, set, frozenset)):
            raise ValueError("WINNER_CARD_ELIGIBLE_OUTCOMES_INVALID")
        try:
            eligible = frozenset(values)
        except TypeError as exc:
            raise ValueError("WINNER_CARD_ELIGIBLE_OUTCOMES_INVALID") from exc
        if len(eligible) != len(values) or not eligible or not eligible.issubset({"HOME", "AWAY", "DRAW"}):
            raise ValueError("WINNER_CARD_ELIGIBLE_OUTCOMES_INVALID")
        if sport == "NHL" and basis == "FULL_GAME_INCLUDING_OT_SO" and eligible != {"HOME", "AWAY"}:
            raise ValueError("WINNER_CARD_NHL_SETTLEMENT_OUTCOMES_INVALID")
        if freeze >= kickoff:
            raise ValueError("WINNER_CARD_NOT_PREGAME")
        if event_id in event_by_id:
            raise ValueError("WINNER_CARD_DUPLICATE_OFFICIAL_EVENT")
        event_order.append(event_id)
        event_by_id[event_id] = (event, kickoff, eligible)

    primaries: dict[str, dict[str, Any]] = {}
    for pick in primary_picks:
        event_id = _required(pick.get("event_id"), "EVENT_ID")
        if event_id not in event_by_id:
            raise ValueError("WINNER_CARD_UNKNOWN_PICK_EVENT")
        if event_id in primaries:
            raise ValueError("WINNER_CARD_DUPLICATE_PRIMARY")
        event, kickoff, eligible = event_by_id[event_id]
        selection = _required(pick.get("selection"), "SELECTION")
        if selection not in eligible:
            raise ValueError("WINNER_CARD_SELECTION_NOT_ELIGIBLE")
        primaries[event_id] = {"selection": selection, **_evidence(pick, freeze=freeze, kickoff=kickoff)}

    if set(primaries) != set(event_by_id):
        raise ValueError("WINNER_CARD_INCOMPLETE_OFFICIAL_SLATE")

    watches: dict[str, list[dict[str, Any]]] = {id_: [] for id_ in event_by_id}
    for watch in upset_watch:
        event_id = _required(watch.get("event_id"), "EVENT_ID")
        if event_id not in event_by_id:
            raise ValueError("WINNER_CARD_UNKNOWN_UPSET_EVENT")
        _, kickoff, eligible = event_by_id[event_id]
        alternative = _required(watch.get("selection"), "SELECTION")
        if alternative not in eligible or alternative == primaries[event_id]["selection"]:
            raise ValueError("WINNER_CARD_UPSET_ALTERNATIVE_INVALID")
        if any(previous["selection"] == alternative for previous in watches[event_id]):
            raise ValueError("WINNER_CARD_DUPLICATE_UPSET_ALTERNATIVE")
        watches[event_id].append({
            "selection": alternative,
            "reason": _required(watch.get("reason"), "UPSET_REASON"),
            **_evidence(watch, freeze=freeze, kickoff=kickoff),
            "changes_primary_selection": False,
        })

    rows = []
    for event_id in event_order:
        event, _, _ = event_by_id[event_id]
        rows.append({
            "event_id": event_id,
            "sport": event["sport"],
            "home": event["home"],
            "away": event["away"],
            "starts_at": event["starts_at"],
            "settlement_basis": event["settlement_basis"],
            "primary": primaries[event_id],
            "upset_watch": watches[event_id],
            "status": "PROVISIONAL",
            "certified": False,
            "rank_eligible": False,
        })
    return {
        "status": "PROVISIONAL_RESEARCH_ONLY",
        "frozen_at": freeze.isoformat(),
        "official_slate_source_authenticated": False,
        "immutable_receipt_status": "NOT_PERSISTED",
        "probability_publishable": False,
        "can_execute": CAN_EXECUTE,
        "rows": rows,
    }


__all__ = ["CAN_EXECUTE", "PROBABILITY_PUBLISHABLE", "assemble_provisional_winner_card"]
