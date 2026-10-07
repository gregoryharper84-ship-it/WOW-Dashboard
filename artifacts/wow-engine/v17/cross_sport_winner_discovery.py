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
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from v17 import market_evidence_observability as observability
from v17 import rundown_sport_registry as registry
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

# Per-family acquisition outcomes. These describe what happened when we tried to
# *look*, and are deliberately separate from what a model could do afterwards.
EVENTS_RETURNED = "EVENTS_RETURNED"
NO_EVENTS_RETURNED = "NO_EVENTS_RETURNED"
NO_CONFIGURED_DISCOVERY_FEED = "NO_CONFIGURED_DISCOVERY_FEED"
PROVIDER_REQUEST_FAILED = "PROVIDER_REQUEST_FAILED"
PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
PROVIDER_SCHEMA_FAILURE = "PROVIDER_SCHEMA_FAILURE"
DISCOVERY_BUDGET_EXHAUSTED = "DISCOVERY_BUDGET_EXHAUSTED"

ACQUISITION_STATUSES = (
    EVENTS_RETURNED,
    NO_EVENTS_RETURNED,
    NO_CONFIGURED_DISCOVERY_FEED,
    PROVIDER_REQUEST_FAILED,
    PROVIDER_RATE_LIMITED,
    PROVIDER_SCHEMA_FAILURE,
    DISCOVERY_BUDGET_EXHAUSTED,
)

PROVIDER_SUCCEEDED = "PROVIDER_SUCCEEDED"
PROVIDER_FAILED = "PROVIDER_FAILED"
PROVIDER_NOT_ATTEMPTED = "PROVIDER_NOT_ATTEMPTED"
FALLBACK_NOT_REPORTED = "FALLBACK_NOT_REPORTED_BY_COMPOSITE_FEED"
FALLBACK_NOT_APPLICABLE = "FALLBACK_NOT_APPLICABLE"
FALLBACK_NOT_ATTEMPTED = "FALLBACK_NOT_ATTEMPTED"
FALLBACK_SUCCEEDED = "FALLBACK_SUCCEEDED"
FALLBACK_FAILED = "FALLBACK_FAILED"
PATHS_NOT_EXHAUSTED = "PATHS_NOT_EXHAUSTED"
PROVIDER_PATHS_EXHAUSTED = "PROVIDER_PATHS_EXHAUSTED"
NO_CONFIGURED_PATH = "NO_CONFIGURED_PATH"

PATH_ESPN_SCOREBOARD = "ESPN_SCOREBOARD"
PATH_ODDS_PROXY = "ODDS_PROXY"
PATH_RUNDOWN = "RUNDOWN"
PATH_GOVERNED_FALLBACK_UNION = "GOVERNED_FALLBACK_UNION"
ACQUISITION_PATH_IDS = (
    PATH_ESPN_SCOREBOARD,
    PATH_ODDS_PROXY,
    PATH_RUNDOWN,
    PATH_GOVERNED_FALLBACK_UNION,
)

PATH_SUCCEEDED_EMPTY = "SUCCEEDED_EMPTY"
PATH_SUCCEEDED_WITH_ROWS = "SUCCEEDED_WITH_ROWS"
PATH_FAILED = "FAILED_TYPED"
PATH_NOT_ATTEMPTED = "NOT_ATTEMPTED"
PATH_NOT_APPLICABLE = "NOT_APPLICABLE"
PATH_CIRCUIT_OPEN_PRIOR_FAILURE = "CIRCUIT_OPEN_FROM_PRIOR_TYPED_FAILURE"
ACQUISITION_PATH_STATES = (
    PATH_SUCCEEDED_EMPTY,
    PATH_SUCCEEDED_WITH_ROWS,
    PATH_FAILED,
    PATH_NOT_ATTEMPTED,
    PATH_NOT_APPLICABLE,
    PATH_CIRCUIT_OPEN_PRIOR_FAILURE,
)

MAX_ACQUISITION_ATTEMPTS = 3
ACQUISITION_ATTEMPT_PATH_IDS = (
    PATH_ESPN_SCOREBOARD,
    PATH_ODDS_PROXY,
    PATH_RUNDOWN,
)
ACQUISITION_ATTEMPT_CONTENT_TYPE_CLASSES = (
    "JSON",
    "TEXT_HTML",
    "TEXT_PLAIN",
    "OTHER",
    "EMPTY",
)
ACQUISITION_ATTEMPT_CREDENTIAL_ALIASES = (
    "ODDS_API_PAID_KEY",
    "ODDS_API_KEY_100K",
    "ODDS_API_FREE_KEY",
    "ODDS_API_KEY",
)


def typed_blocker_code(value: Any) -> str | None:
    """Return one bounded typed token, never raw provider text.

    Provider circuit codes use ``TYPE:ORIGIN``.  The caller records the origin
    separately, so this function intentionally retains only the typed prefix.
    """
    if value is None:
        return None
    token = str(value).strip().split(":", 1)[0].upper()
    sanitized = "".join(
        char if char.isalnum() or char in {"_", "-", "."} else "_"
        for char in token
    )
    return sanitized[:96] or None


