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
        select p.selected_participant,p.calibrated_selection_probability,p.calibrated_selection_lower_bound,
               p.model_probability_publishable,p.model_version,p.created_at
        from public.wow_nfl_event_predictions as p
        join public.wow_nfl_event_fitted_model_artifacts as artifact
          on artifact.artifact_id=p.model_artifact_id
         and artifact.active=true
         and artifact.promoted=true
         and artifact.lifecycle_state='CHAMPION'
         and artifact.can_execute=false
        -- The agent-runtime candidate ledger is not the daily NFL scoring
        -- terminal ledger. Inspect the single newest daily-row decision for
        -- this exact official event BEFORE applying any approval filters:
        -- later HOLD must supersede an earlier FINAL_APPROVED.
        join lateral (
            select daily.row_status,daily.detail,daily.captured_at,daily.can_execute
            from public.wow_v17_daily_run_row_detail as daily
            where daily.identity->>'sport'='NFL'
              and daily.lane='MONEYLINE'
              and daily.identity->>'official_event_id'=p.official_event_id
            order by daily.captured_at desc,daily.row_index desc,daily.run_id desc
            limit 1
        ) as latest on true
        where p.event_key=%s
          and p.home_team=%s and p.away_team=%s
          and abs(extract(epoch from (p.event_start_time_utc-%s::timestamptz))) <= 900
          and p.model_probability_publishable=true
          and p.can_execute=false
          -- A newer persisted score without a matching terminal receipt
          -- supersedes any earlier approved prediction of the same event.
          and not exists (
              select 1 from public.wow_nfl_event_predictions as newer
              where newer.official_event_id=p.official_event_id
                and (newer.created_at,newer.event_prediction_id)
                    > (p.created_at,p.event_prediction_id)
          )
          and latest.row_status='COMPLETED'
          and latest.can_execute=false
          -- Require latest final reducer output, not earlier upstream approval.
          and latest.detail->'result'->>'global_terminal_authority'='V17_TERMINAL_REDUCER'
          and latest.detail->'result'->>'event_prediction_id'=p.event_prediction_id::text
          and latest.detail->'result'->>'selected_participant'=p.selected_participant
          and latest.detail->'result'->>'terminal_label'='FINAL_APPROVED'
          and latest.detail->'result'->>'terminal_ceiling'='FINAL_APPROVED'
          and latest.detail->'result'->>'probability_publishable'='true'
          and latest.detail->'result'->>'rank_eligible'='true'
          and latest.detail->>'probability_publishable'='true'
          and latest.captured_at >= p.created_at
          and latest.captured_at >= p.event_start_time_utc - interval '7 days'
          and latest.captured_at < p.event_start_time_utc
        order by p.created_at desc
        limit 1
        """,
        (event_key, candidate.get("home_team"), candidate.get("away_team"), candidate.get("commence_time")),
    )
    row = cur.fetchone()
    if not row:
        # A fitted prediction is not a terminal publication receipt.
        # Absence of exact run/candidate/reducer proof remains a HOLD.
        return _base("TERMINAL_PROOF_NOT_VERIFIED")
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
