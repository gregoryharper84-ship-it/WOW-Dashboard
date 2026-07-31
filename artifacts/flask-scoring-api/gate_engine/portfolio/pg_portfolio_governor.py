"""
gate_engine/portfolio/pg_portfolio_governor.py  —  PATCH-PORTFOLIO-002
PostgreSQL-backed Cross-Slip Exposure Governor

Promotes the in-memory PortfolioExposureGovernor (Stage 1) to a DB-backed
implementation that catches duplicate exposure across separate /gate-engine/run
calls, not only within a single request.

Architecture
------------
Two tables:

  wow_portfolio_dedup       — lightweight dedup sentinel; holds one row per
                              (session_id, dedup_key) where dedup_key is either
                              "mktf:{mktfamily_key}" or "thesis:{thesis_key}".
                              SELECT … FOR UPDATE on this table prevents races.

  wow_portfolio_exposure_log — full audit log of every check_and_register()
                              call (pass or fail) with all metadata preserved.

Rollover / slate expiry
-----------------------
All queries include AND slate_date = :current_slate_date.  Prior-date rows
remain in the DB for audit but are invisible to the dedup check — a new
slate begins clean without a purge step.

Race safety
-----------
_atomic_check_and_register() runs inside a single transaction:
  1. UPSERT both dedup sentinels (count=0 if new).
  2. SELECT … FOR UPDATE to acquire row-level locks.
  3. Inspect counts; compute blocks.
  4. If pass: UPDATE count+1 for both keys.
  5. COMMIT.
  6. INSERT into audit log (outside the locked window — non-fatal if it fails).

Fail-closed
-----------
Any exception during the DB interaction stamps the row with
SESSION_LEDGER_UNAVAILABLE and sets passed=False, mirroring PgSessionLedger.

can_execute=False is unconditional.
"""
from __future__ import annotations

import os
from datetime import date as _date_type
from typing import Any

can_execute = False

# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

LABEL_DUPLICATE_PLAYER = "REJECT_DUPLICATE_PLAYER_EXPOSURE"
LABEL_DUPLICATE_THESIS = "REJECT_DUPLICATE_THESIS"
LABEL_CROSS_SLIP_CONC  = "REJECT_CROSS_SLIP_CONCENTRATION"
LABEL_SESSION_ID_MISS  = "RUN_INVALID_SESSION_ID_MISSING"
LABEL_LEDGER_UNAVAIL   = "SESSION_LEDGER_UNAVAILABLE"

# ---------------------------------------------------------------------------
# Limits (mirrors Stage 1 defaults)
# ---------------------------------------------------------------------------

