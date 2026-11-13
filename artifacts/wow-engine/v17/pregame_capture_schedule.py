"""Bounded, research-only pregame capture scheduling for NBA and NHL.

Existing sport maintenance runs as OIDC-authenticated internal routes driven by
hourly/daily GitHub workflows. Pregame evidence needs a much tighter cadence, so
this module provides a *bounded* planner/runner an existing maintenance job can
invoke without adding a new schedule surface:

- official event discovery from the same first-party feeds the capture layer
  already reads (ESPN scoreboard, NHL schedule);
- a per-sport pregame window with an explicit open, close and freshness floor;
- a hard attempt budget and per-cycle capture budget, so repeated polling is
  bounded rather than open-ended;
- content-addressed deduplication: identical payloads are never stored twice,
  and a fresh receipt suppresses the next poll until the freshness floor;
- immutable retention: receipts are never deleted here; the selector reports
  which receipts to retain and which are superseded for archival.

It never infers an NHL starting goalie from a candidate list. If the official
payload does not mark a starter, the capture stays at candidate evidence.

It does not fit, calibrate, rank, publish, persist to a governed table or
execute anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from v17.pregame_source_capture import (
    CaptureTransportError,
    NBA_SCOREBOARD_URL,
    SourceFetch,
    capture_nba_pregame,
    capture_nhl_goalie_evidence,
    fetch_source,
    verify_capture_receipt,
    write_capture_artifact,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RESEARCH_ONLY = True
POLICY_VERSION = "wow.v17.pregame_capture_policy.v1"
NHL_SCHEDULE_URL = "https://api-web.nhle.com/v1/schedule/now"
DISCOVERY_HORIZON_HOURS = 12
MAX_EVENTS_PER_CYCLE = 12
MAX_CAPTURES_PER_CYCLE = 6
MAX_RETAINED_RECEIPTS = 6
_SIDES = ("home", "away")


@dataclass(frozen=True)
class CaptureWindow:
    """Pre-registered pregame capture policy for one sport."""

    open_minutes_before_start: int
    close_minutes_before_start: int
    freshness_minutes: int
    max_attempts: int


NBA_WINDOW = CaptureWindow(
    open_minutes_before_start=120,
    close_minutes_before_start=10,
    freshness_minutes=15,
    max_attempts=8,
)
NHL_WINDOW = CaptureWindow(
    open_minutes_before_start=180,
    close_minutes_before_start=5,
    freshness_minutes=15,
    max_attempts=12,
)
WINDOWS = {"NBA": NBA_WINDOW, "NHL": NHL_WINDOW}


def window_for(sport: str) -> CaptureWindow:
    key = str(sport or "").strip().upper()
    if key not in WINDOWS:
        raise ValueError(f"unsupported capture sport: {sport!r}")
    return WINDOWS[key]


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _require_now(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _record(fetch: SourceFetch) -> dict[str, Any]:
    return {
        "url": fetch.url,
        "status": fetch.status,
        "http_date": fetch.http_date,
        "payload_sha256": fetch.payload_sha256(),
        "payload_bytes": len(fetch.body),
        "received_at_utc": fetch.received_at_utc.isoformat(),
    }


def _merge_discovered(
    by_id: dict[str, dict[str, Any]],
    conflicts: set[str],
    candidate: dict[str, Any],
    blockers: list[str],
) -> None:
    event_id = candidate["official_event_id"]
    if not candidate.get("home") or not candidate.get("away"):
        blockers.append("CAPTURE_DISCOVERY_TEAM_IDENTITY_INVALID")
        return
    existing = by_id.get(event_id)
    if existing is None:
        by_id[event_id] = candidate
        return
    if (
        existing.get("start_time_utc") != candidate.get("start_time_utc")
        or existing.get("home") != candidate.get("home")
        or existing.get("away") != candidate.get("away")
    ):
        conflicts.add(event_id)
        blockers.append("CAPTURE_DISCOVERY_EVENT_CONFLICT")


def discover_pregame_events(
    sport: str,
    *,
    now: datetime,
    fetcher: Callable[[str], SourceFetch] = fetch_source,
    horizon_hours: int = DISCOVERY_HORIZON_HOURS,
    scoreboard_url: str = NBA_SCOREBOARD_URL,
    schedule_url: str = NHL_SCHEDULE_URL,
) -> dict[str, Any]:
    """Discover official events that are still pregame inside the horizon."""
    key = str(sport or "").strip().upper()
    window = window_for(key)
    captured_at = _require_now(now)
    horizon_end = captured_at + timedelta(hours=max(int(horizon_hours), 1))
    blockers: list[str] = []
    url = scoreboard_url if key == "NBA" else schedule_url
    try:
        fetch = fetcher(url)
        payload = _mapping(fetch.payload()) or {}
    except CaptureTransportError:
        return {
            "status": "HELD",
            "sport": key,
            "blockers": ["CAPTURE_DISCOVERY_SOURCE_UNAVAILABLE"],
            "events": [],
            "source_responses": [],
            "starter_inference_allowed": False,
            "research_only": RESEARCH_ONLY,
            "probability_publishable": False,
            "can_execute": False,
        }

    by_id: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    if key == "NBA":
        for raw in payload.get("events") or []:
            event = _mapping(raw) or {}
            event_id = str(event.get("id") or "").strip()
            comp = _mapping((event.get("competitions") or [None])[0]) or {}
            status_block = _mapping(comp.get("status")) or _mapping(event.get("status")) or {}
            type_block = _mapping(status_block.get("type")) or {}
            state = str(type_block.get("state") or "").strip().lower()
            start = _aware(event.get("date")) or _aware(comp.get("date"))
            if state != "pre":
                continue
            if start is None:
                blockers.append("CAPTURE_DISCOVERY_EVENT_INVALID")
                continue
            if start <= captured_at or start > horizon_end:
                continue
            competitors = [_mapping(item) or {} for item in comp.get("competitors") or []]
            sides = {
                str(item.get("homeAway") or "").strip().lower(): _mapping(item.get("team")) or {}
                for item in competitors
            }
            home_team = sides.get("home") or {}
            away_team = sides.get("away") or {}
            if (
                not event_id.isdigit()
                or not home_team.get("id")
                or not away_team.get("id")
                or home_team.get("id") == away_team.get("id")
            ):
                blockers.append("CAPTURE_DISCOVERY_EVENT_INVALID")
                continue
            _merge_discovered(
                by_id,
                conflicts,
                {
                    "sport": "NBA",
                    "official_event_id": f"espn-{event_id}",
                    "provider_event_id": event_id,
                    "start_time_utc": start.isoformat(),
                    "state": "PRE",
                    "home": str(home_team.get("abbreviation") or "").strip().upper() or None,
                    "away": str(away_team.get("abbreviation") or "").strip().upper() or None,
                },
                blockers,
            )
    else:
        for day in payload.get("gameWeek") or []:
            for raw in (_mapping(day) or {}).get("games") or []:
                game = _mapping(raw) or {}
                event_id = str(game.get("id") or "").strip()
                state = str(game.get("gameState") or "").strip().upper()
                start = _aware(game.get("startTimeUTC"))
                if (
                    len(event_id) != 10
                    or not event_id.isdigit()
                    or state not in {"FUT", "PRE"}
                    or start is None
                    or start <= captured_at
                    or start > horizon_end
                ):
                    continue
                _merge_discovered(
                    by_id,
                    conflicts,
                    {
                        "sport": "NHL",
                        "official_event_id": event_id,
                        "provider_event_id": event_id,
                        "start_time_utc": start.isoformat(),
                        "state": state,
                        "home": str((_mapping(game.get("homeTeam")) or {}).get("abbrev") or "").strip().upper() or None,
                        "away": str((_mapping(game.get("awayTeam")) or {}).get("abbrev") or "").strip().upper() or None,
                    },
                    blockers,
                )

    ordered = sorted(
        (event for event_id, event in by_id.items() if event_id not in conflicts),
        key=lambda event: (event["start_time_utc"], event["official_event_id"]),
    )
    deferred = ordered[MAX_EVENTS_PER_CYCLE:]
    discovered = ordered[:MAX_EVENTS_PER_CYCLE]
    if deferred:
        blockers.append("CAPTURE_DISCOVERY_TRUNCATED")
    return {
        "status": "DISCOVERED",
        "sport": key,
        "generated_at_utc": fetch.received_at_utc.isoformat(),
        "window": {
            "open_minutes_before_start": window.open_minutes_before_start,
            "close_minutes_before_start": window.close_minutes_before_start,
            "freshness_minutes": window.freshness_minutes,
            "max_attempts": window.max_attempts,
            "policy_version": POLICY_VERSION,
        },
        "events": discovered,
        "deferred_event_ids": [event["official_event_id"] for event in deferred],
        "blockers": sorted(set(blockers)),
        "source_responses": [_record(fetch)],
        "starter_inference_allowed": False,
        "research_only": RESEARCH_ONLY,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def _latest_receipt(
    receipts: Iterable[Mapping[str, Any]], event_id: str
) -> Mapping[str, Any] | None:
    latest: Mapping[str, Any] | None = None
    latest_at: datetime | None = None
    for receipt in receipts:
        if str(receipt.get("official_event_id") or "") != event_id:
            continue
        captured = _aware(receipt.get("captured_at_utc"))
        if captured is None:
            continue
        if latest_at is None or captured > latest_at:
            latest, latest_at = receipt, captured
    return latest


def plan_pregame_capture_cycle(
    sport: str,
    *,
    now: datetime,
    events: Sequence[Mapping[str, Any]],
    receipts: Sequence[Mapping[str, Any]] = (),
    attempts_by_event: Mapping[str, int] | None = None,
    max_captures: int = MAX_CAPTURES_PER_CYCLE,
) -> dict[str, Any]:
    """Decide which official events are due for a bounded capture attempt."""
    key = str(sport or "").strip().upper()
    window = window_for(key)
    captured_at = _require_now(now)
    attempts = attempts_by_event or {}
    decisions: list[dict[str, Any]] = []
    due: list[str] = []
    for raw in events:
        event = _mapping(raw) or {}
        event_id = str(event.get("official_event_id") or "").strip()
        start = _aware(event.get("start_time_utc"))
        if not event_id or start is None:
            decisions.append(
                {"official_event_id": event_id or None, "decision": "INVALID_EVENT"}
            )
            continue
        attempt_count = max(int(attempts.get(event_id, 0)), 0)
        last = _latest_receipt(receipts, event_id)
        last_at = _aware(last.get("captured_at_utc")) if last is not None else None
        row: dict[str, Any] = {
            "official_event_id": event_id,
            "start_time_utc": start.isoformat(),
            "attempts": attempt_count,
            "last_receipt_at_utc": last_at.isoformat() if last_at is not None else None,
            "last_capture_sha256": last.get("capture_sha256") if last is not None else None,
        }
        if captured_at >= start:
            row.update(decision="EXPIRED", next_action_at_utc=None)
        elif captured_at >= start - timedelta(minutes=window.close_minutes_before_start):
            row.update(decision="WINDOW_CLOSED", next_action_at_utc=None)
        elif captured_at >= start - timedelta(minutes=window.open_minutes_before_start):
            if attempt_count >= window.max_attempts:
                row.update(decision="EXHAUSTED", next_action_at_utc=None)
            elif last_at is not None and captured_at - last_at < timedelta(
                minutes=window.freshness_minutes
            ):
                row.update(
                    decision="FRESH",
                    next_action_at_utc=(
                        last_at + timedelta(minutes=window.freshness_minutes)
                    ).isoformat(),
                )
            else:
                row.update(decision="DUE", next_action_at_utc=captured_at.isoformat())
                due.append(event_id)
        else:
            row.update(
                decision="NOT_DUE",
                next_action_at_utc=(
                    start - timedelta(minutes=window.open_minutes_before_start)
                ).isoformat(),
            )
        decisions.append(row)

    budget = max(int(max_captures), 0)
    selected = due[:budget]
    return {
        "status": "PLANNED",
        "sport": key,
        "generated_at_utc": captured_at.isoformat(),
        "policy_version": POLICY_VERSION,
        "window": {
            "open_minutes_before_start": window.open_minutes_before_start,
            "close_minutes_before_start": window.close_minutes_before_start,
            "freshness_minutes": window.freshness_minutes,
            "max_attempts": window.max_attempts,
        },
        "events": decisions,
        "due_event_ids": selected,
        "deferred_event_ids": due[budget:],
        "capture_budget": budget,
        "starter_inference_allowed": False,
        "research_only": RESEARCH_ONLY,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def select_retained_receipts(
    receipts: Iterable[Mapping[str, Any]],
    *,
    max_receipts: int = MAX_RETAINED_RECEIPTS,
) -> dict[str, Any]:
    """Choose immutable receipts to retain; never delete or rewrite one here.

    Retention keeps the first and latest capture of an event, the first receipt
    of every distinct evidence state (status plus confirmed identities), then
    the most recent remaining receipts up to the cap. Identical payload digests
    are collapsed without losing the fact that they repeated.
    """
    ordered: list[Mapping[str, Any]] = [
        receipt for receipt in receipts if _aware(receipt.get("captured_at_utc"))
    ]
    ordered.sort(
        key=lambda item: (
            _aware(item.get("captured_at_utc")) or datetime.min.replace(tzinfo=timezone.utc),
            str(item.get("capture_sha256") or ""),
        )
    )
    unique: list[Mapping[str, Any]] = []
    seen_sha: set[str] = set()
    duplicated: list[str] = []
    for receipt in ordered:
        digest = str(receipt.get("capture_sha256") or "")
        if digest and digest in seen_sha:
            duplicated.append(digest)
            continue
        if digest:
            seen_sha.add(digest)
        unique.append(receipt)

    cap = max(int(max_receipts), 1)
    if len(unique) <= cap:
        retained = unique
    else:
        keep: set[int] = {0, len(unique) - 1}
        states: dict[tuple[str, tuple[str, ...]], int] = {}
        for index, receipt in enumerate(unique):
            confirmed = _mapping(receipt.get("confirmed_starter_ids")) or {}
            state = (
                str(receipt.get("status") or ""),
                tuple(
                    sorted(
                        str(confirmed.get(side))
                        for side in _SIDES
                        if confirmed.get(side)
                    )
                ),
            )
            states.setdefault(state, index)
        keep.update(states.values())
        for index in range(len(unique) - 1, -1, -1):
            if len(keep) >= cap:
                break
            keep.add(index)
        retained = [unique[index] for index in sorted(keep)[:cap]]
    superseded = [receipt for receipt in unique if receipt not in retained]
    return {
        "status": "RETENTION_SELECTED",
        "retained": retained,
        "superseded": superseded,
        "deduplicated_sha256": sorted(set(duplicated)),
        "retained_count": len(retained),
        "superseded_count": len(superseded),
        "max_receipts": cap,
        "deletion_performed": False,
        "research_only": RESEARCH_ONLY,
        "can_execute": False,
    }


def _capture_event(
    sport: str,
    event: Mapping[str, Any],
    *,
    now: datetime,
    fetcher: Callable[[str], SourceFetch],
) -> dict[str, Any]:
    event_id = str(event.get("official_event_id") or "")
    provider_id = str(event.get("provider_event_id") or event_id.replace("espn-", ""))
    if sport == "NBA":
        return capture_nba_pregame(provider_id, now=now, fetcher=fetcher)
    return capture_nhl_goalie_evidence(event_id, now=now, fetcher=fetcher)


def run_pregame_capture_cycle(
    sport: str,
    *,
    now: datetime,
    fetcher: Callable[[str], SourceFetch] = fetch_source,
    events: Sequence[Mapping[str, Any]] | None = None,
    receipts: Sequence[Mapping[str, Any]] = (),
    attempts_by_event: Mapping[str, int] | None = None,
    artifact_root: str | None = None,
    max_captures: int = MAX_CAPTURES_PER_CYCLE,
) -> dict[str, Any]:
    """Capture the events a plan marks DUE, within a hard per-cycle budget."""
    key = str(sport or "").strip().upper()
    captured_at = _require_now(now)
    discovery = (
        None
        if events is not None
        else discover_pregame_events(key, now=captured_at, fetcher=fetcher)
    )
    if discovery is not None and discovery["status"] != "DISCOVERED":
        return {
            "status": "CYCLE_HELD",
            "sport": key,
            "blockers": list(discovery["blockers"]),
            "discovery": discovery,
            "plan": None,
            "captures": [],
            "receipts": [],
            "retention": select_retained_receipts(receipts),
            "starter_inference_allowed": False,
            "research_only": RESEARCH_ONLY,
            "probability_publishable": False,
            "can_execute": False,
        }
    planned_events = list(events or (discovery or {}).get("events") or [])
    plan = plan_pregame_capture_cycle(
        key,
        now=captured_at,
        events=planned_events,
        receipts=receipts,
        attempts_by_event=attempts_by_event,
        max_captures=max_captures,
    )
    by_id = {str(event.get("official_event_id") or ""): event for event in planned_events}
    captures: list[dict[str, Any]] = []
    new_receipts: list[dict[str, Any]] = []
    for event_id in plan["due_event_ids"]:
        event = by_id.get(event_id)
        if event is None:
            continue
        try:
            receipt = _capture_event(key, event, now=captured_at, fetcher=fetcher)
        except (CaptureTransportError, ValueError) as exc:
            captures.append(
                {
                    "official_event_id": event_id,
                    "status": "HELD",
                    "blockers": [f"CAPTURE_PLANNER_INPUT_ERROR:{type(exc).__name__}"],
                    "capture_sha256": None,
                    "capture_verified": False,
                    "deduplicated": False,
                    "artifact": None,
                }
            )
            continue
        verified = verify_capture_receipt(receipt)
        latest = _latest_receipt(receipts, event_id)
        entry: dict[str, Any] = {
            "official_event_id": event_id,
            "status": receipt.get("status"),
            "blockers": list(receipt.get("blockers") or []),
            "capture_sha256": receipt.get("capture_sha256"),
            "capture_verified": verified["status"] == "VERIFIED_RESEARCH_CAPTURE",
            "deduplicated": bool(
                latest is not None
                and latest.get("capture_sha256") == receipt.get("capture_sha256")
            ),
            "artifact": None,
        }
        if artifact_root is not None:
            try:
                entry["artifact"] = str(write_capture_artifact(receipt, artifact_root))
            except ValueError:
                entry["blockers"] = sorted(
                    set(entry["blockers"]) | {"CAPTURE_ARTIFACT_WRITE_FAILED"}
                )
        captures.append(entry)
        new_receipts.append(receipt)

    retention = select_retained_receipts(list(receipts) + new_receipts)
    return {
        "status": "CYCLE_COMPLETE",
        "sport": key,
        "generated_at_utc": captured_at.isoformat(),
        "discovery": discovery,
        "plan": plan,
        "captures": captures,
        "receipts": new_receipts,
        "captures_attempted": len(captures),
        "captures_evidence_complete": sum(
            1 for entry in captures if entry["status"] == "CAPTURED_PREGAME_EVIDENCE_COMPLETE"
        ),
        "captures_evidence_insufficient": sum(
            1 for entry in captures if entry["status"] == "CAPTURED_PREGAME_EVIDENCE_INSUFFICIENT"
        ),
        "captures_candidates_only": sum(
            1 for entry in captures if entry["status"] == "CAPTURED_GOALIE_CANDIDATES_ONLY"
        ),
        "starter_inference_allowed": False,
        "retention": retention,
        "blockers": sorted(
            {blocker for entry in captures for blocker in entry["blockers"]}
        ),
        "research_only": RESEARCH_ONLY,
        "fitted_model": False,
        "calibration_status": "NOT_CALIBRATED_RESEARCH_ONLY",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "MAX_CAPTURES_PER_CYCLE",
    "MAX_RETAINED_RECEIPTS",
    "NBA_WINDOW",
    "NHL_WINDOW",
    "POLICY_VERSION",
    "CaptureWindow",
    "discover_pregame_events",
    "plan_pregame_capture_cycle",
    "run_pregame_capture_cycle",
    "select_retained_receipts",
    "window_for",
]
