"""Cross-sport outright-winner discovery, routing and reconciliation.

The defect this module exists to remove: a full-model moneyline scan that
iterates the *model registry* and discovers events per registered sport produces
an MLB-only board the moment MLB is the only healthy bridge — and the board
looks complete, because nothing ever counted the rows that were never looked
for.

The two questions are separate and are asked in this order:

    DISCOVERY        "What eligible winner events exist?"
    MODEL REGISTRY   "Do we have a real fitted model for this event?"

So discovery walks the full supported universe first, and the registry is
consulted per discovered row afterwards. A sport with no model does not shrink
the board: its rows stay visible with a typed ``MODEL_UNAVAILABLE`` and
``rank_eligible=false``. Every discovered row terminates in exactly one bucket,
and the run reconciles or is invalid.

This module never creates a probability. It never substitutes a sportsbook
price, a public projection or generic reasoning for an absent fitted model, and
``can_execute`` is false everywhere.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from v17 import market_evidence_observability as observability
from v17.llp_governed_package_scoring import (
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_OUTPUT_INVALID,
    MODEL_SCORER_FAILED,
    MODEL_UNAVAILABLE,
)
from v17.team_event_capability_manifest import (
    EXPECTED_TEAM_EVENT_SPORTS,
    normalize_team_event_identity,
)

CAN_EXECUTE = False
OBJECTIVE = "OUTRIGHT_WINNER"
REQUIRED_EVENT_STATE = "PREGAME"

# The discovery universe is the declared catalog, not the registry. It is
# deliberately wider than model coverage: every sport LLP is allowed to surface
# must be *looked for*, whether or not anything can score it.
SUPPORTED_DISCOVERY_SPORTS: tuple[str, ...] = tuple(EXPECTED_TEAM_EVENT_SPORTS)

# Canonical sport -> provider sport key for the discovery feeds already in use.
DISCOVERY_SPORT_KEYS: dict[str, tuple[str, ...]] = {
    "MLB": ("baseball_mlb",),
    "NFL": ("americanfootball_nfl",),
    "NCAAF": ("americanfootball_ncaaf",),
    "NBA": ("basketball_nba",),
    "WNBA": ("basketball_wnba",),
    "NCAAB": ("basketball_ncaab",),
    "NHL": ("icehockey_nhl",),
    "SOCCER": ("soccer_epl",),
    "TENNIS": ("tennis_atp", "tennis_wta"),
    "PGA": ("golf_pga_championship_winner",),
    "MMA": ("mma_mixed_martial_arts",),
    "BOXING": ("boxing_boxing",),
}

# Terminal row buckets. Exactly one applies to every discovered row.
WRONG_DATE = "EVENT_WRONG_DATE"
STARTED_OR_FINAL = "EVENT_STARTED_OR_FINAL"
CANCELLED_OR_POSTPONED = "EVENT_CANCELLED_OR_POSTPONED"
IDENTITY_UNRESOLVED = "EVENT_IDENTITY_UNRESOLVED"
MODEL_COMPLETED = "MODEL_COMPLETED"
OTHER_GOVERNED_HOLD = "OTHER_GOVERNED_HOLD"

PURGE_BUCKETS = (WRONG_DATE, STARTED_OR_FINAL, CANCELLED_OR_POSTPONED, IDENTITY_UNRESOLVED)
MODEL_BUCKETS = (
    MODEL_COMPLETED,
    MODEL_UNAVAILABLE,
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_SCORER_FAILED,
    MODEL_OUTPUT_INVALID,
    OTHER_GOVERNED_HOLD,
)
ALL_BUCKETS = (*PURGE_BUCKETS, *MODEL_BUCKETS)

_CANCELLED_TOKENS = ("CANCEL", "POSTPON", "SUSPEND", "ABANDON", "WALKOVER", "VOID")
_COMPLETE_TOKENS = ("FINAL", "COMPLETE", "ENDED", "CLOSED", "RETIRED")
_LIVE_TOKENS = ("LIVE", "IN_PROGRESS", "INPROGRESS", "STARTED", "PLAYING", "HALFTIME", "DELAY")
_PREGAME_TOKENS = ("PREGAME", "SCHEDULED", "NOT_STARTED", "NOTSTARTED", "UPCOMING", "STATUS_SCHEDULED")


@dataclass(frozen=True)
class DiscoveredEvent:
    """One discovered winner candidate, before any model question is asked."""

    sport: str
    league: str
    sport_key: str
    official_event_id: str | None
    home_team: str | None
    away_team: str | None
    commence_time_utc: str | None
    event_status: str
    source: str
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def event_key(self) -> str:
        return f"{self.sport}:{self.official_event_id or 'UNRESOLVED'}"

    def identity(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "league": self.league,
            "sport_key": self.sport_key,
            "event_key": self.event_key,
            "official_event_id": self.official_event_id,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "commence_time_utc": self.commence_time_utc,
            "event_status": self.event_status,
            "discovery_source": self.source,
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_instant(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def classify_event_status(raw_status: Any, commence_time: Any, *, now: datetime | None = None) -> str:
    """Normalise a provider status into the governed pregame vocabulary.

    A missing status is resolved from start time, not assumed pregame: a row
    whose start time has passed is treated as started even when the feed still
    labels it scheduled, because a pregame probability must never be reused as a
    live one.
    """
    token = _text(raw_status).upper().replace(" ", "_").replace("-", "_")
    if any(needle in token for needle in _CANCELLED_TOKENS):
        return "CANCELLED_OR_POSTPONED"
    if any(needle in token for needle in _COMPLETE_TOKENS):
        return "FINAL"
    if any(needle in token for needle in _LIVE_TOKENS):
        return "LIVE"

    start = _parse_instant(commence_time)
    reference = now or datetime.now(timezone.utc)
    if start is not None and start <= reference:
        return "STARTED"
    if any(needle in token for needle in _PREGAME_TOKENS) or not token:
        return REQUIRED_EVENT_STATE if start is not None else "UNKNOWN"
    return "UNKNOWN"


def _slate_date_matches(commence_time: Any, slate_date: str, tz_name: str) -> bool:
    start = _parse_instant(commence_time)
    if start is None:
        return False
    try:
        local = start.astimezone(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001 - an unusable zone must not purge the board
        local = start.astimezone(timezone.utc)
    return local.date().isoformat() == str(slate_date)


def normalize_discovered_event(
    raw: Mapping[str, Any],
    *,
    sport: str,
    sport_key: str,
    source: str,
    now: datetime | None = None,
) -> DiscoveredEvent:
    """Translate one provider event row into a canonical discovery candidate."""
    league = _text(raw.get("league") or raw.get("league_name") or raw.get("sport_title")) or sport
    commence = (
        raw.get("commence_time")
        or raw.get("event_date")
        or raw.get("start_time")
        or raw.get("event_start_time")
        or raw.get("event_start_time_utc")
    )
    event_id = raw.get("id") or raw.get("event_id") or raw.get("official_event_id") or raw.get("event_uuid")
    return DiscoveredEvent(
        sport=normalize_team_event_identity(sport, league),
        league=league.upper(),
        sport_key=sport_key,
        official_event_id=_text(event_id) or None,
        home_team=_text(raw.get("home_team")) or None,
        away_team=_text(raw.get("away_team")) or None,
        commence_time_utc=_text(commence) or None,
        event_status=classify_event_status(
            raw.get("status") or raw.get("event_status") or raw.get("score_status"),
            commence,
            now=now,
        ),
        source=source,
        raw=dict(raw),
    )


@dataclass
class DiscoveryInventory:
    """What the board *contains*, established before any model is consulted."""

    requested_slate_date: str
    requested_timezone: str
    sports_queried: list[str] = field(default_factory=list)
    sports_with_events: list[str] = field(default_factory=list)
    events: list[DiscoveredEvent] = field(default_factory=list)
    source_blockers: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": OBJECTIVE,
            "requested_slate_date": self.requested_slate_date,
            "requested_timezone": self.requested_timezone,
            "sports_queried": list(self.sports_queried),
            "sports_with_events": list(self.sports_with_events),
            "events_discovered": len(self.events),
            "source_blockers": list(self.source_blockers),
            "discovery_independent_of_model_registry": True,
            "can_execute": False,
        }


def discovery_budget_seconds() -> float:
    """Wall-clock ceiling for one discovery sweep across every supported sport.

    Broad discovery means one feed call per sport, so an unreachable or slow
    source must degrade into typed blockers rather than hold a request open for
    the sum of every timeout. Exhausting the budget is evidence, not silence:
    the sports that were not reached are recorded as blockers and the board
    still reconciles.
    """
    try:
        configured = float(os.environ.get("WOW_CROSS_SPORT_DISCOVERY_BUDGET_SECONDS", "8"))
    except ValueError:
        configured = 8.0
    return max(0.5, min(configured, 120.0))


def discover_winner_slate(
    *,
    requested_slate_date: str,
    requested_timezone: str,
    fetch_sport_events: Callable[[str, str], Iterable[Mapping[str, Any]]],
    supported_sports: Iterable[str] = SUPPORTED_DISCOVERY_SPORTS,
    sport_keys: Mapping[str, tuple[str, ...]] | None = None,
    now: datetime | None = None,
    budget_seconds: float | None = None,
) -> DiscoveryInventory:
    """Walk the full supported universe, independent of registered model coverage.

    ``fetch_sport_events(sport, sport_key)`` is injected so discovery is not
    bound to one feed. A sport that raises or returns nothing is recorded as a
    source blocker — never silently dropped, because "we could not look" and
    "there was nothing there" are different answers.
    """
    keys = dict(sport_keys or DISCOVERY_SPORT_KEYS)
    inventory = DiscoveryInventory(
        requested_slate_date=requested_slate_date,
        requested_timezone=requested_timezone,
    )
    budget = discovery_budget_seconds() if budget_seconds is None else float(budget_seconds)
    deadline = monotonic() + budget
    for sport in supported_sports:
        inventory.sports_queried.append(sport)
        found = 0
        if monotonic() >= deadline:
            inventory.source_blockers.append(
                {
                    "scope": "sport",
                    "sport": sport,
                    "sport_key": None,
                    "status": "DISCOVERY_BUDGET_EXHAUSTED",
                    "budget_seconds": budget,
                }
            )
            continue
        for sport_key in keys.get(sport, ()):  # a sport with no feed key is still queried
            try:
                rows = list(fetch_sport_events(sport, sport_key) or ())
            except Exception as exc:  # noqa: BLE001 - a feed defect is evidence
                inventory.source_blockers.append(
                    {
                        "scope": "sport",
                        "sport": sport,
                        "sport_key": sport_key,
                        "status": "EVENT_DISCOVERY_FAILED",
                        "error_type": type(exc).__name__,
                    }
                )
                continue
            for raw in rows:
                if not isinstance(raw, Mapping):
                    continue
                inventory.events.append(
                    normalize_discovered_event(
                        raw, sport=sport, sport_key=sport_key, source="DISCOVERY_FEED", now=now
                    )
                )
                found += 1
        if not keys.get(sport):
            inventory.source_blockers.append(
                {
                    "scope": "sport",
                    "sport": sport,
                    "sport_key": None,
                    "status": "NO_CONFIGURED_DISCOVERY_FEED",
                }
            )
        if found:
            inventory.sports_with_events.append(sport)
    return inventory


@dataclass
class RoutedRow:
    """One discovered row's single terminal outcome."""

    identity: dict[str, Any]
    bucket: str
    model_status: str | None
    detail: dict[str, Any]
    rank_eligible: bool = False
    probability_publishable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.identity,
            "objective": OBJECTIVE,
            "bucket": self.bucket,
            "model_status": self.model_status,
            "rank_eligible": self.rank_eligible,
            "probability_publishable": self.probability_publishable,
            "detail": self.detail,
            "market_probability_substitution_allowed": False,
            "generic_reasoning_substitution_allowed": False,
            "can_execute": False,
        }