MAX_MKTFAMILY: int = 1
MAX_THESIS:    int = 1

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS wow_portfolio_dedup (
    session_id  TEXT     NOT NULL,
    dedup_key   TEXT     NOT NULL,
    count       INTEGER  NOT NULL DEFAULT 0,
    slate_date  DATE     NOT NULL DEFAULT CURRENT_DATE,
    PRIMARY KEY (session_id, dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_wpd_slate
    ON wow_portfolio_dedup (slate_date);
CREATE INDEX IF NOT EXISTS idx_wpd_session
    ON wow_portfolio_dedup (session_id, slate_date);

CREATE TABLE IF NOT EXISTS wow_portfolio_exposure_log (
    id               BIGSERIAL    PRIMARY KEY,
    session_id       TEXT         NOT NULL,
    research_run_id  TEXT         NOT NULL DEFAULT '',
    slate_date       DATE         NOT NULL DEFAULT CURRENT_DATE,
    mktfamily_key    TEXT         NOT NULL,
    thesis_key       TEXT         NOT NULL,
    player           TEXT         NOT NULL DEFAULT '',
    stat_family      TEXT         NOT NULL DEFAULT '',
    direction        TEXT         NOT NULL DEFAULT '',
    market_line      NUMERIC,
    distribution_key TEXT         NOT NULL DEFAULT '',
    decision_label   TEXT         NOT NULL DEFAULT 'REGISTERED',
    blockers         JSONB        NOT NULL DEFAULT '[]',
    source_ts        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wpel_session
    ON wow_portfolio_exposure_log (session_id, slate_date);
"""


def ensure_portfolio_tables_exist(conn_string: str | None = None) -> None:
    """Create wow_portfolio_dedup and wow_portfolio_exposure_log if they don't exist."""
    import psycopg2
    url = conn_string or os.environ.get("DATABASE_URL", "")
    if not url:
        return
    with psycopg2.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()


# ---------------------------------------------------------------------------
# Key helpers  (same normalisation as Stage 1)
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return (s or "").lower().strip().replace("+", " ").replace("-", " ").replace("_", " ")


def _make_keys(row: dict[str, Any]) -> tuple[str, str]:
    """
    Return (mktfamily_key, thesis_key) for a row.

    Both keys include direction so that opposing bets (MORE vs LESS) on the
    same player+stat are treated as distinct exposures and are NOT blocked by
    each other.  Only same-direction alternate lines (e.g. PRA 19.5 MORE and
    PRA 22.5 MORE on the same player) share a key and are blocked.
    """
    player    = _norm(row.get("player") or row.get("player_name") or "UNKNOWN")
    stat      = _norm(
        row.get("prop_type") or row.get("prop") or
        row.get("stat_type") or row.get("stat_family") or ""
    )
    direction = (row.get("direction") or row.get("side") or "").upper().strip()

    mktfamily_key = f"{player}|{stat}|{direction}"
    thesis_key    = f"{player}|{stat}|{direction}"
    return mktfamily_key, thesis_key


def _extract_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "player":      (row.get("player") or row.get("player_name") or "").lower(),
        "stat_family": _norm(row.get("prop_type") or row.get("stat_type") or ""),
        "direction":   (row.get("direction") or row.get("side") or "").upper().strip(),
        "market_line": row.get("line") or row.get("market_line"),
        "event":       (row.get("game") or row.get("event") or ""),
    }


# ---------------------------------------------------------------------------
# PgPortfolioGovernor
# ---------------------------------------------------------------------------

class PgPortfolioGovernor:
    """
    DB-backed Cross-Slip Exposure Governor.

    Detects duplicate mktfamily or thesis exposure across separate
    /gate-engine/run calls in the same session.  Atomic via SELECT FOR UPDATE
    within a single transaction.  Fail-closed on any DB error.
    """

    def __init__(
        self,
        session_id:      str,
        research_run_id: str = "",
        slate_date:      "_date_type | None" = None,
        conn_string:     str | None = None,
        max_mktfamily:   int = MAX_MKTFAMILY,
        max_thesis:      int = MAX_THESIS,
    ) -> None:
        self.session_id      = session_id
        self.research_run_id = research_run_id
        self.slate_date      = slate_date or _date_type.today()
        self._conn_string    = conn_string or os.environ.get("DATABASE_URL", "")
        self.max_mktfamily   = max_mktfamily
        self.max_thesis      = max_thesis

    # ------------------------------------------------------------------
    # Public interface (mirrors PortfolioExposureGovernor)
    # ------------------------------------------------------------------

    def check_and_register(self, row: dict[str, Any]) -> dict[str, Any]:
        """
        Atomically check cross-slip exposure limits and register the row if
        it passes.  Stamps row["gates"]["portfolio_exposure"] with the result.

        Fail-closed: any DB error → SESSION_LEDGER_UNAVAILABLE blocker.
        """
        row.setdefault("gates",    {})
        row.setdefault("blockers", [])
        row["can_execute"] = False

        # Require non-empty session_id
        if not self.session_id:
            tag = f"{LABEL_SESSION_ID_MISS}:session_id_empty"
            row["blockers"].append(tag)
            row["gates"]["portfolio_exposure"] = {
                "passed":    False,
                "blocks":    [tag],
                "backend":   "postgres",
                "can_execute": False,
            }
            return row

        mktfamily_key, thesis_key = _make_keys(row)
        meta = _extract_metadata(row)

        try:
            blocks, counts = self._atomic_check_and_register(
                mktfamily_key, thesis_key
            )
        except Exception as exc:
            err_tag = f"{LABEL_LEDGER_UNAVAIL}:{type(exc).__name__}:{str(exc)[:80]}"
            row["blockers"].append(err_tag)
            row["gates"]["portfolio_exposure"] = {
                "passed":     False,
                "blocks":     [err_tag],
                "registered": False,
                "db_error":   str(exc)[:200],
                "session_id": self.session_id,
                "backend":    "postgres",
                "can_execute": False,
            }
            return row

        passed = len(blocks) == 0

        # Non-fatal: write audit log outside the locked transaction
        try:
            self._log_exposure(
                mktfamily_key=mktfamily_key,
                thesis_key=thesis_key,
                distribution_key=mktfamily_key,
                meta=meta,
                blocks=blocks,
                decision_label="REGISTERED" if passed else (
                    LABEL_CROSS_SLIP_CONC if any(LABEL_CROSS_SLIP_CONC in b for b in blocks)
                    else LABEL_DUPLICATE_THESIS
                ),
            )
        except Exception:
            pass  # audit log failure is non-fatal

        row["gates"]["portfolio_exposure"] = {
            "passed":          passed,
            "blocks":          blocks,
            "mktfamily_key":   mktfamily_key,
            "thesis_key":      thesis_key,
            "mktfamily_count": counts.get(f"mktf:{mktfamily_key}", 0),
            "thesis_count":    counts.get(f"thesis:{thesis_key}", 0),
            "session_id":      self.session_id,
            "research_run_id": self.research_run_id,
            "slate_date":      str(self.slate_date),
            "backend":         "postgres",
            "can_execute":     False,
        }

        if not passed:
            row["blockers"].extend(blocks)
            if any(LABEL_CROSS_SLIP_CONC in b for b in blocks):
                row["terminal_label"] = LABEL_CROSS_SLIP_CONC
            else:
                row["terminal_label"] = LABEL_DUPLICATE_THESIS

        return row

    def snapshot(self) -> dict[str, Any]:
        """
        Return current session exposure from the DB (read-only).
        Returns both raw dedup counts and the audit log summary.
        """
        import psycopg2

        if not self.session_id:
            return {
                "session_id": self.session_id,
                "error":      LABEL_SESSION_ID_MISS,
                "can_execute": False,
            }

        try:
            with psycopg2.connect(self._conn_string) as conn:
                with conn.cursor() as cur:
                    # Dedup sentinel counts
                    cur.execute(
                        "SELECT dedup_key, count "
                        "FROM wow_portfolio_dedup "
                        "WHERE session_id = %s AND slate_date = %s",
                        (self.session_id, self.slate_date),
                    )
                    dedup_rows = cur.fetchall()

                    # Audit log (latest 50 entries)
                    cur.execute(
                        "SELECT mktfamily_key, thesis_key, player, stat_family, "
                        "       direction, market_line, decision_label, blockers, source_ts "
                        "FROM wow_portfolio_exposure_log "
                        "WHERE session_id = %s AND slate_date = %s "
                        "ORDER BY source_ts DESC LIMIT 50",
                        (self.session_id, self.slate_date),
                    )
                    log_rows = cur.fetchall()

            mktf_seen    = {}
            thesis_seen  = {}
            for key, count in dedup_rows:
                if key.startswith("mktf:"):
                    mktf_seen[key[5:]] = count
                elif key.startswith("thesis:"):
                    thesis_seen[key[7:]] = count

            return {
                "session_id":      self.session_id,
                "research_run_id": self.research_run_id,
                "slate_date":      str(self.slate_date),
                "backend":         "postgres",
                "mktfamily_seen":  mktf_seen,
                "thesis_seen":     thesis_seen,
                "max_mktfamily":   self.max_mktfamily,
                "max_thesis":      self.max_thesis,
                "log":             [
                    {
                        "mktfamily_key":   r[0],
                        "thesis_key":      r[1],
                        "player":          r[2],
                        "stat_family":     r[3],
                        "direction":       r[4],
                        "market_line":     float(r[5]) if r[5] is not None else None,
                        "decision_label":  r[6],
                        "blockers":        r[7],
                        "source_ts":       r[8].isoformat() if r[8] else None,
                    }
                    for r in log_rows
                ],
                "can_execute": False,
            }
        except Exception as exc:
            return {
                "session_id": self.session_id,
                "error":      str(exc)[:200],
                "can_execute": False,
            }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _atomic_check_and_register(
        self,
        mktfamily_key: str,
        thesis_key:    str,
    ) -> tuple[list[str], dict[str, str]]:
        """
        Opens a transaction, locks the two dedup rows for this session with
        SELECT … FOR UPDATE, checks limits, and either blocks or increments.

        Returns (blocks, counts_before_increment).

        NOTE: slate_date is an application-controlled date value (not user
        input), used directly in parameterised queries.
        """
        import psycopg2

        dedup_mktf   = f"mktf:{mktfamily_key}"
        dedup_thesis = f"thesis:{thesis_key}"
        both_keys    = [dedup_mktf, dedup_thesis]

        with psycopg2.connect(self._conn_string) as conn:
            with conn.cursor() as cur:

                # 1. Ensure sentinel rows exist (count=0 if new)
                for dk in both_keys:
                    cur.execute(
                        """
                        INSERT INTO wow_portfolio_dedup
                            (session_id, dedup_key, count, slate_date)
                        VALUES
                            (%s, %s, 0, %s)
                        ON CONFLICT (session_id, dedup_key) DO NOTHING
                        """,
                        (self.session_id, dk, self.slate_date),
                    )

                # 2. Lock and read current counts for this slate date
                cur.execute(
                    "SELECT dedup_key, count "
                    "FROM wow_portfolio_dedup "
                    "WHERE session_id = %s "
                    "  AND dedup_key = ANY(%s) "
                    "  AND slate_date = %s "
                    "FOR UPDATE",
                    (self.session_id, both_keys, self.slate_date),
                )
                db_rows = cur.fetchall()
                counts: dict[str, int] = {r[0]: r[1] for r in db_rows}

                mktf_count   = counts.get(dedup_mktf,   0)
                thesis_count = counts.get(dedup_thesis,  0)

                blocks: list[str] = []

                # Market-family check first (catches alternate-line exposure)
                if mktf_count >= self.max_mktfamily:
                    blocks.append(
                        f"{LABEL_CROSS_SLIP_CONC}"
                        f":alternate_line_same_direction:{mktfamily_key}"
                        f":{mktf_count + 1}x"
                    )
                elif thesis_count >= self.max_thesis:
                    # Same direction duplicate
                    blocks.append(
                        f"{LABEL_DUPLICATE_THESIS}:{thesis_key}:{thesis_count + 1}x"
                    )

                # 3. Increment both if pass
                if not blocks:
                    for dk in both_keys:
                        cur.execute(
                            """
                            UPDATE wow_portfolio_dedup
                               SET count = count + 1
                             WHERE session_id = %s AND dedup_key = %s AND slate_date = %s
                            """,
                            (self.session_id, dk, self.slate_date),
                        )

            conn.commit()

        return blocks, {dedup_mktf: mktf_count, dedup_thesis: thesis_count}

    def _log_exposure(
        self,
        *,
        mktfamily_key:   str,
        thesis_key:      str,
        distribution_key: str,
        meta:            dict[str, Any],
        blocks:          list[str],
        decision_label:  str,
    ) -> None:
        """Write one row to wow_portfolio_exposure_log (non-fatal if it fails)."""
        import psycopg2
        import json as _json

        with psycopg2.connect(self._conn_string) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO wow_portfolio_exposure_log
                        (session_id, research_run_id, slate_date,
                         mktfamily_key, thesis_key, player, stat_family,
                         direction, market_line, distribution_key,
                         decision_label, blockers)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        self.session_id,
                        self.research_run_id,
                        self.slate_date,
                        mktfamily_key,
                        thesis_key,
                        meta.get("player", ""),
                        meta.get("stat_family", ""),
                        meta.get("direction", ""),
                        meta.get("market_line"),
                        distribution_key,
                        decision_label,
                        _json.dumps(blocks),
                    ),
                )
            conn.commit()
