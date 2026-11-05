"""Resolve display-only governed specialist probabilities for Scout boards.

Scout remains research-only and never owns probability. This module reads only
publishable outputs already produced by the controlling fitted specialist. It
never derives probability from sportsbook price, never writes Scout probability,
and never changes execution semantics.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

CAN_EXECUTE = False
PROBABILITY_SOURCE = "CONTROLLING_SPECIALIST"
TERMINAL_LABEL_APPROVED = "FINAL_APPROVED"


def _base(status: str = "PENDING_SPECIALIST") -> dict[str, Any]:
    return {
        "governed_selection": None,
        "governed_probability": None,
        "calibrated_lower_bound": None,
        "calibrated_upper_bound": None,
        "probability_publishable": False,
        "specialist_status": status,
        "probability_source": PROBABILITY_SOURCE,
        "specialist_model_version": None,
        "terminal_label": None,
        "probability_recorded_at": None,
        "can_execute": False,
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _as_utc(value: Any) -> datetime | None:
    """Parse an aware timestamp; naive, unparseable or overflowing values fail.

    `astimezone` can raise `OverflowError` for extreme years once a non-UTC
    offset is applied, so the whole conversion - not just parsing - is guarded.
    A timestamp this boundary cannot represent is never a trustworthy lock.
    """
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        if parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _exact_decimal(value: Any) -> Decimal | None:
    """Return the exact stored decimal in the strict probability domain.

    Binary floats are compared through `float` elsewhere, where two distinct
    numerics can collapse to one value and hide an inverted bound. The ledger
    stores `numeric`, so its text form is parsed as an exact Decimal.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite() or not Decimal(0) < number < Decimal(1):
        return None
    return number


