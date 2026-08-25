"""
The Odds API service — pulls events, markets, and player props.
Docs: https://the-odds-api.com/lts-odds-api/

Failover: get_h2h_odds() transparently falls back to TheRundown on quota
exhaustion (429) or invalid-key (401), normalizing TheRundown's moneyline
shape into the Odds API bookmakers/markets/outcomes shape so no downstream
consumer needs changing.
"""
import os
import hashlib
import requests
from datetime import datetime, timezone

# Key read at call time (not module-load time) so rotation takes effect
# without a restart.  The module-level constant is kept for back-compat only.
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4"

SPORT_KEYS = {
    "NBA":   "basketball_nba",
    "WNBA":  "basketball_wnba",
    "MLB":   "baseball_mlb",
    "NFL":   "americanfootball_nfl",
    "NHL":   "icehockey_nhl",
    "NCAAB": "basketball_ncaab",
    "NCAAF": "americanfootball_ncaaf",
    "Soccer": "soccer_epl",
    "Tennis": "tennis_atp_french_open",
}

# Reverse map: "basketball_nba" → "NBA" — used when routing to TheRundown
_SPORT_KEY_TO_NAME = {v: k for k, v in SPORT_KEYS.items()}

# Status strings that warrant a TheRundown failover attempt.  Transient
# network errors (timeout, 5xx) are NOT in this set — they signal infra
# problems that TheRundown will likely share, so we pass the original
# failure straight through instead of burning a second quota hit.
_RUNDOWN_FAILOVER_STATUSES = frozenset({
    "FAILED: quota exhausted",
    "FAILED: invalid ODDS_API_KEY",
})

PLAYER_PROP_MARKETS = {
    "NBA":  [
        "player_points", "player_rebounds", "player_assists",
        "player_threes", "player_steals", "player_blocks",
        "player_points_rebounds_assists",
    ],
    "WNBA": [
        "player_points", "player_rebounds", "player_assists",
        "player_threes", "player_steals", "player_blocks",
        "player_points_rebounds_assists",
    ],
    "MLB":  [
        "batter_hits", "batter_home_runs", "batter_rbis",
        "batter_strikeouts", "batter_total_bases",
        "pitcher_strikeouts", "pitcher_hits_allowed", "pitcher_outs",
        "pitcher_walks", "pitcher_earned_runs",
    ],
    "NFL":  [
        "player_pass_tds", "player_pass_yds", "player_rush_yds",
        "player_reception_yds", "player_receptions",
        "player_rush_attempts", "player_tackles_assists",
    ],
    "NHL":  [
        "player_points", "player_goals", "player_assists",
        "player_shots_on_goal",
    ],
    "NCAAB": ["player_points", "player_rebounds", "player_assists"],
    "NCAAF": ["player_pass_yds", "player_rush_yds", "player_reception_yds"],
    "Soccer": ["player_goal_scorer_anytime", "player_shots_on_target"],
    "Tennis": ["player_set_winner"],
}


def _resolve_key() -> str:
    """
    Resolve the active Odds API key.

    Priority:
      1. ODDS_API_PAID_KEY  (higher quota)
      2. ODDS_API_KEY_100K  (high-quota existing key)
      3. ODDS_API_FREE_KEY  (fallback)
      4. ODDS_API_KEY       (legacy / back-compat)

    Returns empty string when none are configured.
    Note: _get() now retries the full ladder on auth/quota failures, so
    this function is used only for the no-key guard (returns empty when
    none are configured at all).
    """
    return (
        os.environ.get("ODDS_API_PAID_KEY", "")
        or os.environ.get("ODDS_API_KEY_100K", "")
        or os.environ.get("ODDS_API_FREE_KEY", "")
        or os.environ.get("ODDS_API_KEY", "")
    )


def resolve_odds_api_key_with_source() -> "tuple[str, str]":
    """Public helper — returns (api_key, source_env_var_name).

    Priority: ODDS_API_PAID_KEY → ODDS_API_KEY_100K → ODDS_API_FREE_KEY → ODDS_API_KEY (legacy).

    source_env_var_name values:
      "ODDS_API_PAID_KEY"   — paid quota key in use
      "ODDS_API_KEY_100K"   — high-quota key in use
      "ODDS_API_FREE_KEY"   — free-tier key in use
      "ODDS_API_KEY_LEGACY" — legacy/back-compat key in use
      "NONE"                — no key configured; api_key is ""

    Use this wherever the Odds API key is needed so all consumers share
    the same priority ladder and a deactivated legacy key can never
    override an available paid or free key.  Never log the returned
    api_key value.
    """
    paid = os.environ.get("ODDS_API_PAID_KEY", "")
    if paid:
        return paid, "ODDS_API_PAID_KEY"
    high = os.environ.get("ODDS_API_KEY_100K", "")
    if high:
        return high, "ODDS_API_KEY_100K"
    free = os.environ.get("ODDS_API_FREE_KEY", "")
    if free:
        return free, "ODDS_API_FREE_KEY"
    legacy = os.environ.get("ODDS_API_KEY", "")
    if legacy:
        return legacy, "ODDS_API_KEY_LEGACY"
    return "", "NONE"