def _purge_bucket(event: DiscoveredEvent, *, slate_date: str, tz_name: str) -> str | None:
    if not event.official_event_id or not event.home_team or not event.away_team:
        return IDENTITY_UNRESOLVED
    if event.event_status == "CANCELLED_OR_POSTPONED":
        return CANCELLED_OR_POSTPONED
    if event.event_status in {"LIVE", "STARTED", "FINAL"}:
        return STARTED_OR_FINAL
    if event.event_status != REQUIRED_EVENT_STATE:
        return IDENTITY_UNRESOLVED
    if not _slate_date_matches(event.commence_time_utc, slate_date, tz_name):
        return WRONG_DATE
    return None


def _model_bucket(code: Any) -> str:
    token = _text(code).upper()
    if token in {MODEL_UNAVAILABLE, MODEL_INPUTS_INSUFFICIENT, MODEL_SCORER_FAILED, MODEL_OUTPUT_INVALID}:
        return token
    return OTHER_GOVERNED_HOLD


def route_discovered_slate(
    inventory: DiscoveryInventory,
    *,
    resolve_model: Callable[[DiscoveredEvent], Any],
    score_row: Callable[[DiscoveredEvent, Any], Mapping[str, Any]],
    now: datetime | None = None,
) -> list[RoutedRow]:
    """Route every discovered row to exactly one terminal outcome.

    ``resolve_model`` returns the registered bridge for a row or ``None``. A
    ``None`` never removes the row: it produces a retained ``MODEL_UNAVAILABLE``
    candidate. A model that *was* selected and then failed keeps its own typed
    status — a scorer failure is not a missing model.
    """
    rows: list[RoutedRow] = []
    for event in inventory.events:
        purge = _purge_bucket(
            event, slate_date=inventory.requested_slate_date, tz_name=inventory.requested_timezone
        )
        if purge is not None:
            rows.append(
                RoutedRow(
                    identity=event.identity(),
                    bucket=purge,
                    model_status=None,
                    detail={"reason": purge, "model_invoked": False},
                )
            )
            continue

        try:
            model = resolve_model(event)
        except Exception as exc:  # noqa: BLE001 - resolution defects fail closed
            rows.append(
                RoutedRow(
                    identity=event.identity(),
                    bucket=MODEL_UNAVAILABLE,
                    model_status=MODEL_UNAVAILABLE,
                    detail={
                        "reason": "MODEL_RESOLUTION_FAILED",
                        "error_type": type(exc).__name__,
                        "model_invoked": False,
                    },
                )
            )
            continue

        if model is None:
            # Retained, not removed. This row is exactly what the board is
            # supposed to show when a sport has no fitted model.
            rows.append(
                RoutedRow(
                    identity=event.identity(),
                    bucket=MODEL_UNAVAILABLE,
                    model_status=MODEL_UNAVAILABLE,
                    detail={
                        "reason": f"{event.sport}_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE",
                        "backend_route_status": "SPORT_SPECIFIC_TEAM_EVENT_ADAPTER_NOT_REGISTERED",
                        "model_invoked": False,
                    },
                )
            )
            continue

        result = score_row(event, model)
        payload = dict(result or {})
        code = payload.get("code") or payload.get("model_status") or payload.get("terminal_status")
        completed = (
            payload.get("probability_publishable") is True and payload.get("rank_eligible") is True
        )
        if completed:
            rows.append(
                RoutedRow(
                    identity=event.identity(),
                    bucket=MODEL_COMPLETED,
                    model_status=_text(code) or "SPORTING_PROBABILITY_COMPLETED",
                    detail=payload,
                    rank_eligible=True,
                    probability_publishable=True,
                )
            )
            continue
        bucket = _model_bucket(code)
        rows.append(
            RoutedRow(
                identity=event.identity(),
                bucket=bucket,
                model_status=_text(code) or bucket,
                detail=payload,
            )
        )
    return rows