def circuit_originating_blocker(value: Any) -> str | None:
    """Extract only the typed originating code from a circuit marker."""
    text = str(value or "").strip()
    if "CIRCUIT_OPEN:" not in text.upper():
        return None
    return typed_blocker_code(text.split(":", 1)[1])


@dataclass(frozen=True)
class AcquisitionAttempt:
    """One actual acquisition-layer attempt in immutable execution order."""

    ordinal: int
    path_id: str
    path_state: str
    blocker_code: str | None = None
    originating_blocker_code: str | None = None
    upstream_status: int | None = None
    content_type_class: str | None = None
    credential_alias: str | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.ordinal, bool)
            or not isinstance(self.ordinal, int)
            or not 1 <= self.ordinal <= MAX_ACQUISITION_ATTEMPTS
        ):
            raise ValueError("ACQUISITION_ATTEMPT_ORDINAL_INVALID")
        if self.path_id not in ACQUISITION_ATTEMPT_PATH_IDS:
            raise ValueError("ACQUISITION_ATTEMPT_PATH_INVALID")
        if self.path_state not in ACQUISITION_PATH_STATES:
            raise ValueError("ACQUISITION_ATTEMPT_STATE_INVALID")
        for code in (self.blocker_code, self.originating_blocker_code):
            if code is not None and typed_blocker_code(code) != code:
                raise ValueError("ACQUISITION_ATTEMPT_CODE_INVALID")
        if self.path_state == PATH_CIRCUIT_OPEN_PRIOR_FAILURE:
            if not self.originating_blocker_code:
                raise ValueError("ACQUISITION_ATTEMPT_CIRCUIT_ORIGIN_REQUIRED")
        elif self.originating_blocker_code is not None:
            raise ValueError("ACQUISITION_ATTEMPT_CIRCUIT_ORIGIN_NOT_APPLICABLE")
        if self.upstream_status is not None and (
            isinstance(self.upstream_status, bool)
            or not isinstance(self.upstream_status, int)
            or not 100 <= self.upstream_status <= 599
        ):
            raise ValueError("ACQUISITION_ATTEMPT_HTTP_STATUS_INVALID")
        if (
            self.content_type_class is not None
            and self.content_type_class not in ACQUISITION_ATTEMPT_CONTENT_TYPE_CLASSES
        ):
            raise ValueError("ACQUISITION_ATTEMPT_CONTENT_TYPE_INVALID")
        if (
            self.credential_alias is not None
            and self.credential_alias not in ACQUISITION_ATTEMPT_CREDENTIAL_ALIASES
        ):
            raise ValueError("ACQUISITION_ATTEMPT_CREDENTIAL_ALIAS_INVALID")

    def as_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "path_id": self.path_id,
            "path_state": self.path_state,
            "blocker_code": self.blocker_code,
            "originating_blocker_code": self.originating_blocker_code,
            "upstream_status": self.upstream_status,
            "content_type_class": self.content_type_class,
            "credential_alias": self.credential_alias,
        }


def acquisition_attempt(
    *,
    path_id: str,
    path_state: str,
    blocker_code: Any = None,
    originating_blocker_code: Any = None,
    upstream_status: int | None = None,
    content_type_class: str | None = None,
    credential_alias: str | None = None,
) -> AcquisitionAttempt:
    """Create one sanitized attempt owned by the layer that actually ran."""
    return AcquisitionAttempt(
        ordinal=1,
        path_id=path_id,
        path_state=path_state,
        blocker_code=typed_blocker_code(blocker_code),
        originating_blocker_code=typed_blocker_code(originating_blocker_code),
        upstream_status=upstream_status,
        content_type_class=content_type_class,
        credential_alias=credential_alias,
    )


def ordered_acquisition_attempts(
    *attempt_groups: Sequence[AcquisitionAttempt],
) -> tuple[AcquisitionAttempt, ...]:
    """Combine actual-layer attempts and assign immutable contiguous ordinals."""
    flattened = [attempt for group in attempt_groups for attempt in tuple(group)]
    if len(flattened) > MAX_ACQUISITION_ATTEMPTS:
        raise ValueError("ACQUISITION_ATTEMPTS_BOUND_EXCEEDED")
    path_ids = [attempt.path_id for attempt in flattened]
    if len(path_ids) != len(set(path_ids)):
        raise ValueError("ACQUISITION_ATTEMPT_PATH_DUPLICATED")
    ranks = {path_id: index for index, path_id in enumerate(ACQUISITION_ATTEMPT_PATH_IDS)}
    if path_ids != sorted(path_ids, key=ranks.__getitem__):
        raise ValueError("ACQUISITION_ATTEMPT_ORDER_INVALID")
    return tuple(
        AcquisitionAttempt(
            ordinal=index,
            path_id=attempt.path_id,
            path_state=attempt.path_state,
            blocker_code=attempt.blocker_code,
            originating_blocker_code=attempt.originating_blocker_code,
            upstream_status=attempt.upstream_status,
            content_type_class=attempt.content_type_class,
            credential_alias=attempt.credential_alias,
        )
        for index, attempt in enumerate(flattened, start=1)
    )


