"""
prizepicks.py  —  PrizePicks projections service
WOW v16 data pipeline

PrizePicks blocks unauthenticated server-side requests with DataDome CAPTCHA.
This module works around that by accepting a valid DataDome session cookie
(grabbed from a browser or the desktop app) and forwarding it on every request.

Cookie storage:
  Cookies are persisted to the `wow_config` Postgres table (key-value) so
  they survive server restarts and are shared across gunicorn workers.
  The user updates the cookie via POST /wow/prizepicks/cookie.

Public API:
  set_cookie(cookie_str)        — persist cookie to DB
  get_cookie()                  — retrieve cookie from DB
  fetch_projections(...)        — GET /projections with stored cookie
  fetch_leagues()               — GET /leagues with stored cookie
  normalize_projections(raw)    — JSON:API → flat dicts
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

BASE_URL = "https://api.prizepicks.com"
TIMEOUT  = 20

# Known league IDs (fetched dynamically via fetch_leagues() for the full list)
KNOWN_LEAGUES: dict[str, int] = {
    "NFL":   2,
    "NBA":   7,
    "MLB":   8,
    "NHL":   9,
    "WNBA":  11,
    "NCAAB": 14,
    "PGA":   147,
    "UFC":   152,
    "NCAAF": 16,
}

_CONFIG_KEY = "pp_datadome_cookie"

# ---------------------------------------------------------------------------
# DB helpers — wow_config key-value table
# ---------------------------------------------------------------------------

_CONFIG_DDL = """
CREATE TABLE IF NOT EXISTS wow_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
"""


def _get_conn():
    import psycopg2  # type: ignore
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url, connect_timeout=10)


def _ensure_config_table() -> None:
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(_CONFIG_DDL)
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def set_cookie(cookie_str: str) -> dict[str, Any]:
    """
    Persist the PrizePicks DataDome cookie to the wow_config table.

    cookie_str — the raw Cookie header value, e.g.:
      "datadome=abc123..." or
      "datadome=abc123...; _prizepicks_session=xyz..."
    """
    _ensure_config_table()
    if not cookie_str or not cookie_str.strip():
        return {"ok": False, "detail": "cookie_str is empty"}
    cookie_str = cookie_str.strip()
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(
            """
            INSERT INTO wow_config (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = NOW()
            RETURNING updated_at
            """,
            (_CONFIG_KEY, cookie_str),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return {
            "ok":         True,
            "updated_at": row[0].isoformat() if row and row[0] else None,
            "cookie_tail": "..." + cookie_str[-12:] if len(cookie_str) > 12 else "(short)",
            "detail":     "PrizePicks cookie stored. It will be used for all /wow/prizepicks/* requests.",
        }
    except Exception as exc:
        return {"ok": False, "detail": f"DB error: {exc}"}


def get_cookie() -> str | None:
    """Retrieve the stored PrizePicks cookie from wow_config."""
    _ensure_config_table()
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("SELECT value FROM wow_config WHERE key = %s", (_CONFIG_KEY,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _headers(cookie_str: str) -> dict[str, str]:
    return {
        "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Safari/537.36",
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin":          "https://app.prizepicks.com",
        "Referer":         "https://app.prizepicks.com/",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie":          cookie_str,
    }


def _fetch(
    path:       str,
    params:     dict[str, Any] | None = None,
    cookie_str: str | None            = None,
) -> tuple[dict | None, str]:
    """
    GET {BASE_URL}{path} using the provided or stored cookie.

    Returns (data_dict_or_None, status_string).
    Status strings: "AVAILABLE" | "FAILED: no_cookie" | "FAILED: captcha" | …
    """
    cookie = cookie_str or get_cookie()
    if not cookie:
        return None, "FAILED: no_cookie — POST /wow/prizepicks/cookie first"

    try:
        r = requests.get(
            f"{BASE_URL}{path}",
            headers=_headers(cookie),
            params=params or {},
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            # DataDome returns a JSON redirect on captcha trigger even with 200
            data = r.json()
            if isinstance(data, dict) and "url" in data and "captcha-delivery" in data.get("url", ""):
                return None, "FAILED: captcha — cookie is expired or invalid; refresh it"
            return data, "AVAILABLE"
        elif r.status_code in (401, 403):
            return None, "FAILED: unauthorized — cookie may have expired"
        elif r.status_code == 429:
            return None, "FAILED: rate_limited"
        else:
            return None, f"FAILED: HTTP {r.status_code}"
    except requests.exceptions.Timeout:
        return None, "FAILED: timeout"
    except Exception as exc:
        return None, f"FAILED: {exc}"


# ---------------------------------------------------------------------------
# Normalization — JSON:API → flat dicts
# ---------------------------------------------------------------------------

def normalize_projections(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Convert PrizePicks JSON:API response into a flat list of projection dicts.

    Each dict contains:
      projection_id, player_name, team, position, league, sport,
      stat_type, line_score, status, start_time, game_description,
      is_promo, odds_type, player_id, image_url
    """
    data_items  = raw.get("data", [])
    included    = raw.get("included", [])

    # Build lookup maps from included
    players: dict[str, dict] = {}
    leagues: dict[str, dict] = {}
    for item in included:
        item_id = item.get("id", "")
        attrs   = item.get("attributes", {})
        if item.get("type") == "new_player":
            players[item_id] = attrs
        elif item.get("type") == "league":
            leagues[item_id] = attrs

    results: list[dict[str, Any]] = []
    for item in data_items:
        if item.get("type") != "projection":
            continue
        attrs = item.get("attributes", {})
        rels  = item.get("relationships", {})

        player_id  = (rels.get("new_player", {}).get("data") or {}).get("id", "")
        league_id  = (rels.get("league", {}).get("data") or {}).get("id", "")

        player = players.get(player_id, {})
        league = leagues.get(league_id, {})

        results.append({
            "projection_id":   item.get("id"),
            "player_id":       player_id,
            "player_name":     player.get("name"),
            "team":            player.get("team"),
            "position":        player.get("position"),
            "image_url":       player.get("image_url"),
            "league":          league.get("name"),
            "league_id":       league_id,
            "sport":           league.get("sport"),
            "stat_type":       attrs.get("stat_type"),
            "line_score":      attrs.get("line_score"),
            "status":          attrs.get("status"),
            "start_time":      attrs.get("start_time"),
            "game_description": attrs.get("description"),
            "is_promo":        attrs.get("is_promo", False),
            "odds_type":       attrs.get("odds_type"),
            "flash_sale_line": attrs.get("flash_sale_line_score"),
            "board_time":      attrs.get("board_time"),
            "pulled_at":       datetime.now(timezone.utc).isoformat(),
        })

    return results


def normalize_leagues(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the /leagues response to a flat list."""
    results = []
    for item in (raw.get("data") or []):
        if item.get("type") != "league":
            continue
        attrs = item.get("attributes", {})
        results.append({
            "league_id":   item.get("id"),
            "name":        attrs.get("name"),
            "sport":       attrs.get("sport"),
            "active":      attrs.get("active", True),
            "projections_count": attrs.get("projections_count"),
        })
    return results


# ---------------------------------------------------------------------------
# Public fetch functions
# ---------------------------------------------------------------------------

def fetch_projections(
    league_id:   int | str | None = None,
    stat_type:   str | None       = None,
    per_page:    int               = 250,
    single_stat: bool              = False,
    cookie_str:  str | None        = None,
) -> tuple[list[dict[str, Any]], str]:
    """
    Fetch and normalize current PrizePicks projections.

    Parameters
    ----------
    league_id   Optional league filter (e.g. 7 for NBA).
    stat_type   Optional stat filter (e.g. "Points").
    per_page    Max results per page (PrizePicks caps at ~250).
    single_stat If True, returns one line per stat type per player.
    cookie_str  Override stored cookie for this call only.

    Returns (projections_list, status_string).
    """
    params: dict[str, Any] = {"per_page": per_page}
    if league_id:
        params["league_id"] = league_id
    if stat_type:
        params["stat_type"] = stat_type
    if single_stat:
        params["single_stat"] = "true"

    data, status = _fetch("/projections", params=params, cookie_str=cookie_str)
    if data is None:
        return [], status

    projections = normalize_projections(data)
    return projections, status


def fetch_leagues(cookie_str: str | None = None) -> tuple[list[dict[str, Any]], str]:
    """Fetch available PrizePicks leagues."""
    data, status = _fetch("/leagues", cookie_str=cookie_str)
    if data is None:
        return [], status
    return normalize_leagues(data), status
