"""Read-only NFL full-week event/receipt conservation diagnostic.

This is an offline *coverage auditor*, not an authenticated source verifier,
model scorer, forward grader, publication issuer, Action client, or terminal
reducer. It never changes persisted records or constructs a probability.
Callers supply exact canonical schedule/prediction/async-run rows; this code
refuses to use event-key prefixes or apparent source flags as certification.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
_EVENT_ID = re.compile(r"^(?P<season>[0-9]{4})_(?P<week>[0-9]{2})_(?P<away>[A-Z0-9]{2,4})_(?P<home>[A-Z0-9]{2,4})$")


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _name(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _weekly_run_matches(run: Any, season: int, week: int, expected_game_count: int) -> bool:
    if not isinstance(run, Mapping):
        return False
    request = run.get("request_payload")
    # The authenticated producer's whole-week selector contains an empty
    # date list and an explicit game count. A partial/date-scoped request
    # cannot satisfy the independent exact-week conservation audit.
    return (
        isinstance(request, Mapping)
        and type(request.get("requested_season")) is int
        and type(request.get("requested_week")) is int
        and request["requested_season"] == season
        and request["requested_week"] == week
        and type(request.get("expected_game_count")) is int
        and request["expected_game_count"] == expected_game_count
        and request.get("requested_slate_dates") == []
        and run.get("can_execute") is False
        and _name(run.get("run_id")) is not None
    )


def audit_weekly_receipts(
    schedule_rows: Sequence[Mapping[str, Any]],
    prediction_rows: Sequence[Mapping[str, Any]],
    run_rows: Sequence[Mapping[str, Any]],
    *,
    season: int,
    week: int,
    expected_game_count: int,
    schedule_snapshot_id: str,
) -> dict[str, Any]:
    """Reconcile every exact official week ID without inventing model evidence.

    A caller must externally authenticate the immutable schedule snapshot and
    the recorded Action/worker run. Even a returned COVERAGE_OBSERVED state is
    insufficient for certified forecasts or a publishable Pick'em card.
    """
    if (type(season) is not int or not 2000 <= season <= 2100
            or type(week) is not int or not 1 <= week <= 18
            or type(expected_game_count) is not int or not 1 <= expected_game_count <= 32):
        raise ValueError("PICKEM_AUDIT_REQUEST_INVALID")

    blockers: list[str] = []

    def hold(code: str) -> None:
        if code not in blockers:
            blockers.append(code)

    if not _name(schedule_snapshot_id):
        hold("PICKEM_SCHEDULE_SOURCE_UNVERIFIED")

    events_by_id: dict[str, dict[str, Any]] = {}
    for row in schedule_rows:
        if not isinstance(row, Mapping):
            hold("PICKEM_SCHEDULE_EVENT_IDENTITY_INVALID")
            continue
        official_id = _name(row.get("official_event_id"))
        parsed = _EVENT_ID.fullmatch(official_id) if official_id else None
        row_season, row_week = row.get("season"), row.get("week")
        if not parsed or type(row_season) is not int or type(row_week) is not int:
            hold("PICKEM_SCHEDULE_EVENT_IDENTITY_INVALID")
            continue
        # Fail closed on wrong-week rows instead of treating a textual prefix
        # such as NFL:2026-10-05 (October Week 4) as NFL:2026_10_ (Week 10).
        if (int(parsed.group("season")) != season or int(parsed.group("week")) != week
                or row_season != season or row_week != week):
            hold("PICKEM_SCHEDULE_EVENT_WEEK_IDENTITY_MISMATCH")
            continue
        start = _utc(row.get("event_start_time_utc"))
        home, away = _name(row.get("home_team")), _name(row.get("away_team"))
        if (start is None or not home or not away or home == away
                or home != parsed.group("home") or away != parsed.group("away")):
            hold("PICKEM_SCHEDULE_EVENT_IDENTITY_INVALID")
            continue
        if official_id in events_by_id:
            hold("PICKEM_DUPLICATE_CANONICAL_SCHEDULE_EVENT")
            continue
        events_by_id[official_id] = {"official_event_id": official_id, "start": start}

    if len(events_by_id) != expected_game_count:
        hold("PICKEM_OFFICIAL_SLATE_COUNT_MISMATCH")

    grouped: dict[str, list[tuple[datetime, Mapping[str, Any]]]] = {}
    receipt_events: dict[str, set[str]] = {}
    unmatched: set[str] = set()
    for row in prediction_rows:
        if not isinstance(row, Mapping):
            hold("PICKEM_PREDICTION_ROW_INVALID")
            continue
        official_id = _name(row.get("official_event_id"))
        if official_id not in events_by_id:
            if official_id:
                unmatched.add(official_id)
            else:
                hold("PICKEM_PREDICTION_IDENTITY_INVALID")
            continue
        start = events_by_id[official_id]["start"]
        receipt_id = _name(row.get("event_prediction_id"))
        # Register identity reuse even when this observation cannot qualify
        # as a pregame receipt. An immutable ID cannot belong to two games.
        if receipt_id:
            receipt_events.setdefault(receipt_id, set()).add(official_id)
        created_at = _utc(row.get("created_at"))
        prediction_start = _utc(row.get("event_start_time_utc"))
        if (receipt_id is None or created_at is None
                or prediction_start != start or created_at >= start
                or row.get("can_execute") is not False):
            # An invalid/after-kickoff observation cannot supply pregame proof.
            continue
        grouped.setdefault(official_id, []).append((created_at, row))

    # One immutable prediction cannot satisfy two different official events.
    # Reject every use of a reused ID, rather than selecting an arbitrary side.
    reused_ids = {receipt_id for receipt_id, ids in receipt_events.items() if len(ids) > 1}
    if reused_ids:
        hold("PICKEM_REUSED_IMMUTABLE_PREDICTION_ID")
        for official_id, candidates in grouped.items():
            grouped[official_id] = [item for item in candidates if _name(item[1]["event_prediction_id"]) not in reused_ids]

    events: list[dict[str, Any]] = []
    missing: list[str] = []
    flagged = 0
    for event in sorted(events_by_id.values(), key=lambda x: (x["start"], x["official_event_id"])):
        official_id = event["official_event_id"]
        candidates = grouped.get(official_id, [])
        # A UUID is an identity, not a source ordering sequence. Two distinct
        # receipts at the same timestamp have no proven latest observation.
        candidates.sort(key=lambda item: item[0])
        if candidates:
            last_created_at = candidates[-1][0]
            tied = [row for created_at, row in candidates if created_at == last_created_at]
            distinct_ids = {row["event_prediction_id"] for row in tied}
            distinct_flags = {row.get("model_probability_publishable") is True for row in tied}
            if len(distinct_ids) > 1 or len(distinct_flags) > 1:
                hold("PICKEM_PREGAME_RECEIPT_ORDER_AMBIGUOUS")
                chosen = None
            else:
                chosen = tied[0]
        else:
            chosen = None
        if chosen is None:
            missing.append(official_id)
            hold("PICKEM_PREGAME_PREDICTION_MISSING")
        historical_flag = chosen is not None and chosen.get("model_probability_publishable") is True
        flagged += int(historical_flag)
        events.append({
            "official_event_id": official_id,
            "event_start_time_utc": event["start"].isoformat(),
            "status": "PREGAME_RECEIPT_OBSERVED" if chosen is not None else "PREGAME_RECEIPT_MISSING",
            "latest_prediction_id": chosen.get("event_prediction_id") if chosen is not None else None,
            "latest_stored_model_publishable_flag": historical_flag,
            "probability_publishable": False,
            "can_execute": False,
        })

    matching_runs = [row for row in run_rows if _weekly_run_matches(row, season, week, expected_game_count)]
    completed_runs = [row for row in matching_runs if row.get("run_status") == "COMPLETED"]
    if not matching_runs:
        hold("PICKEM_WEEKLY_RUN_NOT_OBSERVED")
    elif not completed_runs:
        hold("PICKEM_WEEKLY_RUN_NOT_COMPLETED")

    return {
        "status": "HOLD" if blockers else "COVERAGE_OBSERVED_NOT_CERTIFIED",
        "season": season,
        "week": week,
        "expected_game_count": expected_game_count,
        "unique_schedule_event_count": len(events_by_id),
        "pregame_receipt_count": len(events) - len(missing),
        "missing_prediction_event_ids": missing,
        "unmatched_prediction_event_ids": sorted(unmatched),
        "latest_stored_publishable_flag_count": flagged,
        "observed_matching_completed_runs": len(completed_runs),
        "schedule_snapshot_id_reported": _name(schedule_snapshot_id),
        "official_schedule_source_authenticated": False,
        "source_receipts_independently_verified": False,
        "events": events,
        "blockers": blockers,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }
