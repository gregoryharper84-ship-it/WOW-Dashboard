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
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

from v17 import cross_sport_winner_discovery as discovery
from v17 import market_evidence_native_live as live
from v17 import rundown_sport_registry as registry

CAN_EXECUTE = False


def enabled() -> bool:
    return os.environ.get("WOW_V17_CROSS_SPORT_ML_DISCOVERY", "true").strip().lower() in {"1", "true"}


def horizon_hours() -> int:
    try:
        configured = int(os.environ.get("WOW_CROSS_SPORT_DISCOVERY_HORIZON_HOURS", "36"))
    except ValueError:
        configured = 36
    return max(1, min(configured, 168))


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    is used here only as one.
    """
    if proxy_get is None:
        from v17.nightly_multiscout import proxy_get as default_proxy_get

        proxy_get = default_proxy_get

    started = now or datetime.now(timezone.utc)
    end = started + timedelta(hours=horizon_hours())
    offered: dict[str, tuple[str, ...]] | None = dict(sport_keys) if sport_keys is not None else None
    offered_error: str | None = None

    def _offered_keys() -> dict[str, tuple[str, ...]]:
        """Families mapped to the sport keys the proxy currently advertises.

        A failed catalog lookup is deterministic for this feed instance/run.
        Memoize its typed failure so later sport families do not repeatedly hit
        the same failing ``/sports`` endpoint and amplify a provider quota or
        outage. Independent feeds in ``union_feed`` still get their own chance.
        """
        nonlocal offered, offered_error
        if offered is not None:
            return offered
        if offered_error is not None:
            raise discovery.DiscoveryFeedError(offered_error)
        listing = proxy_get("/odds-api/v4/sports", {"all": "true"})
        if not getattr(listing, "ok", False):
            offered_error = str(
                getattr(listing, "code", None) or "SPORT_INVENTORY_UNAVAILABLE"
            )
            raise discovery.DiscoveryFeedError(offered_error)
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

    def fetch(family: str, target: Any = None) -> list[Mapping[str, Any]]:
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
                raise discovery.DiscoveryFeedError(
                    str(getattr(result, "code", None) or "EVENT_DISCOVERY_FAILED")
                )
            rows.extend(
                row for row in (getattr(result, "data", None) or []) if isinstance(row, Mapping)
            )
        return rows

    return fetch


# Proxy sport keys are provider-owned strings. Rather than guessing one per
# family, match the keys the proxy reports against the family's own prefix and
# league token, so a key we have never seen simply does not match.
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
    """Discovery over TheRundown's current board, addressed by verified sport id.

    The provider sport id comes from the authoritative registry, so a family the
    provider does not carry is never approximated by a guessed key — it simply
    has no target to query.
    """

    def fetch(family: str, target: Any = None) -> list[Mapping[str, Any]]:
        sport_id = getattr(target, "sport_id", None)
        if sport_id is None:
            raise discovery.DiscoveryFeedError(registry.NO_CONFIGURED_DISCOVERY_FEED)
        result = live.get_sport_date_odds_snapshot(
            getattr(target, "league", None) or family,
            slate_date,
            capability="events",
            opener=opener,
            sport_id=sport_id,
        )
        if not result.ok:
            raise discovery.DiscoveryFeedError(str(result.code or "RUNDOWN_DISCOVERY_FAILED"))
        return [row for row in (result.data or []) if isinstance(row, Mapping)]

    return fetch


def union_feed(
    *feeds: Callable[..., Iterable[Mapping[str, Any]]],
) -> Callable[..., Iterable[Mapping[str, Any]]]:
    """Union several feeds, deduplicated on event identity.

    A feed that fails contributes nothing but does not take the others down with
    it; only a total failure of every feed for a sport raises, so the caller can
    still tell "could not look" from "nothing there".
    """

    def fetch(family: str, target: Any = None) -> list[Mapping[str, Any]]:
        rows: list[Mapping[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        failures: list[str] = []
        succeeded = False
        for feed in feeds:
            try:
                produced = list(feed(family, target) or ())
            except discovery.DiscoveryFeedError as exc:
                failures.append(exc.code)
                continue
            except Exception as exc:  # noqa: BLE001 - one feed's defect is not the board's
                failures.append(f"{type(exc).__name__}:{exc}")
                continue
            succeeded = True
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
        if not succeeded and failures:
            # Every feed refused. Surface the first provider code so the caller
            # can classify the acquisition status instead of guessing.
            raise discovery.DiscoveryFeedError(failures[0])
        return rows

    return fetch


__all__ = [
    "CAN_EXECUTE",
    "enabled",
    "horizon_hours",
    "odds_proxy_feed",
    "rundown_board_feed",
    "union_feed",
]
