"""Free pregame moneyline capture from ESPN's public scoreboard.

Owner direction (2026-10-09): operate primarily without paid outside market
APIs. ESPN's public scoreboard carries the featured sportsbook's moneyline
(DraftKings at time of writing) for pregame events and drops it once the game
is final, so the price must be captured before start. Captures are written to
the shared ``wow_market_price_observations`` ledger as CURRENT rows; the
existing captured-reference derivation turns the last pregame capture into a
CLOSE reference used by ``closing_line_grading``.

Evidence only: no key, no quota, ``prediction_authority=false``,
``can_execute=false``. A single book is one market view, not a consensus.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from v17 import rundown_market_ledger as ledger
from v17 import scout_secondary_source as espn

CAN_EXECUTE = False
PROVIDER = "ESPN"
MONEYLINE_MARKET_ID = "1"
DEFAULT_SPORT_KEYS = "baseball_mlb,americanfootball_nfl,basketball_wnba,americanfootball_ncaaf"
LOGGER = logging.getLogger(__name__)


def enabled() -> bool:
    return os.getenv("WOW_ESPN_MARKET_HISTORY_ENABLED", "true").strip().lower() == "true"


def interval_seconds() -> int:
    try:
        value = int(os.getenv("WOW_ESPN_MARKET_HISTORY_INTERVAL_SECONDS", "1800"))
    except ValueError:
        value = 1800
    return max(600, min(value, 7200))


def configured_sports() -> tuple[str, ...]:
    raw = os.getenv("WOW_ESPN_MARKET_HISTORY_SPORT_KEYS", DEFAULT_SPORT_KEYS)
    return tuple(key for key in (part.strip() for part in raw.split(",")) if key in espn.ESPN_SPORT_MAP)


def _timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("WOW_USER_TIMEZONE", "America/Chicago").strip() or "America/Chicago")
    except Exception:  # noqa: BLE001 - invalid config falls back deterministically
        return ZoneInfo("UTC")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _american(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    token = str(value).strip().upper()
    if token in {"", "EVEN", "EV"}:
        return 100.0 if token else None
    try:
        parsed = float(token.replace("+", ""))
    except ValueError:
        return None
    return parsed if parsed <= -100 or parsed >= 100 else None


def _side_price(odds: dict[str, Any], side: str) -> float | None:
    """Latest pregame moneyline for one side ('home'/'away').

    Prefer ``moneyline.<side>.close.odds`` (ESPN's running pregame close),
    then ``<side>TeamOdds.moneyLine`` (current).
    """
    moneyline = odds.get("moneyline") if isinstance(odds.get("moneyline"), dict) else {}
    side_block = moneyline.get(side) if isinstance(moneyline.get(side), dict) else {}
    close = side_block.get("close") if isinstance(side_block.get("close"), dict) else {}
    price = _american(close.get("odds"))
    if price is not None:
        return price
    team_odds = odds.get(f"{side}TeamOdds") if isinstance(odds.get(f"{side}TeamOdds"), dict) else {}
    return _american(team_odds.get("moneyLine"))


def _key(parts: list[Any]) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def observations_from_scoreboard(
    payload: Any,
    *,
    sport_key: str,
    fetched_at: datetime,
) -> list[dict[str, Any]]:
    """Two moneyline rows (home/away) per pregame ESPN event with both prices."""
    events = payload.get("events") if isinstance(payload, dict) else None
    rows: list[dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, dict) or not event.get("id"):
            continue
        status = ((event.get("status") or {}).get("type") or {}) if isinstance(event.get("status"), dict) else {}
        start = _parse_dt(event.get("date"))
        if str(status.get("state") or "").lower() != "pre" or start is None or start <= fetched_at:
            continue  # pregame only: a live or final price is never a close
        competitions = event.get("competitions") or []
        competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
        odds = next((row for row in competition.get("odds") or [] if isinstance(row, dict)), None)
        if not odds:
            continue
        home, away = espn._competitors(event)
        names = {"home": espn._team_name(home), "away": espn._team_name(away)}
        prices = {side: _side_price(odds, side) for side in ("home", "away")}
        if not all(names.values()) or any(price is None for price in prices.values()):
            continue  # one-sided or unnamed: unusable for a two-way close
        provider = odds.get("provider") if isinstance(odds.get("provider"), dict) else {}
        book_id = str(provider.get("id") or "featured")
        book_name = str(provider.get("name") or "ESPN featured book").strip() or "ESPN featured book"
        for side in ("home", "away"):
            american = prices[side]
            identity = [PROVIDER, str(event["id"]), MONEYLINE_MARKET_ID, names[side], book_id, american]
            rows.append({
                "observation_key": _key(identity),
                "provider": PROVIDER,
                "provider_event_id": f"espn-{event['id']}",
                "sport_key": sport_key,
                "event_start_utc": _iso(start),
                "market_id": MONEYLINE_MARKET_ID,
                "market_name": "moneyline",
                "participant_id": None,
                "participant_name": names[side],
                "participant_type": side.upper(),
                "selection": names[side],
                "line_id": None,
                "line_value": None,
                "affiliate_id": f"espn:{book_id}",
                "sportsbook": book_name,
                "american_odds": american,
                "decimal_odds": ledger.american_to_decimal(american),
                # Key excludes fetch time, so an unchanged price keeps its
                # first-seen time; a CLOSE is the last price seen before start.
                "price_updated_at": _iso(fetched_at),
                "fetched_at": _iso(fetched_at),
                "snapshot_kind": "CURRENT",
                "is_live": False,
                "is_main_line": True,
                "is_available": True,
                "closed_at": None,
                "raw_payload": {"espn_event_id": str(event["id"]), "side": side, "book_id": book_id},
                "prediction_authority": False,
                "can_execute": False,
            })
    return rows


def capture_once(
    client: Any,
    *,
    now: datetime | None = None,
    fetch: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """One free capture pass over today's and tomorrow's local slates."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = current.astimezone(_timezone()).date()
    fetch_json = fetch or espn._http_json  # uncached: prices move intraday
    summary: dict[str, Any] = {"status": "COMPLETE", "by_sport": {}, "prediction_authority": False, "can_execute": False}
    total = 0
    for sport_key in configured_sports():
        sport, league, _title = espn.ESPN_SPORT_MAP[sport_key]
        sport_rows: list[dict[str, Any]] = []
        failures: list[str] = []
        for day in (today, today + timedelta(days=1)):
            result = fetch_json(
                f"{espn.ESPN_BASE}/{sport}/{league}/scoreboard",
                {"dates": day.strftime("%Y%m%d"), "limit": 1000},
            )
            if not getattr(result, "ok", False):
                failures.append(str(getattr(result, "code", None) or "ESPN_SCOREBOARD_UNAVAILABLE"))
                continue
            sport_rows.extend(observations_from_scoreboard(result.data, sport_key=sport_key, fetched_at=current))
        written = ledger.persist_observations(client, sport_rows)
        total += written
        summary["by_sport"][sport_key] = {"rows_offered": written, "failures": failures}
        if failures:
            summary["status"] = "PARTIAL"
    summary["rows_offered"] = total
    return summary


def materialize_close_references(client: Any, *, now: datetime | None = None) -> int:
    from v17 import rundown_market_history as history

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = current.astimezone(_timezone()).date()
    written = 0
    for sport_key in configured_sports():
        for day in (today - timedelta(days=1), today):
            try:
                written += history.materialize_captured_references(
                    client, sport_key=sport_key, slate_date=day.isoformat(), now=current, provider=PROVIDER
                )
            except Exception:  # noqa: BLE001 - best effort per sport/day
                continue
    return written


def run_cycle(db_client_fn: Callable[[], Any], log: logging.Logger | None = None) -> dict[str, Any]:
    """Capture, materialize references, then grade. Each stage is isolated."""
    from v17 import rundown_market_history as history

    log = log or LOGGER
    out: dict[str, Any] = {"can_execute": False}
    try:
        client = db_client_fn()
        out["capture"] = capture_once(client)
        out["references"] = materialize_close_references(client)
        log.info(
            "ESPN_MARKET_HISTORY=%s rows=%s references=%s by_sport=%s can_execute=false",
            out["capture"].get("status"),
            out["capture"].get("rows_offered"),
            out["references"],
            out["capture"].get("by_sport"),
        )
    except Exception as exc:  # noqa: BLE001 - evidence loop must not affect liveness
        log.exception("ESPN_MARKET_HISTORY=FAIL error_type=%s can_execute=false", type(exc).__name__)
    out["grading"] = history.run_closing_line_grading_cycle(db_client_fn, log)
    return out


async def run_loop(db_client_fn: Callable[[], Any], *, logger: logging.Logger | None = None) -> None:
    await asyncio.sleep(45)
    while enabled():
        await asyncio.to_thread(run_cycle, db_client_fn, logger or LOGGER)
        await asyncio.sleep(interval_seconds())


def install_espn_market_history(app: Any, *, db_client_fn: Callable[[], Any] | None) -> bool:
    if not enabled() or not callable(db_client_fn):
        return False
    if getattr(app.state, "v17_espn_market_history_installed", False):
        return True
    tasks: set[asyncio.Task] = set()
    app.state.v17_espn_market_history_tasks = tasks

    @app.on_event("startup")
    async def _start_espn_market_history():
        task = asyncio.create_task(run_loop(db_client_fn, logger=LOGGER))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        LOGGER.info(
            "ESPN_MARKET_HISTORY=INSTALLED interval=%s sports=%s can_execute=false",
            interval_seconds(),
            ",".join(configured_sports()),
        )

    app.state.v17_espn_market_history_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "PROVIDER",
    "capture_once",
    "install_espn_market_history",
    "materialize_close_references",
    "observations_from_scoreboard",
    "run_cycle",
]