def _get(path, params=None):
    """
    Try all configured keys in priority order: PAID → 100K → FREE → LEGACY.

    Failover logic
    ---------------
    Reactive:  falls back to the next key on auth (401), quota (422/429).
    Proactive: before each HTTP attempt, checks the Postgres cross-worker
               quota state (best-effort, fail-open). When a tier's
               requests_remaining is recorded as exactly 0, skip its HTTP
               call entirely and move to the next tier — saving a round-trip
               that would consume quota on whichever key receives it.

    Non-retriable HTTP errors (timeout, 5xx, etc.) stop immediately.

    key_tier names used in pg_odds_quota:
      'paid'   → ODDS_API_PAID_KEY
      'high'   → ODDS_API_KEY_100K (100K-request key)
      'free'   → ODDS_API_FREE_KEY
      'legacy' → ODDS_API_KEY (back-compat)
    """
    # Build the per-key ladder with tier labels so the proactive quota check
    # can look up the right tier name from the Postgres snapshot.
    _key_ladder = [
        (tier, key)
        for tier, key in [
            ("paid",   os.environ.get("ODDS_API_PAID_KEY", "")),
            ("high",   os.environ.get("ODDS_API_KEY_100K", "")),
            ("free",   os.environ.get("ODDS_API_FREE_KEY", "")),
            ("legacy", os.environ.get("ODDS_API_KEY", "")),
        ]
        if key
    ]
    if not _key_ladder:
        return None, "NOT_CALLED: ODDS_API_KEY not set"

    # Best-effort proactive quota snapshot from Postgres.
    # Fail-open: any DB error leaves _quota_snapshot as {} and we proceed
    # without proactive skipping (reactive fallback still works fine).
    _quota_snapshot: dict = {}
    try:
        from gate_engine.pg_odds_quota import fetch_quota_snapshot as _fqs
        _quota_snapshot = _fqs() or {}
    except Exception:
        pass   # fail-open — quota tracking must never block the caller

    params = dict(params or {})   # copy — never mutate the caller's dict
    _last_msg = "FAILED: invalid ODDS_API_KEY"
    for _tier, _key in _key_ladder:
        # Proactive skip: if cross-worker quota store already shows this tier
        # is exhausted (remaining == 0), skip the HTTP call and try the next.
        _tier_state = _quota_snapshot.get(_tier, {})
        _remaining  = _tier_state.get("requests_remaining")
        if _remaining is not None and _remaining == 0:
            _last_msg = f"proactive_skip:{_tier}:quota_exhausted"
            continue

        _p = {**params, "apiKey": _key}
        try:
            r = requests.get(f"{BASE_URL}{path}", params=_p, timeout=15)
        except requests.exceptions.Timeout:
            return None, "FAILED: timeout"
        except Exception as e:
            return None, f"FAILED: {e}"
        remaining = r.headers.get("x-requests-remaining", "?")
        if r.status_code == 200:
            return r.json(), f"AVAILABLE (remaining={remaining})"
        elif r.status_code in (401, 429):
            _last_msg = "FAILED: quota exhausted" if r.status_code == 429 else "FAILED: invalid ODDS_API_KEY"
            continue   # try next key in ladder
        elif r.status_code == 422:
            # Out-of-credits returns 422; treat as quota exhausted and try next key
            _last_msg = f"FAILED: unprocessable ({r.text[:120]})"
            continue
        else:
            return None, f"FAILED: HTTP {r.status_code}"
    return None, _last_msg


