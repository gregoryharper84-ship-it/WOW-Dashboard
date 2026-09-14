"""Resolve display-only governed specialist probabilities for Scout boards.

Scout remains research-only and never owns probability. This module reads only
publishable outputs already produced by the controlling fitted specialist. It
never derives probability from sportsbook price, never writes Scout probability,
and never changes execution semantics.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

CAN_EXECUTE = False
PROBABILITY_SOURCE = "CONTROLLING_SPECIALIST"


def _base(status: str = "PENDING_SPECIALIST") -> dict[str, Any]:
    return {
        "governed_selection": None,
        "governed_probability": None,
        "calibrated_lower_bound": None,
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


def _nfl(cur: Any, candidate: dict[str, Any]) -> dict[str, Any]:
    event_id = candidate.get("event_id") or candidate.get("canonical_event_id")
    if not event_id:
        return _base("MODEL_INPUTS_INSUFFICIENT")
    event_key = f"NFL:{event_id}"
    cur.execute(
        """
        select selected_participant,calibrated_selection_probability,calibrated_selection_lower_bound,
               model_probability_publishable,model_version,created_at
        from public.wow_nfl_event_predictions
        where event_key=%s
          and home_team=%s and away_team=%s
          and abs(extract(epoch from (event_start_time_utc-%s::timestamptz))) <= 900
          and model_probability_publishable=true
          and can_execute=false
        order by created_at desc
        limit 1
        """,
        (event_key, candidate.get("home_team"), candidate.get("away_team"), candidate.get("commence_time")),
    )
    row = cur.fetchone()
    if not row:
        return _base()
    selection, probability, lower, publishable, version, created_at = row
    if publishable is not True or probability is None or lower is None or not selection:
        return _base("MODEL_OUTPUT_INVALID")
    return {
        "governed_selection": str(selection),
        "governed_probability": float(probability),
        "calibrated_lower_bound": float(lower),
        "probability_publishable": True,
        "specialist_status": "PUBLISHED",
        "probability_source": PROBABILITY_SOURCE,
        "specialist_model_version": version,
        "terminal_label": "FINAL_APPROVED",
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
               probability_publishable,controlling_specialist,terminal_label,created_at
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
    selection, probability, lower, publishable, specialist, terminal_label, created_at = row
    if publishable is not True or probability is None or lower is None or not selection:
        return _base("MODEL_OUTPUT_INVALID")
    return {
        "governed_selection": str(selection),
        "governed_probability": float(probability),
        "calibrated_lower_bound": float(lower),
        "probability_publishable": True,
        "specialist_status": "PUBLISHED",
        "probability_source": PROBABILITY_SOURCE,
        "specialist_model_version": specialist,
        "terminal_label": terminal_label or "FINAL_APPROVED",
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
