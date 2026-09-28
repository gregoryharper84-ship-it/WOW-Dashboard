"""Default discovery feed for the cross-sport winner slate.

Discovery is deliberately source-agnostic in
``v17.cross_sport_winner_discovery``; this module supplies the concrete feed the
production runtime uses today — the same authorized odds proxy the nightly
Multi-Scout already walks, optionally unioned with TheRundown's current board.

Nothing here is a probability source. These feeds answer only "what winner
events exist"; which of them can be *scored* is a separate question the model
registry answers afterwards.
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

from v17 import cross_sport_winner_discovery as discovery
from v17 import market_evidence_native_live as live
from v17 import rundown_sport_registry as registry

CAN_EXECUTE = False
ODDS_API_DISCOVERY_DISABLED = "ODDS_API_DISCOVERY_DISABLED"

# TheRundown bills returned price rows. Cross-sport winner discovery needs only
# the moneyline/H2H board, not spreads, totals, props, or the provider's full
# odds universe. Keep the default to market id 1 only; operators may override it
# explicitly if a future discovery contract requires another provider market.
_DEFAULT_RUNDOWN_DISCOVERY_MARKET_IDS: tuple[str, ...] = ("1",)
_DEFAULT_RUNDOWN_DISCOVERY_AFFILIATE_IDS: tuple[str, ...] = ("3", "19", "23")
_ODDS_CONTENT_TYPE_CLASSES = frozenset({
    "JSON", "TEXT_HTML", "TEXT_PLAIN", "OTHER", "EMPTY",
})
_ODDS_PROVIDER_ALIASES = frozenset({
    "ODDS_API_PAID_KEY", "ODDS_API_KEY_100K", "ODDS_API_FREE_KEY", "ODDS_API_KEY",
})


def _closed_diagnostic(value: Any, allowed: frozenset[str]) -> str | None:
    token = str(value or "").strip().upper()
    return token if token in allowed else None


def _upstream_status(value: Any) -> int | None:
    return (
        int(value)
        if isinstance(value, int)
        and not isinstance(value, bool)
        and 100 <= value <= 599
        else None
    )


def _odds_failure_result(
    result: Any,
    code: str,
    *,
    path_state: str = discovery.PATH_FAILED,
) -> discovery.AcquisitionFeedResult:
    originating_blocker = None
    if "CIRCUIT_OPEN:" in str(code).upper():
        path_state = discovery.PATH_CIRCUIT_OPEN_PRIOR_FAILURE
        originating_blocker = discovery.circuit_originating_blocker(code)
    attempt = discovery.acquisition_attempt(
        path_id=discovery.PATH_ODDS_PROXY,
        path_state=path_state,
        blocker_code=code,
        originating_blocker_code=originating_blocker,
        upstream_status=_upstream_status(getattr(result, "upstream_status", None)),
        content_type_class=_closed_diagnostic(
            getattr(result, "content_type_class", None), _ODDS_CONTENT_TYPE_CLASSES
        ),
        credential_alias=_closed_diagnostic(
            getattr(result, "provider_alias", None), _ODDS_PROVIDER_ALIASES
        ),
    )
    return discovery.AcquisitionFeedResult(
        rows=(),
        provider_status=discovery.PROVIDER_FAILED,
        fallback_status=discovery.FALLBACK_NOT_APPLICABLE,
        exhaustion_status=discovery.PROVIDER_PATHS_EXHAUSTED,
        blocker_code=code,
        primary_blocker_code=code,
        primary_path_id=discovery.PATH_ODDS_PROXY,
        primary_path_state=path_state,
        fallback_path_state=discovery.PATH_NOT_APPLICABLE,
        primary_upstream_status=_upstream_status(getattr(result, "upstream_status", None)),
        primary_content_type_class=_closed_diagnostic(
            getattr(result, "content_type_class", None), _ODDS_CONTENT_TYPE_CLASSES
        ),
        primary_provider_alias=_closed_diagnostic(
            getattr(result, "provider_alias", None), _ODDS_PROVIDER_ALIASES
        ),
        attempts=(attempt,),
    )


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or default


def rundown_discovery_market_ids() -> tuple[str, ...]:
    """Provider market IDs used only to bound event-board acquisition cost."""
    return _csv_env(
        "WOW_RUNDOWN_DISCOVERY_MARKET_IDS",
        _DEFAULT_RUNDOWN_DISCOVERY_MARKET_IDS,
    )


def rundown_discovery_affiliate_ids() -> tuple[str, ...]:
    """Provider affiliate IDs used only to bound event-board acquisition cost."""
    return _csv_env(
        "WOW_RUNDOWN_DISCOVERY_AFFILIATE_IDS",
        _DEFAULT_RUNDOWN_DISCOVERY_AFFILIATE_IDS,
    )


def enabled() -> bool:
    return os.environ.get("WOW_V17_CROSS_SPORT_ML_DISCOVERY", "true").strip().lower() in {"1", "true"}


def odds_api_enabled() -> bool:
    """Whether The Odds API may participate in cross-sport event discovery.

    This controls discovery only. Disabling it must not disable the cross-sport
    lane: schedule-first/public sources and the remaining configured feeds keep
    running through the existing resilient union.
    """
    return os.environ.get("WOW_CROSS_SPORT_ODDS_API_ENABLED", "true").strip().lower() in {
        "1",
        "true",
    }


def horizon_hours() -> int:
    try:
        configured = int(os.environ.get("WOW_CROSS_SPORT_DISCOVERY_HORIZON_HOURS", "36"))
    except ValueError:
        configured = 36
    return max(1, min(configured, 168))


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _odds_api_alias_row(raw: Mapping[str, Any], *, sport_key: str) -> dict[str, Any]:
    """Stamp one zero-credit Odds API event as alias-only discovery evidence."""
    row = dict(raw)
    alias = row.get("id") or row.get("event_id") or row.get("event_uuid")
    row["provider_event_id"] = alias
    row["provider_event_id_type"] = "THE_ODDS_API_EVENT_ALIAS"
    row["canonical_identity_status"] = "ALIAS_ONLY_UNRESOLVED"
    row["provider_sport_key"] = sport_key
    row["discovery_provider"] = "ODDS_API_EVENTS"
    row["source_provider"] = "ODDS_API_EVENTS"
    row["research_only"] = True
    row["prediction_authority"] = False
    row["exact_line_authority"] = False
    row["can_execute"] = False
    # Provider event IDs are useful aliases, but they are not WOW/league
    # canonical IDs. Remove every key the canonical normalizer considers an
    # official identity so the alias cannot be promoted by accident.
    for key in ("id", "event_id", "official_event_id", "event_uuid"):
        row.pop(key, None)
    return row


def odds_proxy_feed(
    *,
    proxy_get: Callable[..., Any] | None = None,
    now: datetime | None = None,
    sport_keys: Mapping[str, tuple[str, ...]] | None = None,
) -> Callable[..., Iterable[Mapping[str, Any]]]:
    """Discovery over the authorized odds proxy's per-sport event route.

    The proxy addresses sports by key, not by TheRundown's numeric id, and this
    module does not guess keys: it asks the proxy which sports it actually
    offers and matches those against the LLP families. A family the proxy does
    not carry contributes nothing here, and TheRundown feed covers it instead.

    The proxy's ``/events`` route is an event listing, not an odds snapshot, and
    is used here only as one. Provider event IDs remain aliases until a separate
    canonical resolver proves identity.

    When ``WOW_CROSS_SPORT_ODDS_API_ENABLED`` is false, this adapter fails typed
    before importing or calling the proxy. The union/resilience layers then use
    the other configured discovery sources; the cross-sport lane itself remains
    enabled.
    """
    if not odds_api_enabled():
        def disabled_fetch(_family: str, _target: Any = None) -> list[Mapping[str, Any]]:
            acquisition = discovery.AcquisitionFeedResult(
                rows=(),
                provider_status=discovery.PROVIDER_NOT_ATTEMPTED,
                fallback_status=discovery.FALLBACK_NOT_APPLICABLE,
                exhaustion_status=discovery.PROVIDER_PATHS_EXHAUSTED,
                blocker_code=ODDS_API_DISCOVERY_DISABLED,
                primary_blocker_code=ODDS_API_DISCOVERY_DISABLED,
                primary_path_id=discovery.PATH_ODDS_PROXY,
                primary_path_state=discovery.PATH_NOT_ATTEMPTED,
                fallback_path_state=discovery.PATH_NOT_APPLICABLE,
                attempts=(
                    discovery.acquisition_attempt(
                        path_id=discovery.PATH_ODDS_PROXY,
                        path_state=discovery.PATH_NOT_ATTEMPTED,
                        blocker_code=ODDS_API_DISCOVERY_DISABLED,
                    ),
                ),
            )
            raise discovery.DiscoveryFeedError(
                ODDS_API_DISCOVERY_DISABLED, acquisition=acquisition
            )

        setattr(disabled_fetch, "_wow_odds_api_enabled", False)
        setattr(disabled_fetch, "_wow_acquisition_path_id", discovery.PATH_ODDS_PROXY)
        return disabled_fetch

    if proxy_get is None:
        from v17.nightly_multiscout import proxy_get as default_proxy_get

        proxy_get = default_proxy_get

    started = now or datetime.now(timezone.utc)
    end = started + timedelta(hours=horizon_hours())
    offered: dict[str, tuple[str, ...]] | None = dict(sport_keys) if sport_keys is not None else None
    offered_error: tuple[str, discovery.AcquisitionFeedResult] | None = None

    def _offered_keys() -> dict[str, tuple[str, ...]]:
        nonlocal offered, offered_error
        if offered is not None:
            return offered
        if offered_error is not None:
            code, acquisition = offered_error
            circuit_attempt = discovery.acquisition_attempt(
                path_id=discovery.PATH_ODDS_PROXY,
                path_state=discovery.PATH_CIRCUIT_OPEN_PRIOR_FAILURE,
                blocker_code="ODDS_PROXY_CIRCUIT_OPEN",
                originating_blocker_code=code,
                upstream_status=acquisition.primary_upstream_status,
                content_type_class=acquisition.primary_content_type_class,
                credential_alias=acquisition.primary_provider_alias,
            )
            raise discovery.DiscoveryFeedError(
                code,
                acquisition=replace(
                    acquisition,
                    attempts=(circuit_attempt,),
                ),
            )
        listing = proxy_get("/odds-api/v4/sports", {"all": "true"})
        if not getattr(listing, "ok", False):
            code = str(
                getattr(listing, "code", None) or "SPORT_INVENTORY_UNAVAILABLE"
            )
            acquisition = _odds_failure_result(listing, code)
            offered_error = (code, acquisition)
            raise discovery.DiscoveryFeedError(code, acquisition=acquisition)
        mapping: dict[str, list[str]] = {}
        for row in getattr(listing, "data", None) or []:
            if not isinstance(row, Mapping) or not row.get("key"):
                continue
            if row.get("active") is False:
                continue
            family = _family_for_proxy_key(str(row["key"]))
            if family:
                mapping.setdefault(family, []).append(str(row["key"]))
        offered = {family: tuple(keys) for family, keys in mapping.items()}
        return offered

    def fetch(family: str, target: Any = None) -> discovery.AcquisitionFeedResult:
        keys = _offered_keys().get(str(family).upper(), ())
        rows: list[Mapping[str, Any]] = []
        for sport_key in keys:
            result = proxy_get(
                f"/odds-api/v4/sports/{sport_key}/events",
                {
                    "dateFormat": "iso",
                    "commenceTimeFrom": _iso(started),
                    "commenceTimeTo": _iso(end),
                },
            )
            if not getattr(result, "ok", False):
                code = str(getattr(result, "code", None) or "EVENT_DISCOVERY_FAILED")
                raise discovery.DiscoveryFeedError(
                    code, acquisition=_odds_failure_result(result, code)
                )
            rows.extend(
                _odds_api_alias_row(row, sport_key=sport_key)
                for row in (getattr(result, "data", None) or [])
                if isinstance(row, Mapping)
            )
        return discovery.AcquisitionFeedResult(
            rows=tuple(rows),
            provider_status=discovery.PROVIDER_SUCCEEDED,
            fallback_status=discovery.FALLBACK_NOT_APPLICABLE,
            exhaustion_status=discovery.PATHS_NOT_EXHAUSTED,
            primary_path_id=discovery.PATH_ODDS_PROXY,
            primary_path_state=discovery.succeeded_path_state(rows),
            fallback_path_state=discovery.PATH_NOT_APPLICABLE,
            attempts=(
                discovery.acquisition_attempt(
                    path_id=discovery.PATH_ODDS_PROXY,
                    path_state=discovery.succeeded_path_state(rows),
                ),
            ),
        )

    setattr(fetch, "_wow_odds_api_enabled", True)
    setattr(fetch, "_wow_acquisition_path_id", discovery.PATH_ODDS_PROXY)
    return fetch


# Proxy sport keys are provider-owned strings. Match only advertised keys against
# known family prefixes; a key we have never seen is not invented by this layer.
_PROXY_KEY_FAMILY_TOKENS: dict[str, tuple[str, ...]] = {
    "MLB": ("baseball_mlb",),
    "NFL": ("americanfootball_nfl",),
    "NCAAF": ("americanfootball_ncaaf",),
    "NBA": ("basketball_nba",),
    "WNBA": ("basketball_wnba",),
    "NCAAB": ("basketball_ncaab",),
    "NHL": ("icehockey_nhl",),
    "MMA": ("mma_",),
    "SOCCER": ("soccer_",),
    "TENNIS": ("tennis_",),
    "PGA": ("golf_",),
    "BOXING": ("boxing_",),
    "CRICKET": ("cricket_",),
}


def _family_for_proxy_key(key: str) -> str | None:
    token = key.strip().lower()
    for family, prefixes in _PROXY_KEY_FAMILY_TOKENS.items():
        for prefix in prefixes:
            if token == prefix or token.startswith(prefix):
                return family
    return None


def rundown_board_feed(
    *,
    slate_date: str,
    opener: Any = None,
) -> Callable[..., Iterable[Mapping[str, Any]]]:
    """Discovery over a quota-bounded TheRundown current board.

    This path needs winner-event identity only. The shared native adapter still
    parses a real provider odds snapshot, but the request is deliberately
    constrained to H2H/moneyline main lines and a small affiliate set. That
    preserves discovery semantics while preventing spreads, totals, props, or a
    full market/book snapshot from consuming the allowance merely to enumerate
    winner candidates.
    """

    def fetch(family: str, target: Any = None) -> discovery.AcquisitionFeedResult:
        sport_id = getattr(target, "sport_id", None)
        if sport_id is None:
            code = registry.NO_CONFIGURED_DISCOVERY_FEED
            acquisition = discovery.AcquisitionFeedResult(
                rows=(),
                provider_status=discovery.PROVIDER_NOT_ATTEMPTED,
                fallback_status=discovery.FALLBACK_NOT_APPLICABLE,
                exhaustion_status=discovery.NO_CONFIGURED_PATH,
                blocker_code=code,
                primary_blocker_code=code,
                primary_path_id=discovery.PATH_RUNDOWN,
                primary_path_state=discovery.PATH_NOT_ATTEMPTED,
                fallback_path_state=discovery.PATH_NOT_APPLICABLE,
                attempts=(
                    discovery.acquisition_attempt(
                        path_id=discovery.PATH_RUNDOWN,
                        path_state=discovery.PATH_NOT_ATTEMPTED,
                        blocker_code=code,
                    ),
                ),
            )
            raise discovery.DiscoveryFeedError(code, acquisition=acquisition)
        result = live.get_sport_date_odds_snapshot(
            getattr(target, "league", None) or family,
            slate_date,
            capability="events",
            opener=opener,
            sport_id=sport_id,
            market_ids=rundown_discovery_market_ids(),
            affiliate_ids=rundown_discovery_affiliate_ids(),
            main_line=True,
            hide_closed=True,
        )
        if not result.ok:
            code = str(result.code or "RUNDOWN_DISCOVERY_FAILED")
            acquisition = discovery.AcquisitionFeedResult(
                rows=(),
                provider_status=discovery.PROVIDER_FAILED,
                fallback_status=discovery.FALLBACK_NOT_APPLICABLE,
                exhaustion_status=discovery.PROVIDER_PATHS_EXHAUSTED,
                blocker_code=code,
                primary_blocker_code=code,
                primary_path_id=discovery.PATH_RUNDOWN,
                primary_path_state=discovery.PATH_FAILED,
                fallback_path_state=discovery.PATH_NOT_APPLICABLE,
                attempts=(
                    discovery.acquisition_attempt(
                        path_id=discovery.PATH_RUNDOWN,
                        path_state=discovery.PATH_FAILED,
                        blocker_code=code,
                        upstream_status=_upstream_status(
                            getattr(result, "upstream_status", None)
                            or getattr(result, "status", None)
                        ),
                    ),
                ),
            )
            raise discovery.DiscoveryFeedError(code, acquisition=acquisition)
        return discovery.AcquisitionFeedResult(
            rows=tuple(row for row in (result.data or []) if isinstance(row, Mapping)),
            provider_status=discovery.PROVIDER_SUCCEEDED,
            fallback_status=discovery.FALLBACK_NOT_APPLICABLE,
            exhaustion_status=discovery.PATHS_NOT_EXHAUSTED,
            primary_path_id=discovery.PATH_RUNDOWN,
            primary_path_state=discovery.succeeded_path_state(result.data or ()),
            fallback_path_state=discovery.PATH_NOT_APPLICABLE,
            attempts=(
                discovery.acquisition_attempt(
                    path_id=discovery.PATH_RUNDOWN,
                    path_state=discovery.succeeded_path_state(result.data or ()),
                    upstream_status=_upstream_status(
                        getattr(result, "upstream_status", None)
                        or getattr(result, "status", None)
                    ),
                ),
            ),
        )

    setattr(fetch, "_wow_acquisition_path_id", discovery.PATH_RUNDOWN)
    return fetch


def union_feed(
    *feeds: Callable[..., Iterable[Mapping[str, Any]]],
) -> Callable[..., Iterable[Mapping[str, Any]]]:
    """Union several feeds, deduplicated on event identity.

    A feed that fails contributes nothing but does not take the others down with
    it; only a total failure of every feed for a sport raises, so the caller can
    still tell "could not look" from "nothing there".
    """

    def fetch(family: str, target: Any = None) -> discovery.AcquisitionFeedResult:
        rows: list[Mapping[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        failures: list[str] = []
        path_failures: list[str | None] = []
        path_succeeded: list[bool] = []
        path_row_counts: list[int] = []
        acquisitions: list[discovery.AcquisitionFeedResult | None] = []
        path_ids: list[str | None] = []
        for feed in feeds:
            path_ids.append(getattr(feed, "_wow_acquisition_path_id", None))
            try:
                outcome = feed(family, target)
                acquisition = (
                    outcome if isinstance(outcome, discovery.AcquisitionFeedResult) else None
                )
                produced = list(outcome or ())
            except discovery.DiscoveryFeedError as exc:
                failures.append(exc.code)
                path_succeeded.append(False)
                path_row_counts.append(0)
                path_failures.append(exc.code)
                acquisitions.append(exc.acquisition)
                continue
            except Exception as exc:  # noqa: BLE001 - one feed's defect is not the board's
                failures.append(type(exc).__name__)
                path_succeeded.append(False)
                path_row_counts.append(0)
                path_failures.append(type(exc).__name__)
                acquisitions.append(None)
                continue
            path_succeeded.append(True)
            path_row_counts.append(len(produced))
            path_failures.append(None)
            acquisitions.append(acquisition)
            for row in produced:
                if not isinstance(row, Mapping):
                    continue
                key = (
                    str(row.get("home_team") or "").strip().lower(),
                    str(row.get("away_team") or "").strip().lower(),
                    str(
                        row.get("commence_time")
                        or row.get("event_date")
                        or row.get("start_time")
                        or ""
                    )[:16],
                )
                if key in seen and all(key):
                    continue
                seen.add(key)
                rows.append(row)
        primary_succeeded = bool(path_succeeded and path_succeeded[0])
        fallback_attempted = len(path_succeeded) > 1
        fallback_succeeded = any(path_succeeded[1:])
        primary_acquisition = acquisitions[0] if acquisitions else None
        fallback_acquisition = next(
            (item for item in acquisitions[1:] if item is not None), None
        )
        fallback_path_ids = {
            (
                item.primary_path_id
                if item is not None and item.primary_path_id
                else path_ids[index]
            )
            for index, item in enumerate(acquisitions[1:], start=1)
            if (
                (item is not None and item.primary_path_id)
                or path_ids[index]
            )
        }
        fallback_path_id = (
            next(iter(fallback_path_ids))
            if len(fallback_path_ids) == 1
            else discovery.PATH_GOVERNED_FALLBACK_UNION
            if fallback_path_ids
            else None
        )
        primary_blocker_code = (
            primary_acquisition.primary_blocker_code
            if primary_acquisition is not None
            and primary_acquisition.primary_blocker_code
            else path_failures[0] if path_failures else None
        )
        fallback_blocker_code = next(
            (
                acquisition.primary_blocker_code
                if acquisition is not None and acquisition.primary_blocker_code
                else path_failures[index]
                for index, acquisition in enumerate(acquisitions[1:], start=1)
                if (
                    (acquisition is not None and acquisition.primary_blocker_code)
                    or path_failures[index]
                )
            ),
            None,
        )
        result = discovery.AcquisitionFeedResult(
            rows=tuple(rows),
            provider_status=(
                discovery.PROVIDER_SUCCEEDED if primary_succeeded else discovery.PROVIDER_FAILED
            ),
            fallback_status=(
                discovery.FALLBACK_NOT_APPLICABLE
                if not fallback_attempted
                else discovery.FALLBACK_SUCCEEDED
                if fallback_succeeded
                else discovery.FALLBACK_FAILED
            ),
            exhaustion_status=(
                discovery.PATHS_NOT_EXHAUSTED
                if any(path_succeeded)
                else discovery.PROVIDER_PATHS_EXHAUSTED
            ),
            blocker_code=failures[0] if failures else None,
            primary_blocker_code=primary_blocker_code,
            fallback_blocker_code=fallback_blocker_code,
            primary_path_id=(
                primary_acquisition.primary_path_id
                if primary_acquisition is not None and primary_acquisition.primary_path_id
                else path_ids[0] if path_ids else None
            ),
            primary_path_state=(
                primary_acquisition.primary_path_state
                if primary_acquisition is not None and primary_acquisition.primary_path_state
                else discovery.succeeded_path_state(range(path_row_counts[0]))
                if primary_succeeded
                else discovery.PATH_FAILED
            ),
            fallback_path_id=fallback_path_id,
            fallback_path_state=(
                discovery.PATH_NOT_APPLICABLE
                if not fallback_attempted
                else discovery.succeeded_path_state(
                    range(sum(path_row_counts[1:]))
                )
                if fallback_succeeded
                else (
                    fallback_acquisition.primary_path_state
                    if fallback_acquisition is not None
                    and fallback_acquisition.primary_path_state
                    == discovery.PATH_CIRCUIT_OPEN_PRIOR_FAILURE
                    and len(path_succeeded[1:]) == 1
                    else discovery.PATH_FAILED
                )
            ),
            primary_upstream_status=(
                primary_acquisition.primary_upstream_status
                if primary_acquisition is not None else None
            ),
            primary_content_type_class=(
                primary_acquisition.primary_content_type_class
                if primary_acquisition is not None else None
            ),
            primary_provider_alias=(
                primary_acquisition.primary_provider_alias
                if primary_acquisition is not None else None
            ),
            fallback_upstream_status=(
                fallback_acquisition.primary_upstream_status
                if fallback_acquisition is not None else None
            ),
            fallback_content_type_class=(
                fallback_acquisition.primary_content_type_class
                if fallback_acquisition is not None else None
            ),
            fallback_provider_alias=(
                fallback_acquisition.primary_provider_alias
                if fallback_acquisition is not None else None
            ),
            attempts=discovery.ordered_acquisition_attempts(
                *(
                    acquisition.attempts
                    for acquisition in acquisitions
                    if acquisition is not None and acquisition.attempts
                )
            ),
        )
        if not any(path_succeeded) and failures:
            raise discovery.DiscoveryFeedError(failures[0], acquisition=result)
        return result

    setattr(fetch, "_wow_acquisition_path_id", discovery.PATH_GOVERNED_FALLBACK_UNION)
    return fetch


__all__ = [
    "CAN_EXECUTE",
    "ODDS_API_DISCOVERY_DISABLED",
    "enabled",
    "horizon_hours",
    "odds_api_enabled",
    "odds_proxy_feed",
    "rundown_board_feed",
    "rundown_discovery_affiliate_ids",
    "rundown_discovery_market_ids",
    "union_feed",
]
