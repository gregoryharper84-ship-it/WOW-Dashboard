"""Read-only projection of the NFL forward cohort before a slate kicks off.

``MIN_FORWARD_GRADED`` is satisfied only by settled games whose immutable
pregame prediction already existed before kickoff *and* carried every
publication flag. A prediction that is missing, unpublished, invalid or written
after kickoff can never be recovered later: ``wow_nfl_event_predictions`` is
immutable and ``select_canonical_forward_predictions`` drops late rows. The last
moment to close a cohort gap is therefore before the slate starts.

This module reads the exact sources the grader reads, classifies every event in
one slate window, and projects whether the minimum will be reached. It never
grades, never writes, never changes a threshold, never certifies and never
authorizes execution.

``ELIGIBLE_PREGAME`` means only that the immutable pregame prediction is present
and publishable before kickoff. It is not a grade: the game still has to settle
with a non-tie outcome and a home-win field before the grader can count it.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import json
import math
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from v17.nfl_forward_shadow import (
    GRADE_TABLE,
    MIN_FORWARD_GRADED,
    _client,
    _paginate,
    grade_forward_predictions,
    load_outcome_rows,
    load_prediction_rows,
    select_canonical_forward_predictions,
)

CAN_EXECUTE = False
# NFL gamedays are published in US Eastern time. A Sunday-night kickoff rolls
# past midnight UTC, so a naive UTC calendar-day filter would silently drop the
# final game of the slate.
SLATE_TIMEZONE = ZoneInfo("America/New_York")
PUBLICATION_FLAG_FIELDS = (
    "model_package_valid",
    "provenance_complete",
    "model_probability_publishable",
)
_CANNOT_GRADE_PRIORITY = (
    "CAN_EXECUTE_NOT_FALSE",
    "TIMESTAMP_INVALID",
    "MODEL_OUTPUT_INVALID",
    "CALIBRATED_PROBABILITY_BOUNDS_INVALID",
    "SELECTION_IDENTITY_INVALID",
    "TEAM_IDENTITY_INVALID",
    "PUBLICATION_FLAGS_INCOMPLETE",
    "PREDICTION_ONLY_AFTER_KICKOFF",
)


def _parse_utc(value: Any) -> datetime | None:
    """Parse a timezone-aware timestamp, or return None when it is unusable."""
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _probability(value: Any) -> float | None:
    """Parse a probability strictly inside (0, 1), matching the grader."""
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not 0.0 < parsed < 1.0:
        return None
    return parsed


def current_slate_date(now: datetime | None = None) -> date:
    """US/Eastern gameday the daily 12:30 UTC run is reporting on."""
    current = now or datetime.now(timezone.utc)
    if current.utcoffset() is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(SLATE_TIMEZONE).date()


def slate_window(slate_date: date | str) -> tuple[datetime, datetime]:
    """Half-open UTC window covering one US/Eastern gameday."""
    day = slate_date if isinstance(slate_date, date) else date.fromisoformat(str(slate_date))
    start_local = datetime.combine(day, time.min, tzinfo=SLATE_TIMEZONE)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _row_blockers(row: Mapping[str, Any]) -> list[str]:
    """Return the defects that keep one row out of the canonical cohort."""
    blockers: list[str] = []
    if row.get("can_execute") is not False:
        blockers.append("CAN_EXECUTE_NOT_FALSE")
    home = str(row.get("home_team") or "").strip()
    away = str(row.get("away_team") or "").strip()
    if not home or not away or home == away:
        blockers.append("TEAM_IDENTITY_INVALID")
    if str(row.get("selected_participant") or "").strip() not in {home, away}:
        blockers.append("SELECTION_IDENTITY_INVALID")
    point = _probability(row.get("calibrated_selection_probability"))
    lower = _probability(row.get("calibrated_selection_lower_bound"))
    upper = _probability(row.get("calibrated_selection_upper_bound"))
    if point is None or lower is None or upper is None:
        blockers.append("MODEL_OUTPUT_INVALID")
    elif not lower <= point <= upper:
        blockers.append("CALIBRATED_PROBABILITY_BOUNDS_INVALID")
    if any(row.get(field) is not True for field in PUBLICATION_FLAG_FIELDS):
        blockers.append("PUBLICATION_FLAGS_INCOMPLETE")
    return blockers


def _grader_rejection(row: Mapping[str, Any]) -> str | None:
    """Return the code the forward grader would abort on, or None.

    ``select_canonical_forward_predictions`` validates every loaded row, not
    only the rows inside one slate window, and raises instead of skipping. One
    such row therefore stops the whole nightly grading run, so the projection
    has to surface it rather than report readiness it cannot deliver.
    """
    event_prediction_id = str(row.get("event_prediction_id") or "").strip()
    official_event_id = str(row.get("official_event_id") or "").strip()
    if not event_prediction_id or not official_event_id:
        return "PREDICTION_IDENTITY_MISSING"
    if row.get("can_execute") is True:
        return "CAN_EXECUTE_MUST_BE_FALSE"
    home = str(row.get("home_team") or "").strip()
    away = str(row.get("away_team") or "").strip()
    selected = str(row.get("selected_participant") or "").strip()
    if not home or not away or selected not in {home, away}:
        return "PREDICTION_SELECTION_IDENTITY_INVALID"
    point = _probability(row.get("calibrated_selection_probability"))
    lower = _probability(row.get("calibrated_selection_lower_bound"))
    upper = _probability(row.get("calibrated_selection_upper_bound"))
    # A probability that rounds to exactly 1.0 is rejected by the grader too.
    if point is None:
        return "MODEL_OUTPUT_INVALID:calibrated_selection_probability"
    if lower is None:
        return "MODEL_OUTPUT_INVALID:calibrated_selection_lower_bound"
    if upper is None:
        return "MODEL_OUTPUT_INVALID:calibrated_selection_upper_bound"
    if not lower <= point <= upper:
        return "CALIBRATED_PROBABILITY_BOUNDS_INVALID"
    for field in ("event_start_time_utc", "created_at"):
        if _parse_utc(row.get(field)) is None:
            return f"TIMESTAMP_MUST_BE_TIMEZONE_AWARE:{field}"
    return None


def _cannot_grade_reason(blockers: Sequence[str]) -> str:
    """Name the most specific unrecoverable reason for one event."""
    for code in _CANNOT_GRADE_PRIORITY:
        if code in blockers:
            return code
    return "NO_ELIGIBLE_PREGAME_PREDICTION"


def assess_slate_event(
    event_id: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    already_graded: bool,
) -> dict[str, Any]:
    """Classify one event using only immutable pregame fields.

    Mirrors ``select_canonical_forward_predictions``: latest valid row created
    strictly before kickoff wins, and every publication flag must be true.
    """
    kickoffs = [parsed for parsed in (_parse_utc(row.get("event_start_time_utc")) for row in rows) if parsed]
    kickoff = min(kickoffs) if kickoffs else None
    eligible: list[tuple[datetime, str, Mapping[str, Any]]] = []
    blockers: set[str] = set()
    for row in rows:
        blockers.update(_row_blockers(row))
        created = _parse_utc(row.get("created_at"))
        if created is None:
            blockers.add("TIMESTAMP_INVALID")
            continue
        if kickoff is not None and created < kickoff and not _row_blockers(row):
            eligible.append((
                created,
                str(row.get("event_prediction_id") or ""),
                row,
            ))
    if kickoff is not None and not eligible and not any(
        parsed < kickoff
        for parsed in (
            _parse_utc(row.get("created_at")) for row in rows
        ) if parsed is not None
    ):
        blockers.add("PREDICTION_ONLY_AFTER_KICKOFF")

    record: dict[str, Any] = {
        "official_event_id": event_id,
        "prediction_rows": len(rows),
        "event_start_time_utc": kickoff.isoformat() if kickoff else None,
        "blockers": sorted(blockers),
        "can_execute": False,
    }
    if already_graded:
        return {**record, "status": "ALREADY_GRADED", "cannot_grade_reason": None}
    if eligible:
        created, prediction_id, row = max(eligible, key=lambda item: (item[0], item[1]))
        return {
            **record,
            "status": "ELIGIBLE_PREGAME",
            "cannot_grade_reason": None,
            "event_prediction_id": prediction_id,
            "prediction_created_at": created.isoformat(),
            "model_version": str(row.get("model_version") or ""),
            "selected_participant": str(row.get("selected_participant") or ""),
            "calibrated_selection_probability": float(row["calibrated_selection_probability"]),
        }
    return {
        **record,
        "status": "CANNOT_GRADE",
        "cannot_grade_reason": _cannot_grade_reason(sorted(blockers)),
    }


def project_forward_cohort(
    db: Any,
    *,
    slate_date: date | str,
    min_forward: int = MIN_FORWARD_GRADED,
    expected_slate_games: int | None = None,
) -> dict[str, Any]:
    """Project the cohort size after one slate settles. Read-only and fail-closed."""
    if type(min_forward) is not int or min_forward <= 0:
        raise ValueError("min_forward must be a positive integer")
    if expected_slate_games is not None and (
        type(expected_slate_games) is not int or expected_slate_games <= 0
    ):
        raise ValueError("expected_slate_games must be a positive integer or None")

    start, end = slate_window(slate_date)
    # Persisted rows are audit history, not the grader's current denominator:
    # corrected settlements (especially ties) are excluded by the grader but
    # never delete an old persisted grade.
    persisted_ids = {
        str(row.get("official_event_id") or "").strip()
        for row in _paginate(db, GRADE_TABLE, "official_event_id")
    }
    persisted_ids.discard("")
    prediction_rows = load_prediction_rows(db)
    # The grader validates the whole ledger, so an invalid row outside this
    # slate still prevents every slate game from being graded.
    ledger_violations: list[dict[str, str]] = []
    for row in prediction_rows:
        violation = _grader_rejection(row)
        if violation is not None:
            ledger_violations.append({
                "official_event_id": str(row.get("official_event_id") or ""),
                "event_prediction_id": str(row.get("event_prediction_id") or ""),
                "code": violation,
            })

    # Match run_forward_shadow: canonical pregame rows + *current* settlement.
    # An invalid ledger makes that grader abort, so no counted grade is proven.
    outcome_rows = load_outcome_rows(db)
    current_grade_ids = (
        {grade.official_event_id for grade in grade_forward_predictions(
            select_canonical_forward_predictions(prediction_rows), outcome_rows
        )}
        if not ledger_violations else set()
    )
    graded_ids = current_grade_ids
    graded_n = len(graded_ids)
    outcome_index = {
        str(row.get("game_id") or "").strip(): row
        for row in outcome_rows if str(row.get("game_id") or "").strip()
    }

    by_event: dict[str, list[Mapping[str, Any]]] = {}
    for row in prediction_rows:
        kickoff = _parse_utc(row.get("event_start_time_utc"))
        if kickoff is None or not start <= kickoff < end:
            continue
        event_id = str(row.get("official_event_id") or "").strip()
        if not event_id:
            continue
        by_event.setdefault(event_id, []).append(row)

    events = []
    for event_id, rows in sorted(by_event.items()):
        event = assess_slate_event(event_id, rows, already_graded=event_id in graded_ids)
        if event_id not in graded_ids and outcome_index.get(event_id, {}).get("tie") is True:
            # The game has settled as a tie; it can never enter the binary
            # forward cohort, even if the pregame prediction was eligible.
            event["status"] = "CANNOT_GRADE"
            event["cannot_grade_reason"] = "SETTLED_TIE_EXCLUDED"
            event["blockers"] = sorted(set(event["blockers"]) | {"SETTLED_TIE_EXCLUDED"})
        events.append(event)
    eligible_pregame_n = sum(1 for event in events if event["status"] == "ELIGIBLE_PREGAME")
    already_graded_n = sum(1 for event in events if event["status"] == "ALREADY_GRADED")
    cannot_grade = [event for event in events if event["status"] == "CANNOT_GRADE"]
    projected_n = graded_n + eligible_pregame_n

    blockers: list[str] = []
    if projected_n < min_forward:
        blockers.append(f"FORWARD_GRADED_PROJECTED_{projected_n}_LT_{min_forward}")
    if cannot_grade:
        blockers.append(f"NFL_FORWARD_SLATE_EVENTS_CANNOT_GRADE:{len(cannot_grade)}")
    # Coverage is verified only when both independently supplied counts agree.
    # More observed events than expected is a mismatch, not proof of coverage.
    coverage_verified = expected_slate_games is not None and len(events) == expected_slate_games
    unobserved = 0
    if expected_slate_games is None:
        blockers.append("NFL_FORWARD_SLATE_COVERAGE_UNVERIFIED")
    elif len(events) < expected_slate_games:
        unobserved = expected_slate_games - len(events)
        blockers.append(f"NFL_FORWARD_SLATE_GAMES_UNOBSERVED:{unobserved}")
    elif len(events) > expected_slate_games:
        blockers.append(f"NFL_FORWARD_SLATE_COUNT_MISMATCH:{len(events)}_GT_{expected_slate_games}")
    if persisted_ids != graded_ids:
        blockers.append(f"NFL_FORWARD_PERSISTED_GRADE_DRIFT:{len(persisted_ids)}_VS_{graded_n}")
    if ledger_violations:
        blockers.append(f"PREDICTION_LEDGER_WOULD_FAIL_GRADER:{len(ledger_violations)}")

    if ledger_violations:
        status = "HOLD_PREDICTION_LEDGER_INVALID"
    elif persisted_ids != graded_ids:
        status = "HOLD_PERSISTED_FORWARD_GRADE_DRIFT"
    elif projected_n < min_forward:
        status = "PROJECTED_SHORT_OF_MINIMUM"
    elif not coverage_verified:
        status = "PROJECTED_TO_REACH_MINIMUM_COVERAGE_UNVERIFIED"
    elif cannot_grade:
        # A high enough numeric projection cannot conceal known irreversible
        # gaps in the full expected slate.
        status = "HOLD_SLATE_EVENTS_CANNOT_GRADE"
    else:
        status = "PROJECTED_TO_REACH_MINIMUM"

    return {
        "schema_version": "NFL_FORWARD_COHORT_PREFLIGHT_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sport": "NFL",
        "market_family": "OUTRIGHT_WINNER",
        "slate_date": start.astimezone(SLATE_TIMEZONE).date().isoformat(),
        "slate_window_utc": [start.isoformat(), end.isoformat()],
        "minimum_forward_required": int(min_forward),
        "graded_n": graded_n,
        "persisted_grade_n": len(persisted_ids),
        "current_cohort_reconciled_with_persisted_grades": persisted_ids == graded_ids,
        "current_gap_to_minimum": max(min_forward - graded_n, 0),
        "expected_slate_games": expected_slate_games,
        "observed_slate_games": len(events),
        "unobserved_slate_games": unobserved,
        "slate_coverage_verified": coverage_verified,
        "slate_already_graded_n": already_graded_n,
        "eligible_pregame_n": eligible_pregame_n,
        "cannot_grade_n": len(cannot_grade),
        "projected_graded_n": projected_n,
        "projected_gap_to_minimum": max(min_forward - projected_n, 0),
        "status": status,
        # Pregame eligibility is not a grade: ties are excluded and the outcome
        # must expose home_win before the grader counts a game.
        "settlement_requirements": ["SETTLED_NON_TIE_OUTCOME", "HOME_WIN_PRESENT"],
        "pregame_eligibility_is_not_a_grade": True,
        "ledger_violation_n": len(ledger_violations),
        "ledger_violations": ledger_violations[:50],
        "projection_is_certification_evidence": False,
        "certification_recommendation": "DO_NOT_CERTIFY_YET",
        "blockers": blockers,
        "slate_events": events,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Read-only projection of the NFL forward cohort before a slate starts."
    )
    parser.add_argument("--slate-date", required=True, help="US/Eastern gameday, e.g. 2026-11-15")
    parser.add_argument("--expected-slate-games", type=int, default=None)
    parser.add_argument("--min-forward", type=int, default=MIN_FORWARD_GRADED)
    args = parser.parse_args(argv)
    print(json.dumps(
        project_forward_cohort(
            _client(),
            slate_date=args.slate_date,
            min_forward=args.min_forward,
            expected_slate_games=args.expected_slate_games,
        ),
        sort_keys=True,
        default=str,
    ))


if __name__ == "__main__":
    main()


__all__ = [
    "CAN_EXECUTE",
    "SLATE_TIMEZONE",
    "assess_slate_event",
    "current_slate_date",
    "project_forward_cohort",
    "slate_window",
]
