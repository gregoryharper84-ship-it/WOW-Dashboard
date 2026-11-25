"""Read-only, provisional team/event winner-card presentation integrity.

This is NOT a V17 scorer, fitted specialist, certifier, immutable receipt writer,
or terminal publisher. It validates externally researched card *presentation*
against a caller-supplied official slate. Official source authentication and
certified model admission remain solely with the governed LLP/V17 pipeline.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
ALLOWED_PROVENANCE = frozenset({"QUALITATIVE_RESEARCH", "MARKET_NO_VIG", "EXTERNAL_PROJECTION"})
# Source-label/settlement policy applies to research presentation only, never
# sport probability certification. Do not infer a default settlement for
# unknown sports, including tennis retirement or soccer advancement.
SPORT_CONTRACTS: dict[str, tuple[str, frozenset[str], str]] = {
    "NHL": ("FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT", frozenset({"HOME", "AWAY"}), "FULL_GAME_MONEYLINE"),
    "NBA": ("FULL_GAME_INCLUDING_OVERTIME", frozenset({"HOME", "AWAY"}), "FULL_GAME_MONEYLINE"),
    "NFL": ("FULL_GAME_INCLUDING_OVERTIME", frozenset({"HOME", "AWAY"}), "FULL_GAME_MONEYLINE"),
    "NCAAF": ("FULL_GAME_INCLUDING_OVERTIME", frozenset({"HOME", "AWAY"}), "FULL_GAME_MONEYLINE"),
    "NCAAB": ("FULL_GAME_INCLUDING_OVERTIME", frozenset({"HOME", "AWAY"}), "FULL_GAME_MONEYLINE"),
    "SOCCER": ("REGULATION", frozenset({"HOME", "DRAW", "AWAY"}), "REGULATION_1X2"),
}
# Conservative evidence-display freshness. Not fitted model input thresholds.
MAX_NUMERIC_EVIDENCE_AGE = {
    "MARKET_NO_VIG": timedelta(minutes=60),
    "EXTERNAL_PROJECTION": timedelta(hours=6),
}
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
    # Bound the original number before float conversion. Huge Python ints
    # otherwise raise OverflowError before isfinite can reject them.
    if not 0.0 <= value <= 1.0:
        raise ValueError("WINNER_CARD_PROBABILITY_INVALID")
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError("WINNER_CARD_PROBABILITY_INVALID")
    return numeric


def _evidence(row: Mapping[str, Any], *, freeze: datetime, kickoff: datetime,
              event_id: str, sport: str, settlement_basis: str,
              selection: str) -> dict[str, Any]:
    """Validate research provenance without authenticating a source or model."""
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
    source_context_status = "NOT_APPLICABLE_QUALITATIVE"
    source_snapshot_id = None
    if source == "QUALITATIVE_RESEARCH":
        if estimate is not None:
            raise ValueError("WINNER_CARD_UNSOURCED_NUMERIC_PROBABILITY")
    else:
        if estimate is None:
            raise ValueError("WINNER_CARD_NUMERIC_PROBABILITY_REQUIRED")
        estimate = _probability(estimate)
        if freeze - observed > MAX_NUMERIC_EVIDENCE_AGE[source]:
            raise ValueError("WINNER_CARD_NUMERIC_SOURCE_STALE")
        context = row.get("numeric_source_context")
        if not isinstance(context, Mapping):
            raise ValueError("WINNER_CARD_NUMERIC_SOURCE_CONTEXT_REQUIRED")
        # All source identities are caller-declared; mismatches fail closed.
        expected_basis, expected_outcomes, expected_market = SPORT_CONTRACTS[sport]
        if (context.get("event_id") != event_id
                or context.get("source_kind") != source
                or context.get("selection") != selection
                or context.get("settlement_basis") != settlement_basis
                or settlement_basis != expected_basis
                or context.get("market_type") != expected_market):
            raise ValueError("WINNER_CARD_NUMERIC_SOURCE_MARKET_MISMATCH")
        outcomes = context.get("market_outcomes")
        if not isinstance(outcomes, (list, tuple, set, frozenset)):
            raise ValueError("WINNER_CARD_NUMERIC_SOURCE_MARKET_MISMATCH")
        try:
            observed_outcomes = frozenset(outcomes)
        except TypeError as exc:
            raise ValueError("WINNER_CARD_NUMERIC_SOURCE_MARKET_MISMATCH") from exc
        if observed_outcomes != expected_outcomes or len(outcomes) != len(expected_outcomes):
            raise ValueError("WINNER_CARD_NUMERIC_SOURCE_MARKET_MISMATCH")
        if _instant(context.get("observed_at"), "SOURCE_OBSERVED_AT") != observed:
            raise ValueError("WINNER_CARD_NUMERIC_SOURCE_TIMESTAMP_MISMATCH")
        if source == "MARKET_NO_VIG" and context.get("de_vig_method") != "POWER":
            raise ValueError("WINNER_CARD_NUMERIC_SOURCE_METHOD_INVALID")
        source_snapshot_id = _required(context.get("source_snapshot_id"), "SOURCE_SNAPSHOT_ID")
        source_context_status = "CALLER_DECLARED_MATCH_NOT_INDEPENDENTLY_AUTHENTICATED"
    return {
        "probability_source": source,
        "display_probability": estimate,
        "display_probability_is_calibrated": False,
        "numeric_source_context_status": source_context_status,
        "source_snapshot_id": source_snapshot_id,
        "evidence_ref": reference,
        "observed_at": observed.isoformat(),
    }

def _check_shared_numeric_snapshots(
    primary: Mapping[str, Any],
    watches: Sequence[Mapping[str, Any]],
    full_outcomes: frozenset[str],
) -> None:
    """Refuse contradictory selections claimed from one numeric snapshot.

    A caller-declared snapshot is NOT authenticated here. This check prevents
    self-contradictory display probabilities, not proof that the source quote
    or the no-vig computation is genuine.
    """
    snapshots: dict[tuple[str, str], dict[str, Any]] = {}
    for item in (primary, *watches):
        if item["display_probability"] is None:
            continue
        snapshot_key = (item["probability_source"], item["source_snapshot_id"])
        prior = snapshots.setdefault(
            snapshot_key,
            {"observed_at": item["observed_at"], "selections": {}},
        )
        if item["observed_at"] != prior["observed_at"]:
            raise ValueError("WINNER_CARD_NUMERIC_SNAPSHOT_TIME_CONFLICT")
        prior["selections"][item["selection"]] = item["display_probability"]
    for snapshot in snapshots.values():
        numbers = snapshot["selections"]
        total = sum(numbers.values())
        if total > 1.0 + 1e-8:
            raise ValueError("WINNER_CARD_NUMERIC_SNAPSHOT_DISTRIBUTION_CONFLICT")
        if set(numbers) == full_outcomes and abs(total - 1.0) > 1e-6:
            raise ValueError("WINNER_CARD_NUMERIC_SNAPSHOT_DISTRIBUTION_CONFLICT")


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
        home = _required(event.get("home"), "HOME")
        away = _required(event.get("away"), "AWAY")
        if home.casefold() == away.casefold():
            raise ValueError("WINNER_CARD_EVENT_TEAMS_INVALID")
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
        if sport not in SPORT_CONTRACTS:
            raise ValueError("WINNER_CARD_SPORT_SETTLEMENT_UNSUPPORTED")
        canonical_basis, full_outcomes, _ = SPORT_CONTRACTS[sport]
        if basis != canonical_basis:
            if sport == "NHL":
                raise ValueError("WINNER_CARD_NHL_SETTLEMENT_BASIS_INVALID")
            raise ValueError("WINNER_CARD_SETTLEMENT_BASIS_INVALID")
        if sport == "SOCCER":
            # Participant-only contests award no point for an actual draw.
            # They may omit DRAW as a *choice* but not as a market outcome.
            allowed = (
                eligible == full_outcomes and event.get("draw_pick_rule") == "DRAW_PICK_ALLOWED"
            ) or (
                eligible == {"HOME", "AWAY"}
                and event.get("draw_pick_rule") == "DRAW_COUNTS_AS_INCORRECT"
            )
            if not allowed:
                raise ValueError("WINNER_CARD_SOCCER_OUTCOME_RULES_INVALID")
        elif eligible != full_outcomes:
            if sport == "NHL":
                raise ValueError("WINNER_CARD_NHL_SETTLEMENT_OUTCOMES_INVALID")
            raise ValueError("WINNER_CARD_SETTLEMENT_OUTCOMES_INVALID")
        if freeze >= kickoff:
            raise ValueError("WINNER_CARD_NOT_PREGAME")
        if event_id in event_by_id:
            raise ValueError("WINNER_CARD_DUPLICATE_OFFICIAL_EVENT")
        event_order.append(event_id)
        # Use the same canonical identity checked above, not raw caller strings.
        # Never mutate the supplied official-source object.
        normalized = dict(event)
        normalized.update(event_id=event_id, sport=sport, home=home, away=away,
                          settlement_basis=basis)
        event_by_id[event_id] = (normalized, kickoff, eligible)

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
        primaries[event_id] = {"selection": selection, **_evidence(pick, freeze=freeze, kickoff=kickoff,
            event_id=event_id, sport=str(event["sport"]).upper(),
            settlement_basis=str(event["settlement_basis"]), selection=selection)}

    if set(primaries) != set(event_by_id):
        raise ValueError("WINNER_CARD_INCOMPLETE_OFFICIAL_SLATE")

    watches: dict[str, list[dict[str, Any]]] = {id_: [] for id_ in event_by_id}
    for watch in upset_watch:
        event_id = _required(watch.get("event_id"), "EVENT_ID")
        if event_id not in event_by_id:
            raise ValueError("WINNER_CARD_UNKNOWN_UPSET_EVENT")
        event, kickoff, eligible = event_by_id[event_id]
        alternative = _required(watch.get("selection"), "SELECTION")
        if alternative not in eligible or alternative == primaries[event_id]["selection"]:
            raise ValueError("WINNER_CARD_UPSET_ALTERNATIVE_INVALID")
        if any(previous["selection"] == alternative for previous in watches[event_id]):
            raise ValueError("WINNER_CARD_DUPLICATE_UPSET_ALTERNATIVE")
        watches[event_id].append({
            "selection": alternative,
            "reason": _required(watch.get("reason"), "UPSET_REASON"),
            **_evidence(watch, freeze=freeze, kickoff=kickoff,
                event_id=event_id, sport=str(event["sport"]).upper(),
                settlement_basis=str(event["settlement_basis"]), selection=alternative),
            "changes_primary_selection": False,
        })

    rows = []
    for event_id in event_order:
        event, _, _ = event_by_id[event_id]
        _check_shared_numeric_snapshots(
            primaries[event_id], watches[event_id], SPORT_CONTRACTS[event["sport"]][1],
        )
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