def succeeded_path_state(rows: Iterable[Any]) -> str:
    """Distinguish a healthy empty answer from a healthy answer with rows."""
    return PATH_SUCCEEDED_WITH_ROWS if bool(rows) else PATH_SUCCEEDED_EMPTY


@dataclass(frozen=True)
class AcquisitionFeedResult:
    """Rows plus sanitized path-level acquisition provenance.

    It behaves as a sequence for compatibility with existing discovery callers,
    while preserving whether the primary and governed fallback paths actually
    succeeded. No provider payload or numeric authority is carried here.
    """

    rows: tuple[Mapping[str, Any], ...]
    provider_status: str
    fallback_status: str
    exhaustion_status: str
    blocker_code: str | None = None
    primary_blocker_code: str | None = None
    fallback_blocker_code: str | None = None
    primary_path_id: str | None = None
    primary_path_state: str | None = None
    fallback_path_id: str | None = None
    fallback_path_state: str | None = None
    primary_upstream_status: int | None = None
    primary_content_type_class: str | None = None
    primary_provider_alias: str | None = None
    fallback_upstream_status: int | None = None
    fallback_content_type_class: str | None = None
    fallback_provider_alias: str | None = None
    attempts: tuple[AcquisitionAttempt, ...] = ()

    def __post_init__(self) -> None:
        attempts = ordered_acquisition_attempts(self.attempts)
        object.__setattr__(self, "attempts", attempts)
        if not attempts:
            return
        primary = attempts[0]
        fallback = attempts[-1] if len(attempts) > 1 else None
        object.__setattr__(self, "primary_path_id", primary.path_id)
        object.__setattr__(self, "primary_path_state", primary.path_state)
        object.__setattr__(
            self,
            "primary_blocker_code",
            primary.originating_blocker_code or primary.blocker_code,
        )
        object.__setattr__(self, "primary_upstream_status", primary.upstream_status)
        object.__setattr__(self, "primary_content_type_class", primary.content_type_class)
        object.__setattr__(self, "primary_provider_alias", primary.credential_alias)
        # A one-attempt result can be wrapping a legacy downstream adapter that
        # has not yet emitted ordered evidence. Preserve its explicitly supplied
        # compatibility fallback scalars; persistence will fail closed rather
        # than claiming the partial attempt list is complete. Two/three-attempt
        # results are fully projectable from ordered truth.
        if fallback is not None:
            object.__setattr__(self, "fallback_path_id", fallback.path_id)
            object.__setattr__(self, "fallback_path_state", fallback.path_state)
            object.__setattr__(
                self,
                "fallback_blocker_code",
                fallback.originating_blocker_code or fallback.blocker_code,
            )
            object.__setattr__(self, "fallback_upstream_status", fallback.upstream_status)
            object.__setattr__(
                self, "fallback_content_type_class", fallback.content_type_class
            )
            object.__setattr__(self, "fallback_provider_alias", fallback.credential_alias)

    def __iter__(self):
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int):
        return self.rows[index]

    def detail_fields(self) -> dict[str, Any]:
        """Return only typed observability fields for target-level persistence."""
        return {
            "primary_path_id": self.primary_path_id,
            "primary_path_state": self.primary_path_state,
            "primary_blocker_code": self.primary_blocker_code,
            "fallback_path_id": self.fallback_path_id,
            "fallback_path_state": self.fallback_path_state,
            "fallback_blocker_code": self.fallback_blocker_code,
            "primary_upstream_status": self.primary_upstream_status,
            "primary_content_type_class": self.primary_content_type_class,
            "primary_provider_alias": self.primary_provider_alias,
            "fallback_upstream_status": self.fallback_upstream_status,
            "fallback_content_type_class": self.fallback_content_type_class,
            "fallback_provider_alias": self.fallback_provider_alias,
            "attempts": [attempt.as_dict() for attempt in self.attempts],
        }


class DiscoveryFeedError(RuntimeError):
    """A feed failure that carries the provider's own typed reason code."""

    def __init__(self, code: str, *, acquisition: AcquisitionFeedResult | None = None):
        super().__init__(str(code))
        self.code = str(code)
        self.acquisition = acquisition


def acquisition_observability_fields(
    acquisition: AcquisitionFeedResult | None,
    *,
    default_primary_state: str,
) -> dict[str, Any]:
    """Normalize optional feed provenance into the durable detail shape."""
    if acquisition is not None:
        return acquisition.detail_fields()
    return {
        "primary_path_id": None,
        "primary_path_state": default_primary_state,
        "primary_blocker_code": None,
        "fallback_path_id": None,
        "fallback_path_state": PATH_NOT_APPLICABLE,
        "fallback_blocker_code": None,
        "primary_upstream_status": None,
        "primary_content_type_class": None,
        "primary_provider_alias": None,
        "fallback_upstream_status": None,
        "fallback_content_type_class": None,
        "fallback_provider_alias": None,
    }


