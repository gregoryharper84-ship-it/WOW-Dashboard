"""
prizepicks.py  —  PrizePicks projections service
WOW v16 data pipeline

DataDome (PrizePicks' bot protection) ties cookies to the originating IP.
Server-side fetches using a browser cookie are rejected regardless of TLS
fingerprint impersonation. The correct architecture is:

  Mac script runs locally  →  fetches from PrizePicks (same IP as cookie)
  Mac script POSTs rows    →  POST /wow/prizepicks/projections/ingest
  Server stores rows       →  prizepicks_projections Postgres table
  Server serves rows       →  GET /wow/prizepicks/projections (reads DB)

This module handles:
  • DB table creation + upsert (ingest)
  • Querying stored projections
  • Cookie storage (kept for future proxy/VPN approaches)
  • normalize_projections() — JSON:API → flat dicts (used by Mac script)

Public API:
  set_cookie(cookie_str)          — persist cookie to wow_config
  get_cookie()                    — retrieve stored cookie
  ingest_projections(rows)        — upsert flat projection rows into DB
  query_projections(...)          — read stored projections from DB
  normalize_projections(raw)      — JSON:API → flat dicts (Mac script uses this)
  fetch_leagues_local(cookie_str) — fetch leagues on Mac (not from server)
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_CONFIG_DDL = """
CREATE TABLE IF NOT EXISTS wow_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
)
"""

_PROJECTIONS_DDL = """
CREATE TABLE IF NOT EXISTS prizepicks_projections (
    projection_id   TEXT PRIMARY KEY,
    player_id       TEXT,
    player_name     TEXT,
    team            TEXT,
    position        TEXT,
    image_url       TEXT,
    league          TEXT,
    league_id       TEXT,
    sport           TEXT,
    stat_type       TEXT,
    line_score      NUMERIC,
    status          TEXT,
    start_time      TEXT,
    game_description TEXT,
    is_promo        BOOLEAN DEFAULT FALSE,
    odds_type       TEXT,
    flash_sale_line NUMERIC,
    board_time      TEXT,
    pulled_at       TIMESTAMPTZ,
    ingested_at     TIMESTAMPTZ DEFAULT NOW()
)
"""

_PROJECTIONS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS pp_proj_league_idx   ON prizepicks_projections(league);
CREATE INDEX IF NOT EXISTS pp_proj_stat_idx     ON prizepicks_projections(stat_type);
CREATE INDEX IF NOT EXISTS pp_proj_player_idx   ON prizepicks_projections(player_name);
CREATE INDEX IF NOT EXISTS pp_proj_ingested_idx ON prizepicks_projections(ingested_at DESC);
"""

_CONFIG_KEY = "pp_datadome_cookie"

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


def _get_conn():
    import psycopg2  # type: ignore
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url, connect_timeout=10)


def _ensure_tables() -> None:
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(_CONFIG_DDL)
        cur.execute(_PROJECTIONS_DDL)
        cur.execute(_PROJECTIONS_INDEX_DDL)
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Cookie storage (kept for completeness; server-side fetches are IP-blocked)
# ---------------------------------------------------------------------------

def set_cookie(cookie_str: str) -> dict[str, Any]:
    """Persist a PrizePicks DataDome cookie to wow_config."""
    _ensure_tables()
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
            "cookie_tail": "..." + cookie_str[-12:],
            "detail":     "Cookie stored. Use the Mac pusher script to fetch projections locally and push them here.",
        }
    except Exception as exc:
        return {"ok": False, "detail": f"DB error: {exc}"}


def get_cookie() -> str | None:
    _ensure_tables()
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
# Normalization — JSON:API → flat dicts  (used by Mac script before POST)
# ---------------------------------------------------------------------------

