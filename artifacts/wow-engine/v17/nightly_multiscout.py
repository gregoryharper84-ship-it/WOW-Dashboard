"""WOW V17 Nightly Multi-Scout discovery runner.

Discovery only. Sportsbook data, scanner agreement, game-script hypotheses and
research narratives are evidence; none are governed model probabilities.
Output is a typed handoff for the existing parallel discovery / specialist
routing chain. can_execute=false.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROXY_URL = os.environ.get("WOW_ODDS_PROXY_URL", "https://wow-odds-proxy.onrender.com").rstrip("/")
REGIONS = os.environ.get("WOW_SCOUT_REGIONS", "us")
ALLOW_MULTI_REGION = os.environ.get("WOW_SCOUT_ALLOW_MULTI_REGION", "false").strip().lower() == "true"
HORIZON_HOURS = int(os.environ.get("WOW_SCOUT_HORIZON_HOURS", "36"))
REQUEST_DELAY_MS = int(os.environ.get("WOW_SCOUT_REQUEST_DELAY_MS", "75"))
MARKET_CHUNK_SIZE = int(os.environ.get("WOW_SCOUT_MARKET_CHUNK_SIZE", "10"))
MAX_MARKETS_PER_EVENT = int(os.environ.get("WOW_SCOUT_MAX_MARKETS_PER_EVENT", "30"))
TERMINAL_SOURCE_HTTP_STATUSES = {401, 403, 429}

MANDATORY_SPORT_FAMILIES = {
    "basketball_nba": "NBA",
    "basketball_ncaab": "NCAAMB",
    "basketball_wnba": "WNBA",
    "americanfootball_ncaaf": "CFB",
    "americanfootball_nfl": "NFL",
    "baseball_mlb": "MLB",
    "icehockey_nhl": "NHL",
}

PROP_MARKET_TOKENS = (
    "player_", "pitcher_", "batter_", "passing_", "rushing_", "receiving_",
    "points", "rebounds", "assists", "threes", "blocks", "steals", "strikeouts",
    "outs", "shots", "saves", "goalscorer", "aces", "double_faults",
)

DERIVATIVE_MARKET_TOKENS = (
    "alternate", "1st_quarter", "2nd_quarter", "3rd_quarter", "4th_quarter",
    "1st_half", "2nd_half", "1st_period", "2nd_period", "3rd_period",
    "1st_5_innings", "1st_3_innings",
)

SCOUT_TEAM = (
    "BOARD_SCOUT",
    "CROSS_SPORT_OPPORTUNITY_SCOUT",
    "MATCHUP_AND_GAME_SCRIPT_SCOUT",
    "ROLE_NEWS_STATUS_SCOUT",
    "MARKET_ALTERNATE_LINE_SCOUT",
    "CONTRARIAN_RED_TEAM_SCOUT",
)

GENERIC_GAME_SCRIPTS = (
    "FAVORITE_CONTROL",
    "UNDERDOG_CONTROL_UPSET",
    "ONE_SCORE_OR_ONE_POSSESSION_CLOSE_GAME",
    "FAVORITE_BLOWOUT",
    "UNDERDOG_BLOWOUT",
    "COMEBACK_FAVORITE",
    "COMEBACK_UNDERDOG",
    "HIGH_PACE_HIGH_SCORING",
    "LOW_PACE_LOW_SCORING",
    "OVERTIME_OR_EXTRA_PERIOD_EXTENSION",
    "KEY_PLAYER_EARLY_EXIT_OR_LIMITATION",
    "ROTATION_OR_ROLE_CHANGE",
    "FATIGUE_TRAVEL_REST_DISADVANTAGE",
)

SPORT_GAME_SCRIPTS = {
    "baseball": ("STARTER_DOMINANCE", "EARLY_STARTER_FAILURE", "EARLY_HOOK", "BULLPEN_DOMINANCE", "BULLPEN_COLLAPSE", "HIGH_CONTACT_LOW_K", "SWING_AND_MISS_HIGH_K", "EXTRA_INNINGS"),
    "basketball": ("PACE_UP", "PACE_DOWN", "FOUL_EXTENSION", "STAR_FOUL_TROUBLE", "BENCH_BLOWOUT_RUN", "SMALL_BALL", "BIG_LINEUP", "HOT_THREE_POINT_VARIANCE", "COLD_THREE_POINT_VARIANCE"),
    "americanfootball": ("PASS_HEAVY_TRAILING", "RUN_HEAVY_LEADING", "TURNOVER_SHORT_FIELDS", "DEFENSIVE_GRIND", "SHOOTOUT", "WEATHER_SUPPRESSED_PASSING", "BACKUP_QB", "TWO_MINUTE_VOLUME"),
    "icehockey": ("GOALIE_DOMINANCE", "GOALIE_FAILURE", "EMPTY_NET_EXTENSION", "POWER_PLAY_HEAVY", "LOW_EVENT_FIVE_ON_FIVE", "HIGH_EVENT_FIVE_ON_FIVE", "OVERTIME"),
    "soccer": ("EARLY_FAVORITE_GOAL", "EARLY_UNDERDOG_GOAL", "LEVEL_LATE", "RED_CARD_FAVORITE", "RED_CARD_UNDERDOG", "LOW_BLOCK", "OPEN_TRANSITION_GAME", "DRAW_SCRIPT"),
    "tennis": ("STRAIGHT_SETS_FAVORITE", "STRAIGHT_SETS_UNDERDOG", "DECIDING_SET", "TIEBREAK_HEAVY", "BREAK_HEAVY", "SERVE_DOMINANCE", "FATIGUE_OR_MEDICAL_LIMITATION"),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def effective_regions() -> str:
    regions = [r.strip() for r in REGIONS.split(",") if r.strip()]
    if not regions:
        return "us"
    return ",".join(regions if ALLOW_MULTI_REGION else regions[:1])


@dataclass
class FetchResult:
    ok: bool
    data: Any = None
    status: int | None = None
    code: str | None = None


def proxy_get(path: str, params: dict[str, Any] | None = None) -> FetchResult:
    token = os.environ.get("WOW_ODDS_PROXY_ACTION_KEY") or os.environ.get("WOW_GITHUB_OIDC_TOKEN")
    if not token:
        return FetchResult(False, code="WOW_SCOUT_SOURCE_AUTH_UNCONFIGURED")
    query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"{PROXY_URL}{path}" + (f"?{query}" if query else "")
    req = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=25) as response:
            return FetchResult(True, json.loads(response.read().decode("utf-8")), response.status)
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            detail = payload.get("detail") if isinstance(payload, dict) else None
            detail = detail if isinstance(detail, dict) else {}
            code = payload.get("code") or detail.get("code")
        except Exception:
            code = None
        return FetchResult(False, status=exc.code, code=code or f"HTTP_{exc.code}")
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return FetchResult(False, code=type(exc).__name__)


def sport_family(key: str) -> str:
    return key.split("_", 1)[0] if "_" in key else key


def game_scripts(key: str) -> list[str]:
    return sorted(set(GENERIC_GAME_SCRIPTS + SPORT_GAME_SCRIPTS.get(sport_family(key), ())))


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def market_keys_from_inventory(payload: Any) -> list[str]:
    keys: set[str] = set()
    objects = payload if isinstance(payload, list) else [payload]
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        for book in obj.get("bookmakers", []) or []:
            if not isinstance(book, dict):
                continue
            for market in book.get("markets", []) or []:
                key = market.get("key") if isinstance(market, dict) else None
                if key:
                    keys.add(str(key))
    return sorted(keys)


def is_prop_market(key: str) -> bool:
    k = key.lower()
    return any(token in k for token in PROP_MARKET_TOKENS) and k not in {"h2h", "spreads", "totals"}


def bounded_market_keys(keys: list[str]) -> tuple[list[str], list[str]]:
    def priority(key: str) -> tuple[int, str]:
        k = key.lower()
        if k in {"h2h", "spreads", "totals"}:
            return (0, k)
        if not any(token in k for token in DERIVATIVE_MARKET_TOKENS):
            return (1, k)
        return (2, k)
    ordered = sorted(set(keys), key=priority)
    if MAX_MARKETS_PER_EVENT <= 0:
        return ordered, []
    return ordered[:MAX_MARKETS_PER_EVENT], ordered[MAX_MARKETS_PER_EVENT:]


def bookmaker_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    for book in payload.get("bookmakers", []) or []:
        if not isinstance(book, dict):
            continue
        book_key = book.get("key") or book.get("title") or "UNKNOWN_BOOKMAKER"
        for market in book.get("markets", []) or []:
            if not isinstance(market, dict):
                continue
            for outcome in market.get("outcomes", []) or []:
                if not isinstance(outcome, dict):
                    continue
                rows.append({
                    "bookmaker": book_key,
                    "bookmaker_title": book.get("title"),
                    "bookmaker_last_update": book.get("last_update"),
                    "market_key": market.get("key"),
                    "market_last_update": market.get("last_update"),
                    "outcome_name": outcome.get("name"),
                    "description": outcome.get("description"),
                    "price": outcome.get("price"),
                    "point": outcome.get("point"),
                    "link": outcome.get("link") or market.get("link") or book.get("link"),
                })
    return rows


def run() -> dict[str, Any]:
    started = utc_now()
    end = started + timedelta(hours=HORIZON_HOURS)
    regions = effective_regions()
    coverage: list[dict[str, Any]] = []
    source_blockers: list[dict[str, Any]] = []
    team_event_handoff: list[dict[str, Any]] = []
    prop_handoff: list[dict[str, Any]] = []
    sportsbook_set: set[str] = set()
    terminal_source_blocked = False

    sports_res = proxy_get("/odds-api/v4/sports", {"all": "true"})
    if not sports_res.ok:
        return {
            "schema_version": "wow.v17.nightly_multiscout.v1",
            "status": "BLOCKED_SOURCE_ACQUISITION",
            "reason_code": sports_res.code,
            "source_http_status": sports_res.status,
            "generated_at": iso(utc_now()),
            "can_execute": False,
            "model_handoff_ready": False,
            "coverage": coverage,
            "source_blockers": [{"scope": "sports", "reason_code": sports_res.code, "http_status": sports_res.status}],
        }

    sports = [s for s in (sports_res.data or []) if isinstance(s, dict) and s.get("active", True) and s.get("key")]
    active_keys = {str(s["key"]) for s in sports}
    for required, label in MANDATORY_SPORT_FAMILIES.items():
        coverage.append({"scope": "required_sport", "sport": required, "label": label, "status": "ACTIVE" if required in active_keys else "NOT_ACTIVE_OR_NOT_OFFERED"})

    for sport in sorted(sports, key=lambda x: str(x["key"])):
        if terminal_source_blocked:
            break
        key = str(sport["key"])
        events_res = proxy_get(
            f"/odds-api/v4/sports/{key}/events",
            {"dateFormat": "iso", "commenceTimeFrom": iso(started), "commenceTimeTo": iso(end), "includeRotationNumbers": "true"},
        )
        if not events_res.ok:
            blocker = {"scope": "sport", "sport": key, "status": "EVENT_FETCH_FAILED", "reason_code": events_res.code, "http_status": events_res.status}
            coverage.append(blocker)
            source_blockers.append(blocker)
            terminal_source_blocked = events_res.status in TERMINAL_SOURCE_HTTP_STATUSES
            continue
        events = [e for e in (events_res.data or []) if isinstance(e, dict) and e.get("id")]
        coverage.append({"scope": "sport", "sport": key, "status": "SCANNED", "events": len(events)})

        for event in events:
            if terminal_source_blocked:
                break
            event_id = str(event["id"])
            inventory = proxy_get(f"/odds-api/v4/sports/{key}/events/{event_id}/markets", {"regions": regions, "dateFormat": "iso"})
            if not inventory.ok:
                blocker = {"scope": "event", "sport": key, "event_id": event_id, "status": "MARKET_INVENTORY_FAILED", "reason_code": inventory.code, "http_status": inventory.status}
                coverage.append(blocker)
                source_blockers.append(blocker)
                terminal_source_blocked = inventory.status in TERMINAL_SOURCE_HTTP_STATUSES
                continue

            all_market_keys = market_keys_from_inventory(inventory.data)
            market_keys, deferred_market_keys = bounded_market_keys(all_market_keys)
            if deferred_market_keys:
                coverage.append({"scope": "event_market_budget", "sport": key, "event_id": event_id, "status": "BOUNDED", "selected_market_count": len(market_keys), "deferred_market_count": len(deferred_market_keys), "deferred_markets": deferred_market_keys})

            event_rows: list[dict[str, Any]] = []
            for market_chunk in chunked(market_keys, max(1, MARKET_CHUNK_SIZE)):
                odds = proxy_get(
                    f"/odds-api/v4/sports/{key}/events/{event_id}/odds",
                    {"markets": ",".join(market_chunk), "regions": regions, "dateFormat": "iso", "oddsFormat": "american", "includeLinks": "true", "includeSids": "true"},
                )
                if not odds.ok:
                    blocker = {"scope": "event_market_chunk", "sport": key, "event_id": event_id, "markets": market_chunk, "status": "ODDS_FETCH_FAILED", "reason_code": odds.code, "http_status": odds.status}
                    coverage.append(blocker)
                    source_blockers.append(blocker)
                    if odds.status in TERMINAL_SOURCE_HTTP_STATUSES:
                        terminal_source_blocked = True
                        break
                    continue
                rows = bookmaker_rows(odds.data)
                event_rows.extend(rows)
                sportsbook_set.update(str(r["bookmaker"]) for r in rows if r.get("bookmaker"))
                if REQUEST_DELAY_MS:
                    time.sleep(REQUEST_DELAY_MS / 1000.0)

            event_identity = {
                "official_event_id": event_id,
                "sport_key": key,
                "sport_title": sport.get("title"),
                "commence_time": event.get("commence_time"),
                "home_team": event.get("home_team"),
                "away_team": event.get("away_team"),
            }
            scripts = game_scripts(key)
            team_event_handoff.append({
                **event_identity,
                "route": "LLP_TEAM_BETTING_ENGINE",
                "discovery_status": "DISCOVERY_ONLY",
                "research_ceiling": "RESEARCH_INTEREST",
                "upset_evaluation_requested": True,
                "game_script_hypotheses": scripts,
                "game_scripts_are_evidence_only": True,
                "scout_team": list(SCOUT_TEAM),
                "market_evidence": [r for r in event_rows if not is_prop_market(str(r.get("market_key") or ""))],
                "contrarian_review_required": True,
                "canonicalization_required": True,
            })
            for row in event_rows:
                if not is_prop_market(str(row.get("market_key") or "")):
                    continue
                prop_handoff.append({
                    **event_identity,
                    "route": "WOW_PROP_LANE",
                    "discovery_status": "DISCOVERY_ONLY",
                    "research_ceiling": "RESEARCH_INTEREST",
                    "market_evidence": row,
                    "game_script_hypotheses": scripts,
                    "game_scripts_are_evidence_only": True,
                    "scout_team": list(SCOUT_TEAM),
                    "contrarian_review_required": True,
                    "canonicalization_required": True,
                })
            coverage.append({"scope": "event", "sport": key, "event_id": event_id, "status": "PARTIAL_SOURCE_BLOCKED" if terminal_source_blocked else "SCANNED", "market_keys": market_keys, "market_rows": len(event_rows)})

    finished = utc_now()
    status = "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS" if source_blockers else "DISCOVERY_COMPLETE"
    return {
        "schema_version": "wow.v17.nightly_multiscout.v1",
        "status": status,
        "generated_at": iso(finished),
        "window": {"from": iso(started), "to": iso(end)},
        "regions_configured": [r for r in REGIONS.split(",") if r],
        "regions_requested": [r for r in regions.split(",") if r],
        "acquisition_budget": {"max_markets_per_event": MAX_MARKETS_PER_EVENT, "market_chunk_size": MARKET_CHUNK_SIZE, "multi_region_opt_in": ALLOW_MULTI_REGION},
        "sportsbook_coverage": {
            "mode": "ALL_BOOKMAKERS_RETURNED_BY_CONFIGURED_PUBLIC_ODDS_FEED",
            "bookmakers_seen": sorted(sportsbook_set),
            "bookmaker_count": len(sportsbook_set),
            "note": "No claim is made for a sportsbook not exposed by the configured public feed; omissions remain visible in coverage telemetry.",
        },
        "scout_team": list(SCOUT_TEAM),
        "coverage": coverage,
        "source_blockers": source_blockers,
        "model_handoff": {
            "parallel_discovery_router_required": True,
            "slate_integrity_required": True,
            "deduplication_required": True,
            "no_silent_drop": True,
            "prop_candidates": prop_handoff,
            "team_event_candidates": team_event_handoff,
        },
        "governance": {
            "sportsbook_implied_probability_is_model_probability": False,
            "scout_consensus_is_model_probability": False,
            "upset_alert_requires_governed_llp_probability": True,
            "v17_terminal_reducer_is_terminal_authority": True,
            "can_execute": False,
        },
        "can_execute": False,
        "model_handoff_ready": not source_blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="v17/nightly-multiscout-handoff.json")
    args = parser.parse_args()
    payload = run()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload.get("status"), "output": str(output), "can_execute": False}))
    return 0 if payload.get("status") == "DISCOVERY_COMPLETE" else 2


if __name__ == "__main__":
    sys.exit(main())