def classify_acquisition_failure(code: Any) -> str:
    """Map a provider reason code onto its acquisition status.

    Rate limiting and a schema/contract failure are distinguishable from a plain
    request failure, because they call for different operator action.
    """
    token = str(code or "").upper()
    if "429" in token or "THROTTLED" in token or "QUOTA_EXHAUSTED" in token or "RATE_LIMIT" in token:
        return PROVIDER_RATE_LIMITED
    if (
        "SCHEMA_UNRECOGNISED" in token
        or "SCHEMA_UNRECOGNIZED" in token
        or "MARKET_CATALOG" in token
        or "WITHOUT_PRICES" in token
        or "INVALID_JSON" in token
    ):
        return PROVIDER_SCHEMA_FAILURE
    return PROVIDER_REQUEST_FAILED


@dataclass(frozen=True)
class DiscoveryTarget:
    """One concrete thing to query: a provider, a sport id, a league, a regime.

    Provider sport ids are verified registry values, never names guessed from a
    family. A family with no targets has no configured feed — which is a
    different answer from a query that returned nothing.
    """

    family: str
    provider: str
    league: str
    regime: str = registry.REGULAR_SEASON
    sport_id: int | None = None
    sport_key: str | None = None

    @property
    def label(self) -> str:
        return str(self.sport_id) if self.sport_id is not None else str(self.sport_key or "")

    @property
    def target_key(self) -> str:
        """Stable non-secret identity for one configured acquisition target."""
        return "|".join(
            (
                str(self.provider or "").upper(),
                str(self.label),
                str(self.league or "").upper(),
                str(self.regime or "").upper(),
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "provider": self.provider,
            "provider_sport_id": self.sport_id,
            "sport_key": self.sport_key,
            "league": self.league,
            "regime": self.regime,
            "target_key": self.target_key,
        }


def rundown_discovery_targets(
    family: str, *, include_regime_variants: bool = False
) -> tuple[DiscoveryTarget, ...]:
    """Verified TheRundown targets for one family, league and regime preserved.

    Soccer expands across every configured competition rather than collapsing to
    EPL, and each row keeps its exact competition. A family the provider registry
    does not carry (boxing today) yields no targets at all.
    """
    ids = registry.discovery_sport_ids(family, include_regime_variants=include_regime_variants)
    targets: list[DiscoveryTarget] = []
    for sport_id in ids:
        sport = registry.provider_sport(sport_id)
        if sport is None:
            continue
        targets.append(
            DiscoveryTarget(
                family=sport.family,
                provider=registry.PROVIDER,
                league=sport.league,
                regime=sport.regime,
                sport_id=sport.sport_id,
            )
        )
    return tuple(targets)


def default_discovery_targets(
    supported_sports: Iterable[str] = SUPPORTED_DISCOVERY_SPORTS,
    *,
    include_regime_variants: bool = False,
) -> dict[str, tuple[DiscoveryTarget, ...]]:
    return {
        family: rundown_discovery_targets(
            family, include_regime_variants=include_regime_variants
        )
        for family in supported_sports
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
    # Competition/season regime. Preserved from the provider target rather than
    # assumed: a preseason or playoff row must stay distinguishable from a
    # regular-season one all the way to model routing.
    regime: str = registry.REGULAR_SEASON
    provider: str | None = None
    provider_sport_id: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def event_key(self) -> str:
        return f"{self.sport}:{self.official_event_id or 'UNRESOLVED'}"

    def identity(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "league": self.league,
            "sport_key": self.sport_key,
            "regime": self.regime,
            "provider": self.provider,
            "provider_sport_id": self.provider_sport_id,
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


def _dedupe_identity(event: DiscoveredEvent) -> tuple[str, str, str]:
    """Identity for suppressing the same fixture seen under two provider ids."""
    if event.official_event_id:
        return ("ID", event.sport, str(event.official_event_id))
    return (
        "TEAMS",
        event.sport,
        "|".join(
            (
                _text(event.home_team).lower(),
                _text(event.away_team).lower(),
                _text(event.commence_time_utc)[:16],
            )
        ),
    )


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
    target: "DiscoveryTarget | None" = None,
) -> DiscoveredEvent:
    """Translate one provider event row into a canonical discovery candidate.

    The target's league and regime win over anything the row says about itself:
    the provider sport id is what was actually queried, so it is the authority on
    which competition and which season regime this row belongs to.
    """
    league = _text(raw.get("league") or raw.get("league_name") or raw.get("sport_title")) or sport
    if target is not None and target.league:
        league = target.league
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
        regime=target.regime if target is not None else registry.REGULAR_SEASON,
        provider=target.provider if target is not None else None,
        provider_sport_id=target.sport_id if target is not None else None,
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
    # One row per family describing what the acquisition attempt actually did.
    acquisition_audit: list[dict[str, Any]] = field(default_factory=list)
    # One bounded, sanitized terminal row per configured target. These rows are
    # persisted independently of event rows; they never contain provider
    # payloads, prices, probabilities, participant names, or other PII.
    acquisition_details: list[dict[str, Any]] = field(default_factory=list)
    # Independently derived from configured targets before any provider call.
    # The persistence boundary reconciles this set against generated and stored
    # detail identities; detail rows cannot declare their own completeness.
    expected_acquisition_targets: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": OBJECTIVE,
            "requested_slate_date": self.requested_slate_date,
            "requested_timezone": self.requested_timezone,
            "sports_queried": list(self.sports_queried),
            "sports_with_events": list(self.sports_with_events),
            "events_discovered": len(self.events),
            "source_blockers": list(self.source_blockers),
            "acquisition_audit": list(self.acquisition_audit),
            "acquisition_details_count": len(self.acquisition_details),
            "acquisition_targets_expected": len(self.expected_acquisition_targets),
            "discovery_independent_of_model_registry": True,
            "can_execute": False,
        }


def discovery_budget_seconds() -> float:
    """Wall-clock ceiling for one discovery sweep across every supported sport.

    Broad discovery means one feed call per configured target, so an unreachable
    or slow source must degrade into typed blockers rather than hold a request
    open for the sum of every timeout. Exhausting the budget is evidence, not
    silence: the families that were not reached are recorded and the board still
    reconciles.
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
    fetch_sport_events: Callable[..., Iterable[Mapping[str, Any]]],
    supported_sports: Iterable[str] = SUPPORTED_DISCOVERY_SPORTS,
    discovery_targets: Mapping[str, tuple[DiscoveryTarget, ...]] | None = None,
    include_regime_variants: bool = False,
    now: datetime | None = None,
    budget_seconds: float | None = None,
) -> DiscoveryInventory:
    """Walk the full supported universe, independent of registered model coverage.

    ``fetch_sport_events(family, target)`` is injected so discovery is not bound
    to one feed. Every family terminates with exactly one acquisition status:
    "we never had a feed", "the provider refused", and "the provider answered
    and there was nothing" are three different answers and are recorded as such.
    ``NO_EVENTS_RETURNED`` is only ever used after a configured query succeeded.
    """
    supported_sports = tuple(supported_sports)
    targets = dict(
        discovery_targets
        if discovery_targets is not None
        else default_discovery_targets(
            supported_sports, include_regime_variants=include_regime_variants
        )
    )
    inventory = DiscoveryInventory(
        requested_slate_date=requested_slate_date,
        requested_timezone=requested_timezone,
    )
    for family in supported_sports:
        family_targets = tuple(targets.get(family) or ())
        if family_targets:
            inventory.expected_acquisition_targets.extend(
                {"family": str(family).upper(), "target_key": target.target_key}
                for target in family_targets
            )
        else:
            inventory.expected_acquisition_targets.append(
                {
                    "family": str(family).upper(),
                    "target_key": NO_CONFIGURED_DISCOVERY_FEED,
                }
            )
    budget = discovery_budget_seconds() if budget_seconds is None else float(budget_seconds)
    deadline = monotonic() + budget

    for family in supported_sports:
        inventory.sports_queried.append(family)
        family_targets = tuple(targets.get(family) or ())
        attempted: list[Any] = []
        returned = 0
        succeeded = False
        failures: list[str] = []
        budget_exhausted = False

        if not family_targets:
            # Never queried, because nothing is configured to query. This must
            # not read as an empty slate.
            inventory.acquisition_audit.append(
                {
                    "family": family,
                    "provider": registry.PROVIDER,
                    "provider_sport_ids_attempted": [],
                    "request_status": NO_CONFIGURED_DISCOVERY_FEED,
                    "events_returned": 0,
                    "blocker_if_any": NO_CONFIGURED_DISCOVERY_FEED,
                    "can_execute": False,
                }
            )
            inventory.source_blockers.append(
                {
                    "scope": "family",
                    "sport": family,
                    "sport_key": None,
                    "status": NO_CONFIGURED_DISCOVERY_FEED,
                }
            )
            inventory.acquisition_details.append(
                {
                    "family": family,
                    "target_key": NO_CONFIGURED_DISCOVERY_FEED,
                    "provider": registry.PROVIDER,
                    "league": None,
                    "regime": None,
                    "provider_sport_id": None,
                    "sport_key": None,
                    "final_state": NO_CONFIGURED_DISCOVERY_FEED,
                    "provider_status": PROVIDER_NOT_ATTEMPTED,
                    "fallback_status": FALLBACK_NOT_APPLICABLE,
                    "exhaustion_status": NO_CONFIGURED_PATH,
                    "events_returned": 0,
                    "duplicate_rows_suppressed": 0,
                    "blocker_code": NO_CONFIGURED_DISCOVERY_FEED,
                    **acquisition_observability_fields(
                        None, default_primary_state=PATH_NOT_APPLICABLE
                    ),
                    "can_execute": False,
                }
            )
            continue

        if monotonic() >= deadline:
            budget_exhausted = True
            inventory.acquisition_audit.append(
                {
                    "family": family,
                    "provider": registry.PROVIDER,
                    "provider_sport_ids_attempted": [],
                    "request_status": DISCOVERY_BUDGET_EXHAUSTED,
                    "events_returned": 0,
                    "blocker_if_any": DISCOVERY_BUDGET_EXHAUSTED,
                    "budget_seconds": budget,
                    "can_execute": False,
                }
            )
            inventory.source_blockers.append(
                {
                    "scope": "family",
                    "sport": family,
                    "sport_key": None,
                    "status": DISCOVERY_BUDGET_EXHAUSTED,
                    "budget_seconds": budget,
                }
            )
            for target in family_targets:
                inventory.acquisition_details.append(
                    {
                        **target.as_dict(),
                        "final_state": DISCOVERY_BUDGET_EXHAUSTED,
                        "provider_status": PROVIDER_NOT_ATTEMPTED,
                        "fallback_status": FALLBACK_NOT_REPORTED,
                        "exhaustion_status": DISCOVERY_BUDGET_EXHAUSTED,
                        "events_returned": 0,
                        "duplicate_rows_suppressed": 0,
                        "blocker_code": DISCOVERY_BUDGET_EXHAUSTED,
                        **acquisition_observability_fields(
                            None, default_primary_state=PATH_NOT_ATTEMPTED
                        ),
                        "can_execute": False,
                    }
                )
            continue

        seen_identities: set[tuple[str, str, str]] = set()
        duplicates = 0
        for target in family_targets:
            if monotonic() >= deadline:
                budget_exhausted = True
                inventory.acquisition_details.append(
                    {
                        **target.as_dict(),
                        "final_state": DISCOVERY_BUDGET_EXHAUSTED,
                        "provider_status": PROVIDER_NOT_ATTEMPTED,
                        "fallback_status": FALLBACK_NOT_REPORTED,
                        "exhaustion_status": DISCOVERY_BUDGET_EXHAUSTED,
                        "events_returned": 0,
                        "duplicate_rows_suppressed": 0,
                        "blocker_code": DISCOVERY_BUDGET_EXHAUSTED,
                        **acquisition_observability_fields(
                            None, default_primary_state=PATH_NOT_ATTEMPTED
                        ),
                        "can_execute": False,
                    }
                )
                continue
            attempted.append(target.sport_id if target.sport_id is not None else target.sport_key)
            try:
                fetched = fetch_sport_events(family, target)
                if isinstance(fetched, AcquisitionFeedResult):
                    rows = list(fetched.rows)
                    provider_status = fetched.provider_status
                    fallback_status = fetched.fallback_status
                    exhaustion_status = fetched.exhaustion_status
                    blocker_code = fetched.blocker_code
                else:
                    rows = list(fetched or ())
                    provider_status = PROVIDER_SUCCEEDED
                    fallback_status = FALLBACK_NOT_APPLICABLE
                    exhaustion_status = PATHS_NOT_EXHAUSTED
                    blocker_code = None
            except DiscoveryFeedError as exc:
                failures.append(exc.code)
                failure_status = classify_acquisition_failure(exc.code)
                acquisition = exc.acquisition
                inventory.source_blockers.append(
                    {
                        "scope": "target",
                        "sport": family,
                        **target.as_dict(),
                        "status": failure_status,
                        "reason_code": exc.code,
                    }
                )
                inventory.acquisition_details.append(
                    {
                        **target.as_dict(),
                        "final_state": failure_status,
                        "provider_status": (
                            acquisition.provider_status
                            if acquisition is not None
                            else PROVIDER_FAILED
                        ),
                        "fallback_status": (
                            acquisition.fallback_status
                            if acquisition is not None
                            else FALLBACK_NOT_APPLICABLE
                        ),
                        "exhaustion_status": (
                            acquisition.exhaustion_status
                            if acquisition is not None
                            else PROVIDER_PATHS_EXHAUSTED
                        ),
                        "events_returned": 0,
                        "duplicate_rows_suppressed": 0,
                        "blocker_code": str(exc.code),
                        **acquisition_observability_fields(
                            acquisition, default_primary_state=PATH_FAILED
                        ),
                        "can_execute": False,
                    }
                )
                continue
            except Exception as exc:  # noqa: BLE001 - a feed defect is evidence
                failures.append(type(exc).__name__)
                inventory.source_blockers.append(
                    {
                        "scope": "target",
                        "sport": family,
                        **target.as_dict(),
                        "status": PROVIDER_REQUEST_FAILED,
                        "error_type": type(exc).__name__,
                    }
                )
                inventory.acquisition_details.append(
                    {
                        **target.as_dict(),
                        "final_state": PROVIDER_REQUEST_FAILED,
                        "provider_status": PROVIDER_FAILED,
                        "fallback_status": FALLBACK_NOT_REPORTED,
                        "exhaustion_status": PROVIDER_PATHS_EXHAUSTED,
                        "events_returned": 0,
                        "duplicate_rows_suppressed": 0,
                        "blocker_code": type(exc).__name__,
                        **acquisition_observability_fields(
                            None, default_primary_state=PATH_FAILED
                        ),
                        "can_execute": False,
                    }
                )
                continue

            succeeded = True
            target_returned = 0
            target_duplicates = 0
            for raw in rows:
                if not isinstance(raw, Mapping):
                    continue
                event = normalize_discovered_event(
                    raw,
                    sport=family,
                    sport_key=target.label,
                    source="DISCOVERY_FEED",
                    now=now,
                    target=target,
                )
                # A family is queried across several provider ids (twelve soccer
                # competitions; regular season alongside a regime variant). The
                # same fixture surfacing under two of them is one event, not two
                # — counting it twice would inflate the board and break
                # reconciliation against the real slate.
                identity = _dedupe_identity(event)
                if identity in seen_identities:
                    duplicates += 1
                    target_duplicates += 1
                    continue
                seen_identities.add(identity)
                inventory.events.append(event)
                returned += 1
                target_returned += 1

            inventory.acquisition_details.append(
                {
                    **target.as_dict(),
                    "final_state": EVENTS_RETURNED if target_returned else NO_EVENTS_RETURNED,
                    "provider_status": provider_status,
                    "fallback_status": fallback_status,
                    "exhaustion_status": exhaustion_status,
                    "events_returned": target_returned,
                    "duplicate_rows_suppressed": target_duplicates,
                    "blocker_code": blocker_code,
                    **acquisition_observability_fields(
                        fetched if isinstance(fetched, AcquisitionFeedResult) else None,
                        default_primary_state=succeeded_path_state(rows),
                    ),
                    "can_execute": False,
                }
            )

        if returned:
            status = EVENTS_RETURNED
            blocker = None
            inventory.sports_with_events.append(family)
        elif succeeded:
            # A configured provider answered and carried nothing. This is the
            # only case where an empty slate is a real answer.
            status = NO_EVENTS_RETURNED
            blocker = None
        elif budget_exhausted and not failures:
            status = DISCOVERY_BUDGET_EXHAUSTED
            blocker = DISCOVERY_BUDGET_EXHAUSTED
        else:
            status = classify_acquisition_failure(failures[0] if failures else None)
            blocker = failures[0] if failures else status

        inventory.acquisition_audit.append(
            {
                "family": family,
                "provider": registry.PROVIDER,
                "provider_sport_ids_attempted": attempted,
                "request_status": status,
                "events_returned": returned,
                "duplicate_rows_suppressed": duplicates,
                "blocker_if_any": blocker,
                "can_execute": False,
            }
        )
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
    # Preserve the existing event-state precedence: an event already started,
    # completed, cancelled, or postponed keeps that stronger terminal reason even
    # when it is also outside the requested slate date.
    if event.event_status == "CANCELLED_OR_POSTPONED":
        return CANCELLED_OR_POSTPONED
    if event.event_status in {"LIVE", "STARTED", "FINAL"}:
        return STARTED_OR_FINAL

    # For still-pregame rows, a parseable start that is provably outside the
    # requested local slate is wrong-date before canonical-identity routing.
    # Public/free discovery ids are intentionally aliases, so identity-first
    # ordering incorrectly labels next-day ESPN rows EVENT_IDENTITY_UNRESOLVED.
    # Missing/malformed start times do not earn WRONG_DATE and remain fail-closed.
    if _parse_instant(event.commence_time_utc) is not None and not _slate_date_matches(
        event.commence_time_utc, slate_date, tz_name
    ):
        return WRONG_DATE
    if not event.official_event_id or not event.home_team or not event.away_team:
        return IDENTITY_UNRESOLVED
    if event.event_status != REQUIRED_EVENT_STATE:
        return IDENTITY_UNRESOLVED
    return None


def _model_bucket(code: Any) -> str:
    token = _text(code).upper()
    if token in {MODEL_UNAVAILABLE, MODEL_INPUTS_INSUFFICIENT, MODEL_SCORER_FAILED, MODEL_OUTPUT_INVALID}:
        return token
    return OTHER_GOVERNED_HOLD


def model_supports_regime(event: DiscoveredEvent, model: Any) -> bool:
    """Whether a resolved model is contracted to score this row's regime.

    Fails closed by design. A model that does not declare ``supported_regimes``
    is treated as regular-season only, because a fitted regular-season artifact
    has no calibration evidence for preseason, playoff, spring-training or
    summer-league play and must never inherit those regimes by default.
    """
    declared = getattr(model, "supported_regimes", None)
    if not declared:
        return event.regime == registry.REGULAR_SEASON
    return event.regime in {str(value).upper() for value in declared}


def route_discovered_slate(
    inventory: DiscoveryInventory,
    *,
    resolve_model: Callable[[DiscoveredEvent], Any],
    score_row: Callable[[DiscoveredEvent, Any], Mapping[str, Any]],
    now: datetime | None = None,
    regime_supported: Callable[[DiscoveredEvent, Any], bool] = model_supports_regime,
) -> list[RoutedRow]:
    """Route every discovered row to exactly one terminal outcome.

    ``resolve_model`` returns the registered bridge for a row or ``None``. A
    ``None`` never removes the row: it produces a retained ``MODEL_UNAVAILABLE``
    candidate. A model that *was* selected and then failed keeps its own typed
    status — a scorer failure is not a missing model.

    A model resolved for the wrong season regime is treated as no controlling
    model for that row: it terminates as ``MODEL_UNAVAILABLE`` with its own
    blocker rather than being invoked outside its calibration contract.
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

        if not regime_supported(event, model):
            # Discovered and retained, but not routed. The regular-season model
            # is not the controlling model for this regime, and there is no
            # other one, so the row is MODEL_UNAVAILABLE for a named reason.
            rows.append(
                RoutedRow(
                    identity=event.identity(),
                    bucket=MODEL_UNAVAILABLE,
                    model_status=MODEL_UNAVAILABLE,
                    detail={
                        "reason": f"{event.sport}_{event.regime}_REGIME_NOT_SUPPORTED_BY_FITTED_MODEL",
                        "backend_route_status": "SPORT_SPECIFIC_TEAM_EVENT_REGIME_NOT_REGISTERED",
                        "regime": event.regime,
                        "supported_regimes": [
                            str(value) for value in (getattr(model, "supported_regimes", None) or (registry.REGULAR_SEASON,))
                        ],
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
        "acquisition_audit": list(inventory.acquisition_audit),
        "families_without_configured_feed": [
            row["family"]
            for row in inventory.acquisition_audit
            if row.get("request_status") == NO_CONFIGURED_DISCOVERY_FEED
        ],
        "source_blockers": list(inventory.source_blockers),
        "discovery_independent_of_model_registry": True,
        "can_execute": False,
    }


def run_cross_sport_winner_scan(
    *,
    requested_slate_date: str,
    requested_timezone: str,
    fetch_sport_events: Callable[..., Iterable[Mapping[str, Any]]],
    resolve_model: Callable[[DiscoveredEvent], Any],
    score_row: Callable[[DiscoveredEvent, Any], Mapping[str, Any]],
    supported_sports: Iterable[str] = SUPPORTED_DISCOVERY_SPORTS,
    discovery_targets: Mapping[str, tuple[DiscoveryTarget, ...]] | None = None,
    include_regime_variants: bool = False,
    now: datetime | None = None,
    budget_seconds: float | None = None,
    canonicalize_identity: Callable[[DiscoveredEvent], DiscoveredEvent] | None = None,
) -> dict[str, Any]:
    """Discovery -> canonical identity -> registry routing -> reconciliation.

    An optional sport-owned identity handoff may replace alias-only discovery
    with a canonical event before routing. It may not drop rows or create
    sporting probability.
    """
    counters_before = observability.counters()
    inventory = discover_winner_slate(
        requested_slate_date=requested_slate_date,
        requested_timezone=requested_timezone,
        fetch_sport_events=fetch_sport_events,
        supported_sports=supported_sports,
        discovery_targets=discovery_targets,
        include_regime_variants=include_regime_variants,
        now=now,
        budget_seconds=budget_seconds,
    )
    if callable(canonicalize_identity):
        inventory.events = [canonicalize_identity(event) for event in inventory.events]
    rows = route_discovered_slate(
        inventory, resolve_model=resolve_model, score_row=score_row, now=now
    )
    return {
        "discovery": inventory.as_dict(),
        "rows": [row.as_dict() for row in rows],
        "reconciliation": reconcile(inventory, rows),
        # Internal handoff to the Daily persistence boundary. The Daily runtime
        # persists then removes this list before either FULL or COMPACT output.
        "acquisition_details": list(inventory.acquisition_details),
        "expected_acquisition_targets": list(inventory.expected_acquisition_targets),
        # Acquisition counters attributable to this run, so a board with missing
        # rows can be read against what the provider lane actually did.
        "market_evidence_counters": observability.run_delta(counters_before),
        "can_execute": False,
    }


__all__ = [
    "ACQUISITION_STATUSES",
    "ALL_BUCKETS",
    "DISCOVERY_BUDGET_EXHAUSTED",
    "AcquisitionFeedResult",
    "DiscoveryFeedError",
    "DiscoveryTarget",
    "EVENTS_RETURNED",
    "NO_CONFIGURED_DISCOVERY_FEED",
    "NO_EVENTS_RETURNED",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_REQUEST_FAILED",
    "PROVIDER_SCHEMA_FAILURE",
    "classify_acquisition_failure",
    "default_discovery_targets",
    "model_supports_regime",
    "rundown_discovery_targets",
    "CANCELLED_OR_POSTPONED",
    "CAN_EXECUTE",
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