def reconcile(inventory: DiscoveryInventory, rows: list[RoutedRow]) -> dict[str, Any]:
    """Cross-sport discovery audit. Every discovered row lands in one bucket."""
    counts = {bucket: 0 for bucket in ALL_BUCKETS}
    for row in rows:
        counts[row.bucket] = counts.get(row.bucket, 0) + 1
    discovered = len(inventory.events)
    accounted = sum(counts.values())
    model_supported = counts[MODEL_COMPLETED]
    return {
        "audit": "CROSS_SPORT_DISCOVERY_AUDIT",
        "objective": OBJECTIVE,
        "requested_slate_date": inventory.requested_slate_date,
        "sports_queried": len(inventory.sports_queried),
        "sports_with_events": len(inventory.sports_with_events),
        "events_discovered": discovered,
        "events_accounted": accounted,
        "buckets": counts,
        "model_supported_rows": model_supported,
        "retained_unsupported_rows": counts[MODEL_UNAVAILABLE],
        "row_reconciliation": "PASS" if accounted == discovered else "FAIL",
        "run_status": "COMPLETED" if accounted == discovered else "RUN_INVALID_ROW_RECONCILIATION",
        "source_blockers": list(inventory.source_blockers),
        "discovery_independent_of_model_registry": True,
        "can_execute": False,
    }