def _verified_receipt(
    *,
    selection: Any,
    probability: Any,
    lower: Any,
    upper: Any,
    publishable: Any,
    terminal_label: Any,
    created_at: Any,
    event_start: Any,
    teams: tuple[Any, Any],
    terminal_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Validate a ledger row as a governed receipt before it is displayed.

    A publishable flag alone is not evidence. The displayed numbers must satisfy
    the strict calibrated domain `0 < lower <= probability <= upper < 1` exactly,
    carry the ledger's own approved terminal label, name a participant in this
    exact event, and have been recorded before the ledger's authoritative event
    lock. A row that fails any check is never rendered as a governed probability.
    """
    if publishable is not True:
        return None
    if not isinstance(selection, str) or not selection.strip():
        return None
    if selection not in {str(team) for team in teams if team is not None}:
        return None
    # The terminal label is read from the ledger/reducer output. Treating a
    # missing label as FINAL_APPROVED would fabricate approval provenance.
    if not isinstance(terminal_label, str) or terminal_label != TERMINAL_LABEL_APPROVED:
        return None
    if terminal_receipt is not None and (
        terminal_receipt.get("status") != "PASS"
        or terminal_receipt.get("global_terminal_reducer") != "V17_TERMINAL_REDUCER"
        or terminal_receipt.get("can_execute") is not False
    ):
        return None
    point = _exact_decimal(probability)
    low = _exact_decimal(lower)
    high = _exact_decimal(upper)
    if point is None or low is None or high is None:
        return None
    if not low <= point <= high:
        return None
    recorded = _as_utc(created_at)
    lock = _as_utc(event_start)
    # The ledger's event start is authoritative for the pregame boundary; the
    # caller's commence_time is not allowed to widen it.
    if recorded is None or lock is None or recorded >= lock:
        return None
    # Display is float at two decimal places. A Decimal inside the exact domain
    # can still round to 1.0 (or 0.0) as a float, or land exactly on a 100.00% /
    # 0.00% boundary once formatted. Either would render a probability the
    # strict domain excludes, so neither is displayed.
    displayed = tuple(float(value) for value in (low, point, high))
    if not all(0.0 < value < 1.0 for value in displayed):
        return None
    if round(displayed[1] * 100.0, 2) >= 100.0 or round(displayed[0] * 100.0, 2) <= 0.0:
        return None
    return {
        "probability": displayed[1],
        "lower": displayed[0],
        "upper": displayed[2],
        "terminal_label": terminal_label,
    }


def _nfl(cur: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    event_id = candidate.get("event_id") or candidate.get("canonical_event_id")
    if not event_id:
        return _base("MODEL_INPUTS_INSUFFICIENT")
    event_key = f"NFL:{event_id}"
    cur.execute(
        """
        select selected_participant,calibrated_selection_probability,calibrated_selection_lower_bound,
               calibrated_selection_upper_bound,model_probability_publishable,model_version,created_at,
               event_start_time_utc,
               public.wow_v17_reduce_nfl_event_terminal_label(score_snapshot_id) as terminal_receipt
        from public.wow_nfl_event_predictions
        where event_key=%s
          and home_team=%s and away_team=%s
          and abs(extract(epoch from (event_start_time_utc-%s::timestamptz))) <= 900
          and model_probability_publishable=true
          and can_execute=false
          and selected_participant in (home_team,away_team)
        order by created_at desc
        limit 1
        """,
        (event_key, candidate.get("home_team"), candidate.get("away_team"), candidate.get("commence_time")),
    )
    row = cur.fetchone()
    if not row:
        return _base()
    selection, probability, lower, upper, publishable, version, created_at, event_start, receipt = row
    # The predictions table has no terminal_label column. The label is derived
    # by the authoritative V17 reducer over this exact immutable row, so a
    # display value can never assert approval the reducer did not produce.
    terminal_receipt = receipt if isinstance(receipt, Mapping) else None
    terminal_label = terminal_receipt.get("terminal_label") if terminal_receipt else None
    verified = _verified_receipt(
        selection=selection,
        probability=probability,
        lower=lower,
        upper=upper,
        publishable=publishable,
        terminal_label=terminal_label,
        created_at=created_at,
        event_start=event_start,
        teams=(candidate.get("home_team"), candidate.get("away_team")),
        terminal_receipt=terminal_receipt,
    )
    if verified is None:
        return _base("MODEL_OUTPUT_INVALID")
    return {
        "governed_selection": str(selection),
        "governed_probability": verified["probability"],
        "calibrated_lower_bound": verified["lower"],
        "calibrated_upper_bound": verified["upper"],
        "probability_publishable": True,
        "specialist_status": "PUBLISHED",
        "probability_source": PROBABILITY_SOURCE,
        "specialist_model_version": version,
        "terminal_label": verified["terminal_label"],
        "probability_recorded_at": _iso(created_at),
        "can_execute": False,
    }


def _mlb(cur: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    cur.execute(
        """
        select selected_participant,
               case when selected_participant=home_team then calibrated_home_probability
                    when selected_participant=away_team then calibrated_away_probability end as calibrated_probability,
               case when selected_participant=home_team then calibrated_home_lower_bound
                    when selected_participant=away_team then calibrated_away_lower_bound end as calibrated_lower_bound,
               case when selected_participant=home_team then calibrated_home_upper_bound
                    when selected_participant=away_team then calibrated_away_upper_bound end as calibrated_upper_bound,
               probability_publishable,controlling_specialist,terminal_label,created_at,event_start_time
        from public.wow_event_predictions
        where home_team=%s and away_team=%s
          and abs(extract(epoch from (event_start_time-%s::timestamptz))) <= 900
          and probability_publishable=true
          and coalesce(probability_invalidated,false)=false
          and can_execute=false
          and selected_participant in (home_team,away_team)
        order by created_at desc
        limit 1
        """,
        (candidate.get("home_team"), candidate.get("away_team"), candidate.get("commence_time")),
    )
    row = cur.fetchone()
    if not row:
        return _base()
    selection, probability, lower, upper, publishable, specialist, terminal_label, created_at, event_start = row
    verified = _verified_receipt(
        selection=selection,
        probability=probability,
        lower=lower,
        upper=upper,
        publishable=publishable,
        terminal_label=terminal_label,
        created_at=created_at,
        event_start=event_start,
        teams=(candidate.get("home_team"), candidate.get("away_team")),
    )
    if verified is None:
        return _base("MODEL_OUTPUT_INVALID")
    return {
        "governed_selection": str(selection),
        "governed_probability": verified["probability"],
        "calibrated_lower_bound": verified["lower"],
        "calibrated_upper_bound": verified["upper"],
        "probability_publishable": True,
        "specialist_status": "PUBLISHED",
        "probability_source": PROBABILITY_SOURCE,
        "specialist_model_version": specialist,
        "terminal_label": verified["terminal_label"],
        "probability_recorded_at": _iso(created_at),
        "can_execute": False,
    }


def lookup_governed_probability(cur: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    """Read a publishable controlling-specialist probability for display only."""
    if candidate.get("can_execute") is not False:
        # Durable Scout rows are fail-closed. Refuse to display probability if
        # a malformed row somehow violates that invariant.
        return _base("SCOUT_GOVERNANCE_INVALID")
    sport_key = str(candidate.get("sport_key") or "")
    try:
        if sport_key == "americanfootball_nfl":
            return _nfl(cur, candidate)
        if sport_key == "baseball_mlb":
            return _mlb(cur, candidate)
    except Exception:
        # A reporting lookup must not break Scout materialization or fabricate
        # probability when the governed ledger is unavailable.
        return _base("SPECIALIST_LEDGER_UNAVAILABLE")
    return _base("MODEL_UNAVAILABLE")


__all__ = ["lookup_governed_probability", "PROBABILITY_SOURCE"]