def normalize_projections(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Convert PrizePicks JSON:API response to a flat list of projection dicts.
    Called by the Mac pusher script after a local fetch.
    """
    data_items = raw.get("data", [])
    included   = raw.get("included", [])

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
        attrs  = item.get("attributes", {})
        rels   = item.get("relationships", {})
        pid    = (rels.get("new_player", {}).get("data") or {}).get("id", "")
        lid    = (rels.get("league", {}).get("data") or {}).get("id", "")
        player = players.get(pid, {})
        league = leagues.get(lid, {})

        results.append({
            "projection_id":    item.get("id"),
            "player_id":        pid,
            "player_name":      player.get("name"),
            "team":             player.get("team"),
            "position":         player.get("position"),
            "image_url":        player.get("image_url"),
            "league":           league.get("name"),
            "league_id":        lid,
            "sport":            league.get("sport"),
            "stat_type":        attrs.get("stat_type"),
            "line_score":       attrs.get("line_score"),
            "status":           attrs.get("status"),
            "start_time":       attrs.get("start_time"),
            "game_description": attrs.get("description"),
            "is_promo":         attrs.get("is_promo", False),
            "odds_type":        attrs.get("odds_type"),
            "flash_sale_line":  attrs.get("flash_sale_line_score"),
            "board_time":       attrs.get("board_time"),
            "pulled_at":        datetime.now(timezone.utc).isoformat(),
        })

    return results


def normalize_leagues(raw: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for item in (raw.get("data") or []):
        if item.get("type") != "league":
            continue
        attrs = item.get("attributes", {})
        results.append({
            "league_id":          item.get("id"),
            "name":               attrs.get("name"),
            "sport":              attrs.get("sport"),
            "active":             attrs.get("active", True),
            "projections_count":  attrs.get("projections_count"),
        })
    return results


# ---------------------------------------------------------------------------
# Ingest — Mac script POSTs normalized rows here
# ---------------------------------------------------------------------------

def ingest_projections(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Upsert a list of normalized projection dicts into prizepicks_projections.

    On conflict (same projection_id) the row is fully replaced so line moves
    and status changes are always current.

    Returns {ok, upserted, skipped, detail}.
    """
    _ensure_tables()
    if not rows:
        return {"ok": True, "upserted": 0, "skipped": 0, "detail": "No rows provided."}

    upserted = 0
    skipped  = 0
    errors: list[str] = []

    try:
        conn = _get_conn()
        cur  = conn.cursor()

        for row in rows:
            pid = row.get("projection_id")
            if not pid:
                skipped += 1
                continue
            try:
                pulled_raw = row.get("pulled_at")
                pulled_ts  = None
                if pulled_raw:
                    try:
                        from dateutil import parser as _dp  # type: ignore
                        pulled_ts = _dp.parse(pulled_raw)
                    except Exception:
                        pulled_ts = None

                cur.execute(
                    """
                    INSERT INTO prizepicks_projections (
                        projection_id, player_id, player_name, team, position,
                        image_url, league, league_id, sport,
                        stat_type, line_score, status, start_time,
                        game_description, is_promo, odds_type,
                        flash_sale_line, board_time, pulled_at, ingested_at
                    ) VALUES (
                        %s,%s,%s,%s,%s,
                        %s,%s,%s,%s,
                        %s,%s,%s,%s,
                        %s,%s,%s,
                        %s,%s,%s, NOW()
                    )
                    ON CONFLICT (projection_id) DO UPDATE SET
                        player_name     = EXCLUDED.player_name,
                        team            = EXCLUDED.team,
                        league          = EXCLUDED.league,
                        stat_type       = EXCLUDED.stat_type,
                        line_score      = EXCLUDED.line_score,
                        status          = EXCLUDED.status,
                        start_time      = EXCLUDED.start_time,
                        game_description= EXCLUDED.game_description,
                        is_promo        = EXCLUDED.is_promo,
                        odds_type       = EXCLUDED.odds_type,
                        flash_sale_line = EXCLUDED.flash_sale_line,
                        board_time      = EXCLUDED.board_time,
                        pulled_at       = EXCLUDED.pulled_at,
                        ingested_at     = NOW()
                    """,
                    (
                        pid,
                        row.get("player_id"),
                        row.get("player_name"),
                        row.get("team"),
                        row.get("position"),
                        row.get("image_url"),
                        row.get("league"),
                        str(row.get("league_id", "")),
                        row.get("sport"),
                        row.get("stat_type"),
                        row.get("line_score"),
                        row.get("status"),
                        row.get("start_time"),
                        row.get("game_description"),
                        bool(row.get("is_promo", False)),
                        row.get("odds_type"),
                        row.get("flash_sale_line"),
                        row.get("board_time"),
                        pulled_ts,
                    ),
                )
                upserted += 1
            except Exception as exc:
                skipped += 1
                errors.append(f"{pid}: {exc}")

        conn.commit()
        cur.close()
        conn.close()
        return {
            "ok":       True,
            "upserted": upserted,
            "skipped":  skipped,
            "errors":   errors[:5] if errors else [],
            "detail":   f"Ingested {upserted} projections ({skipped} skipped).",
        }
    except Exception as exc:
        return {"ok": False, "upserted": 0, "skipped": len(rows), "detail": f"DB error: {exc}"}


# ---------------------------------------------------------------------------
# Query — serve stored projections
# ---------------------------------------------------------------------------

def query_projections(
    league:    str | None = None,
    stat_type: str | None = None,
    player:    str | None = None,
    status:    str | None = None,
    limit:     int        = 500,
    since_minutes: int    = 360,   # only rows ingested within this window
) -> tuple[list[dict[str, Any]], str]:
    """
    Read stored projections from DB with optional filters.

    Returns (rows, freshness_note).
    """
    _ensure_tables()
    try:
        import psycopg2.extras  # type: ignore
        conn = _get_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        clauses: list[str] = [f"ingested_at >= NOW() - INTERVAL '{since_minutes} minutes'"]
        params:  list[Any] = []

        if league:
            clauses.append("UPPER(league) = UPPER(%s)")
            params.append(league)
        if stat_type:
            clauses.append("LOWER(stat_type) = LOWER(%s)")
            params.append(stat_type)
        if player:
            clauses.append("LOWER(player_name) LIKE LOWER(%s)")
            params.append(f"%{player}%")
        if status:
            clauses.append("status = %s")
            params.append(status)

        where  = "WHERE " + " AND ".join(clauses)
        params.append(min(limit, 1000))

        cur.execute(
            f"""
            SELECT projection_id, player_id, player_name, team, position,
                   league, league_id, sport, stat_type, line_score, status,
                   start_time, game_description, is_promo, odds_type,
                   flash_sale_line, board_time, pulled_at, ingested_at
            FROM prizepicks_projections
            {where}
            ORDER BY ingested_at DESC, player_name
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()

        # Freshness check — use a plain cursor (not RealDict) for this scalar query
        plain_cur = conn.cursor()
        plain_cur.execute(
            "SELECT MAX(ingested_at) FROM prizepicks_projections "
            f"WHERE ingested_at >= NOW() - INTERVAL '{since_minutes} minutes'"
        )
        latest_row = plain_cur.fetchone()
        plain_cur.close()
        cur.close()
        conn.close()

        def _safe(v: Any) -> Any:
            if hasattr(v, "isoformat"):
                return v.isoformat()
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return v

        result    = [{k: _safe(v) for k, v in dict(r).items()} for r in rows]
        latest_ts = latest_row[0] if latest_row else None
        freshness = latest_ts.isoformat() if latest_ts else None
        return result, freshness or "NO_DATA"
    except Exception as exc:
        return [], f"DB_ERROR: {exc}"


def get_ingest_summary() -> dict[str, Any]:
    """Return a summary of the current board: leagues, counts, last ingest time."""
    _ensure_tables()
    try:
        import psycopg2.extras  # type: ignore
        conn = _get_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT league, COUNT(*) AS count, MAX(ingested_at) AS last_ingested
            FROM prizepicks_projections
            WHERE ingested_at >= NOW() - INTERVAL '24 hours'
            GROUP BY league
            ORDER BY count DESC
            """
        )
        rows = cur.fetchall()

        cur.execute(
            "SELECT COUNT(*) AS total, MAX(ingested_at) AS last_ingested "
            "FROM prizepicks_projections WHERE ingested_at >= NOW() - INTERVAL '24 hours'"
        )
        totals = dict(cur.fetchone() or {})
        cur.close()
        conn.close()

        leagues = []
        for r in rows:
            d = dict(r)
            leagues.append({
                "league":         d["league"],
                "count":          int(d["count"]),
                "last_ingested":  d["last_ingested"].isoformat() if d["last_ingested"] else None,
            })

        last = totals.get("last_ingested")
        return {
            "total_projections": int(totals.get("total") or 0),
            "last_ingested":     last.isoformat() if last else None,
            "leagues":           leagues,
        }
    except Exception as exc:
        return {"error": str(exc)}