def run_cross_sport_winner_scan(
    *,
    requested_slate_date: str,
    requested_timezone: str,
    fetch_sport_events: Callable[[str, str], Iterable[Mapping[str, Any]]],
    resolve_model: Callable[[DiscoveredEvent], Any],
    score_row: Callable[[DiscoveredEvent, Any], Mapping[str, Any]],
    supported_sports: Iterable[str] = SUPPORTED_DISCOVERY_SPORTS,
    sport_keys: Mapping[str, tuple[str, ...]] | None = None,
    now: datetime | None = None,
    budget_seconds: float | None = None,
) -> dict[str, Any]:
    """Discovery -> registry routing -> reconciliation, in that order."""
    counters_before = observability.counters()
    inventory = discover_winner_slate(
        requested_slate_date=requested_slate_date,
        requested_timezone=requested_timezone,
        fetch_sport_events=fetch_sport_events,
        supported_sports=supported_sports,
        sport_keys=sport_keys,
        now=now,
        budget_seconds=budget_seconds,
    )
    rows = route_discovered_slate(
        inventory, resolve_model=resolve_model, score_row=score_row, now=now
    )
    return {
        "discovery": inventory.as_dict(),
        "rows": [row.as_dict() for row in rows],
        "reconciliation": reconcile(inventory, rows),
        # Acquisition counters attributable to this run, so a board with missing
        # rows can be read against what the provider lane actually did.
        "market_evidence_counters": observability.run_delta(counters_before),
        "can_execute": False,
    }


__all__ = [
    "ALL_BUCKETS",
    "CANCELLED_OR_POSTPONED",
    "CAN_EXECUTE",
    "DISCOVERY_SPORT_KEYS",
    "DiscoveredEvent",
    "DiscoveryInventory",
    "IDENTITY_UNRESOLVED",
    "MODEL_BUCKETS",
    "MODEL_COMPLETED",
    "OBJECTIVE",
    "OTHER_GOVERNED_HOLD",
    "PURGE_BUCKETS",
    "REQUIRED_EVENT_STATE",
    "RoutedRow",
    "STARTED_OR_FINAL",
    "SUPPORTED_DISCOVERY_SPORTS",
    "WRONG_DATE",
    "classify_event_status",
    "discover_winner_slate",
    "discovery_budget_seconds",
    "normalize_discovered_event",
    "reconcile",
    "route_discovered_slate",
    "run_cross_sport_winner_scan",
]
