"""
gate_engine/source_health_monitor.py

Source health aggregation for the WOW dashboard.

Tracks API health across all data providers used in the scoring pipeline:
  - Odds API (sports odds)
  - ESPN (box scores, player data)
  - MLB Stats API
  - nba_api (NBA/WNBA data)
  - BallDontLie (NBA data)
  - Open-Meteo (weather)
  - PrizePicks (board/projections)
  - Sackmann tennis CSV (tennis historical)

Health records are written to wow_source_health_log at each probe.
The dashboard reads the most recent record per source.

Public API
----------
  ensure_table(conn)                     — idempotent DDL
  record_probe(conn, source, status, ...) — write one health event
  read_current_health(conn)              — dict[source → health_entry]
  aggregate_health_summary(conn)        — summary for dashboard
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

can_execute: bool = False

# ---------------------------------------------------------------------------
# Known sources
# ---------------------------------------------------------------------------

SOURCES = {
    "odds_api":       "Odds API (sports odds)",
    "espn":           "ESPN API (box scores/players)",
    "mlb_stats_api":  "MLB Stats API",
    "nba_api":        "nba_api (NBA/WNBA game logs)",
    "balldontlie":    "BallDontLie (NBA)",
    "open_meteo":     "Open-Meteo (weather)",
    "prizepicks":     "PrizePicks (board/projections)",
    "sackmann_csv":   "Sackmann Tennis CSV",
    "kalshi":         "Kalshi (prediction markets)",
    "fbref":          "FBRef (soccer stats)",
}

# Status codes
STATUS_OK      = "OK"
STATUS_DEGRADED = "DEGRADED"
STATUS_DOWN    = "DOWN"
STATUS_UNKNOWN = "UNKNOWN"

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_HEALTH_LOG = """
CREATE TABLE IF NOT EXISTS wow_source_health_log (
    id              SERIAL      PRIMARY KEY,
    source_id       TEXT        NOT NULL,
    status          TEXT        NOT NULL,
    latency_ms      INTEGER,
    http_status     INTEGER,
    error_message   TEXT,
    response_meta   JSONB,
    probed_at       TIMESTAMPTZ DEFAULT NOW()
)
"""

_CREATE_HEALTH_IDX = """
CREATE INDEX IF NOT EXISTS idx_src_health_source_time
ON wow_source_health_log(source_id, probed_at DESC)
"""


def ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_CREATE_HEALTH_LOG)
        cur.execute(_CREATE_HEALTH_IDX)
    conn.commit()


# ---------------------------------------------------------------------------
# Write probe record
# ---------------------------------------------------------------------------

def record_probe(
    conn,
    source_id: str,
    status: str,
    latency_ms: int | None = None,
    http_status: int | None = None,
    error_message: str | None = None,
    response_meta: dict | None = None,
) -> None:
    """
    Write one source health probe result.
    Non-blocking — on error, log and return without raising.
    """
    try:
        sql = """
            INSERT INTO wow_source_health_log
                (source_id, status, latency_ms, http_status, error_message, response_meta)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        with conn.cursor() as cur:
            cur.execute(sql, (
                source_id,
                status,
                latency_ms,
                http_status,
                error_message,
                json.dumps(response_meta or {}),
            ))
        conn.commit()
    except Exception as exc:
        print(f"[source_health_monitor] record_probe error: {exc}", flush=True)


# ---------------------------------------------------------------------------
# Read current health
# ---------------------------------------------------------------------------

def read_current_health(conn) -> dict[str, dict]:
    """
    Return the most recent probe for each source_id.
    Falls back to UNKNOWN for sources not yet probed.
    """
    sql = """
        SELECT DISTINCT ON (source_id)
            source_id, status, latency_ms, http_status,
            error_message, probed_at
        FROM wow_source_health_log
        ORDER BY source_id, probed_at DESC
    """
    rows: dict[str, dict] = {}
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            for r in cur.fetchall():
                d = dict(zip(cols, r))
                if hasattr(d.get("probed_at"), "isoformat"):
                    d["probed_at"] = d["probed_at"].isoformat()
                rows[d["source_id"]] = d
    except Exception as exc:
        print(f"[source_health_monitor] read_current_health error: {exc}", flush=True)

    # Fill unknown for sources not yet probed
    for sid in SOURCES:
        if sid not in rows:
            rows[sid] = {
                "source_id":     sid,
                "status":        STATUS_UNKNOWN,
                "latency_ms":    None,
                "http_status":   None,
                "error_message": "Not yet probed this session",
                "probed_at":     None,
            }
    # Add display name
    for sid, entry in rows.items():
        entry["display_name"] = SOURCES.get(sid, sid)

    return rows


# ---------------------------------------------------------------------------
# Aggregate summary
# ---------------------------------------------------------------------------

def aggregate_health_summary(conn) -> dict:
    """
    Dashboard summary:
      overall_status, n_ok, n_degraded, n_down, n_unknown, sources (list)
    """
    current = read_current_health(conn)
    sources_list = []
    n_ok = n_deg = n_down = n_unk = 0

    for sid, entry in sorted(current.items()):
        s = entry.get("status", STATUS_UNKNOWN)
        sources_list.append({
            "source_id":    sid,
            "display_name": entry.get("display_name", sid),
            "status":       s,
            "latency_ms":   entry.get("latency_ms"),
            "http_status":  entry.get("http_status"),
            "error":        entry.get("error_message"),
            "probed_at":    entry.get("probed_at"),
        })
        if s == STATUS_OK:
            n_ok += 1
        elif s == STATUS_DEGRADED:
            n_deg += 1
        elif s == STATUS_DOWN:
            n_down += 1
        else:
            n_unk += 1

    # Overall status: worst of any source
    if n_down > 0:
        overall = STATUS_DOWN
    elif n_deg > 0:
        overall = STATUS_DEGRADED
    elif n_unk == len(sources_list):
        overall = STATUS_UNKNOWN
    else:
        overall = STATUS_OK

    return {
        "overall_status": overall,
        "n_ok":           n_ok,
        "n_degraded":     n_deg,
        "n_down":         n_down,
        "n_unknown":      n_unk,
        "sources":        sources_list,
        "as_of":          datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Probe helper — call from Flask health-check routes to record state
# ---------------------------------------------------------------------------

def probe_from_health_response(
    conn,
    source_id: str,
    health_dict: dict,
    latency_ms: int | None = None,
) -> None:
    """
    Record a probe result from an existing health-check response dict.
    Extracts status from common shapes (ok, healthy, status fields).
    """
    ok = (
        health_dict.get("ok") is True
        or health_dict.get("healthy") is True
        or health_dict.get("status") in ("ok", "OK", "healthy", "HEALTHY")
    )
    status = STATUS_OK if ok else STATUS_DEGRADED
    err = None if ok else str(health_dict.get("error") or health_dict.get("message") or "")
    record_probe(
        conn,
        source_id=source_id,
        status=status,
        latency_ms=latency_ms,
        error_message=err,
        response_meta=health_dict,
    )