def _normalize_rundown_to_h2h_events(rundown_events):
    """
    Normalize TheRundown moneyline events into The Odds API bookmakers/markets/
    outcomes shape so _books_from_odds_api_event (consensus_odds.py) needs no
    changes.

    TheRundown source shape per event
    ----------------------------------
    event.teams_normalized[0].name       → home_team
    event.teams_normalized[-1].name      → away_team
    event.event_date                     → commence_time
    event.lines[affiliate_id].moneyline:
      moneyline_home                     → home team American price
      moneyline_away                     → away team American price
      date_updated                       → market last_update

    Odds API target shape per event
    --------------------------------
    {
      "home_team":     str,
      "away_team":     str,
      "commence_time": str,          # ISO-8601
      "bookmakers": [{
        "key":         "rundown:<affiliate_id>",
        "last_update": str | None,
        "markets": [{
          "key":         "h2h",
          "last_update": str | None,
          "outcomes": [
            {"name": home_team, "price": moneyline_home},
            {"name": away_team, "price": moneyline_away},
          ]
        }]
      }]
    }

    Only affiliates that supply *both* home and away prices are included.
    Partially-filled affiliates are dropped rather than fabricating a price.
    Events with fewer than 2 teams_normalized entries are skipped entirely.
    """
    normalized = []
    for ev in (rundown_events or []):
        teams = ev.get("teams_normalized") or []
        if len(teams) < 2:
            continue
        home_team = (teams[0].get("name") or "").strip()
        away_team = (teams[-1].get("name") or "").strip()
        if not home_team or not away_team:
            continue

        bookmakers = []
        for aff_id, line in (ev.get("lines") or {}).items():
            ml = (line or {}).get("moneyline") or {}
            home_price = ml.get("moneyline_home")
            away_price = ml.get("moneyline_away")
            if home_price is None or away_price is None:
                continue
            last_update = ml.get("date_updated")
            bookmakers.append({
                "key":         f"rundown:{aff_id}",
                "last_update": last_update,
                "markets": [{
                    "key":         "h2h",
                    "last_update": last_update,
                    "outcomes": [
                        {"name": home_team, "price": home_price},
                        {"name": away_team, "price": away_price},
                    ],
                }],
            })

        if not bookmakers:
            continue

        # TheRundown's normalized payload does not consistently expose an
        # upstream event ID.  Moneyline discovery requires a stable event
        # identity, so retain any provider ID first and otherwise derive an
        # auditable deterministic source identity from immutable event facts.
        provider_event_id = (
            ev.get("id") or ev.get("event_id") or ev.get("eventId")
            or ev.get("game_id") or ev.get("gameId")
        )
        if provider_event_id is None:
            identity = "\x1f".join((
                str(ev.get("event_date") or ""),
                home_team,
                away_team,
            ))
            provider_event_id = (
                "rundown:"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
            )
        normalized.append({
            "id":            str(provider_event_id),
            "home_team":     home_team,
            "away_team":     away_team,
            "commence_time": ev.get("event_date", ""),
            "bookmakers":    bookmakers,
        })
    return normalized


def get_sports():
    data, status = _get("/sports")
    return data or [], status


def get_events(sport_key):
    """Return today's events for a sport key."""
    data, status = _get(f"/sports/{sport_key}/events", {
        "dateFormat": "iso",
        "commenceTimeFrom": datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z"),
        "commenceTimeTo":   datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59Z"),
    })
    return data or [], status


def get_h2h_odds(sport_key):
    """
    Return today's moneyline (h2h) odds across all events for a sport key
    in one call — used by kalshi_engine.llp_bridge.consensus_odds for the
    no-vig sportsbook consensus gate. Read-only; no order placement.
    Returns (events_list, status_string).

    Failover chain (h2h only)
    --------------------------
    1. The Odds API (primary) — native bookmakers/markets/outcomes shape.
    2. TheRundown (on 429 or 401 only) — response normalized via
       _normalize_rundown_to_h2h_events() into the same shape, so every
       downstream consumer works without modification.

    Transient errors (timeout, 5xx, network failures) are NOT retried via
    TheRundown — those conditions typically affect both providers and burning
    an extra network call only adds latency with no benefit.

    Status strings returned:
      "AVAILABLE (remaining=N)"       — Odds API primary succeeded
      "FALLBACK_RUNDOWN:AVAILABLE (N events)"   — TheRundown served data
      "FALLBACK_RUNDOWN:NOT_CALLED: ..." / "FALLBACK_RUNDOWN:FAILED: ..."
                                      — both providers failed; caller sees detail
      "FAILED: ..."                   — primary failed; no failover attempted
    """
    data, status = _get(f"/sports/{sport_key}/odds", {
        "regions":    "us",
        "markets":    "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
    })
    if data is not None:
        return data, status

    # Failover: quota exhausted or invalid key — try TheRundown.
    if status in _RUNDOWN_FAILOVER_STATUSES:
        sport_name = _SPORT_KEY_TO_NAME.get(sport_key)
        if sport_name:
            # Deferred import: avoids any circular-import risk at module load
            # time and keeps the hot path (primary success) allocation-free.
            from services import rundown as _rundown  # noqa: PLC0415
            rd_events, rd_status = _rundown.get_events_for_sport(sport_name)
            normalized = _normalize_rundown_to_h2h_events(rd_events)
            n = len(normalized)
            if n > 0:
                return normalized, f"FALLBACK_RUNDOWN:{rd_status} ({n} events)"
            # TheRundown responded but yielded no normalizable events.
            return [], f"FALLBACK_RUNDOWN:{rd_status} (0 events; primary={status})"
        # sport_key not in our reverse map — nothing to fall back to.
        return [], status

    # Non-failover failure (timeout, 5xx, etc.) — propagate as-is.
    return [], status


def get_player_props(sport_key, event_id, markets):
    """Return player prop odds for a specific event + market list."""
    data, status = _get(f"/sports/{sport_key}/events/{event_id}/odds", {
        "regions":   "us",
        "markets":   ",".join(markets),
        "oddsFormat": "american",
        "dateFormat": "iso",
    })
    return data, status


def extract_props_from_event(event_data, sport):
    """
    Parse bookmaker outcomes into flat prop dicts.
    Returns list of:
      {player, prop, side, line, bookmaker, price, sport, game_date, home_team, away_team}
    """
    if not event_data:
        return []
    props = []
    game_date = (event_data.get("commence_time", "") or "")[:10]
    home = event_data.get("home_team", "")
    away = event_data.get("away_team", "")
    for bm in (event_data.get("bookmakers") or []):
        for market in (bm.get("markets") or []):
            mkey = market.get("key", "")
            for outcome in (market.get("outcomes") or []):
                name = outcome.get("description") or outcome.get("name", "")
                side_raw = outcome.get("name", "")
                side = "MORE" if side_raw.upper() in ("OVER", "MORE") else (
                    "LESS" if side_raw.upper() in ("UNDER", "LESS") else side_raw.upper()
                )
                point = outcome.get("point")
                if point is None:
                    continue
                props.append({
                    "player": name,
                    "prop": mkey,
                    "side": side,
                    "line": float(point),
                    "bookmaker": bm.get("key", ""),
                    "price": outcome.get("price"),
                    "sport": sport,
                    "game_date": game_date,
                    "home_team": home,
                    "away_team": away,
                })
    return props


def fetch_all_props(sport):
    """
    Full pipeline for one sport:
    1. Get today's events
    2. For each event, fetch player prop markets
    3. Parse into flat prop list
    Returns (props_list, status_dict)
    """
    sport_key = SPORT_KEYS.get(sport)
    if not sport_key:
        return [], {
            "events": "NOT_CALLED: unknown sport",
            "props": "NOT_CALLED",
            "coverage_props": "NOT_CALLED: unknown sport",
            "event_count": 0,
            "event_prop_statuses": [],
            "event_prop_failure_count": 0,
        }

    events, ev_status = get_events(sport_key)
    if not events:
        return [], {
            "events": ev_status,
            "props": "NOT_CALLED: no events",
            "coverage_props": "NOT_CALLED: no events",
            "event_count": 0,
            "event_prop_statuses": [],
            "event_prop_failure_count": 0,
        }

    markets = PLAYER_PROP_MARKETS.get(sport, [])
    if not markets:
        return [], {
            "events": ev_status,
            "props": "NOT_CALLED: no markets defined",
            "coverage_props": "NOT_CALLED: no markets defined",
            "event_count": len(events),
            "event_prop_statuses": [],
            "event_prop_failure_count": 0,
        }

    all_props = []
    prop_status = "NOT_RETRIEVED"
    event_prop_statuses = []
    # Coverage is mandatory for every current-slate event.  Do not silently
    # truncate a large slate: per-event statuses below make any failed
    # acquisition visible to daily-scan integrity.
    for event in events:
        event_id = event.get("id")
        if not event_id:
            event_prop_statuses.append({
                "event_id": None,
                "status": "FAILED: missing event_id",
                "inventory_count": 0,
            })
            continue
        data, p_status = get_player_props(sport_key, event_id, markets)
        prop_status = p_status
        extracted = extract_props_from_event(data, sport) if data else []
        event_prop_statuses.append({
            "event_id": event_id,
            "status": p_status,
            "inventory_count": len(extracted),
        })
        if data:
            all_props.extend(extracted)

    attempted = event_prop_statuses
    failed = [
        item for item in attempted
        if "FAILED" in str(item.get("status") or "").upper()
        or "PARTIAL" in str(item.get("status") or "").upper()
    ]
    if failed and len(failed) < len(attempted):
        coverage_props_status = (
            f"PARTIAL: {len(failed)}/{len(attempted)} event prop fetches failed"
        )
    elif failed:
        coverage_props_status = (
            f"FAILED: {len(failed)}/{len(attempted)} event prop fetches failed"
        )
    elif attempted:
        coverage_props_status = (
            f"AVAILABLE: {len(attempted)}/{len(attempted)} event prop fetches completed"
        )
    else:
        coverage_props_status = "FAILED: no event prop fetches attempted"

    return all_props, {
        "events": ev_status,
        # Preserve the legacy last-event status used by existing scoring gates.
        "props": prop_status,
        # Integrity uses the complete per-event aggregate instead.
        "coverage_props": coverage_props_status,
        "event_count": len(events),
        "event_prop_statuses": event_prop_statuses,
        "event_prop_failure_count": len(failed),
    }
