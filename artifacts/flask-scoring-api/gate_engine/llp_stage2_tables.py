"""
gate_engine/llp_stage2_tables.py
Stage 2 — Item 7: Required database tables

Creates (idempotently) all 7 Stage 2 required tables:
  1. llp_research_runs       — run-level metadata and governance hash
  2. llp_events              — canonical events with event_key
  3. llp_event_candidates    — per-candidate probability schema + rank_eligible
  4. llp_event_decisions     — one final decision per event_key
  5. llp_event_settlements   — settlement outcomes with brier, log_loss, calibration_bucket
  6. llp_calibration_ledger  — unified cross-sport calibration ledger (replaces JSONL)
  7. llp_source_snapshots    — source data snapshots keyed by source_snapshot_id

Call ensure_all_tables() once at application startup.
Individual ensure_X() functions are idempotent and safe to call repeatedly.

IMPORTANT: can_execute is always False.
  These tables record research and calibration data only.
  No table here may be used to place live orders or trades.
"""
from __future__ import annotations

import os
from typing import Any

# ── Safety constants ──────────────────────────────────────────────────────────
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
CAN_EXECUTE    = False


# ─────────────────────────────────────────────────────────────────────────────
# DDL definitions
# ─────────────────────────────────────────────────────────────────────────────

_DDL_LLP_RESEARCH_RUNS = """
CREATE TABLE IF NOT EXISTS llp_research_runs (
    id                      BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id                  TEXT NOT NULL UNIQUE,
    session_id              TEXT,
    research_run_id         TEXT,
    governance_hash         TEXT,
    expected_governance_hash TEXT,
    as_of                   TIMESTAMPTZ,
    target_date             DATE,
    sport                   TEXT,
    board_type              TEXT,
    input_row_count         INTEGER,
    output_row_count        INTEGER,
    approved_count          INTEGER,
    watch_count             INTEGER,
    reject_count            INTEGER,
    run_valid               BOOLEAN,
    invalidation_code       TEXT,
    run_status              TEXT DEFAULT 'COMPLETE',
    -- Safety flags (unconditional)
    can_execute             BOOLEAN NOT NULL DEFAULT FALSE,
    execution_rule          TEXT    NOT NULL DEFAULT 'DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS',
    notes                   TEXT
);
CREATE INDEX IF NOT EXISTS llp_research_runs_run_id_idx    ON llp_research_runs (run_id);
CREATE INDEX IF NOT EXISTS llp_research_runs_created_idx   ON llp_research_runs (created_at DESC);
CREATE INDEX IF NOT EXISTS llp_research_runs_target_date_idx ON llp_research_runs (target_date);
"""

_DDL_LLP_EVENTS = """
CREATE TABLE IF NOT EXISTS llp_events (
    id                      BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Canonical identity (Stage 2 Item 1)
    event_key               TEXT NOT NULL,
    league                  TEXT,
    official_event_id       TEXT,
    scheduled_start_utc     TIMESTAMPTZ,
    participant_home        TEXT,
    participant_away        TEXT,
    participants_json       JSONB,
    settlement_market       TEXT,
    -- Status
    event_status            TEXT DEFAULT 'SCHEDULED',  -- SCHEDULED/POSTPONED/IN_PROGRESS/COMPLETED/CANCELLED
    can_score               BOOLEAN DEFAULT TRUE,
    status_block_reason     TEXT,
    -- Slate matching
    target_date             DATE,
    slate_date_valid        BOOLEAN,
    -- Source
    source                  TEXT,
    espn_event_id           TEXT,
    raw_meta                JSONB
);
CREATE UNIQUE INDEX IF NOT EXISTS llp_events_event_key_date_idx
    ON llp_events (event_key, target_date);
CREATE INDEX IF NOT EXISTS llp_events_scheduled_idx
    ON llp_events (scheduled_start_utc);
CREATE INDEX IF NOT EXISTS llp_events_league_idx
    ON llp_events (league);
"""

_DDL_LLP_EVENT_CANDIDATES = """
CREATE TABLE IF NOT EXISTS llp_event_candidates (
    id                      BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id                  TEXT NOT NULL REFERENCES llp_research_runs(run_id) ON DELETE CASCADE,
    event_key               TEXT NOT NULL,
    -- Identity
    player                  TEXT,
    prop_type               TEXT,
    side                    TEXT,
    line                    NUMERIC,
    sport                   TEXT,
    market                  TEXT,
    -- Stage 2 probability schema (Item 3) — all required before rank_eligible=TRUE
    raw_probability         NUMERIC,
    calibrated_probability  NUMERIC,
    lower_bound             NUMERIC,
    upper_bound             NUMERIC,
    model_timestamp         TIMESTAMPTZ,
    source_snapshot_id      TEXT REFERENCES llp_source_snapshots(snapshot_id) ON DELETE SET NULL,
    calibration_method      TEXT,
    -- Derived
    rank_eligible           BOOLEAN NOT NULL DEFAULT FALSE,
    probability_schema_complete BOOLEAN NOT NULL DEFAULT FALSE,
    -- Existing probability fields (kept for backward compat)
    model_probability       NUMERIC,
    no_vig_probability      NUMERIC,
    edge                    NUMERIC,
    -- Label
    terminal_label          TEXT,
    final_label             TEXT,
    -- Material staleness (Item 4)
    latest_material_update_at TIMESTAMPTZ,
    material_staleness_seconds NUMERIC,
    is_stale                BOOLEAN DEFAULT FALSE,
    -- Safety
    can_execute             BOOLEAN NOT NULL DEFAULT FALSE,
    execution_rule          TEXT    NOT NULL DEFAULT 'DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS',
    -- Raw
    raw_row                 JSONB
);
CREATE INDEX IF NOT EXISTS llp_event_candidates_run_id_idx
    ON llp_event_candidates (run_id);
CREATE INDEX IF NOT EXISTS llp_event_candidates_event_key_idx
    ON llp_event_candidates (event_key);
CREATE INDEX IF NOT EXISTS llp_event_candidates_rank_eligible_idx
    ON llp_event_candidates (rank_eligible) WHERE rank_eligible = TRUE;
"""
# Note: FK to llp_source_snapshots added via ALTER after both tables exist (order-safe)

_DDL_LLP_EVENT_DECISIONS = """
CREATE TABLE IF NOT EXISTS llp_event_decisions (
    id                      BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id                  TEXT NOT NULL REFERENCES llp_research_runs(run_id) ON DELETE CASCADE,
    event_key               TEXT NOT NULL,
    -- Exactly one decision per event_key per run
    selected_side           TEXT,
    selected_candidate_id   BIGINT,
    final_label             TEXT,
    terminal_label          TEXT,
    -- Mutex result
    mutex_passed            BOOLEAN DEFAULT TRUE,
    opposing_sides_conflict BOOLEAN DEFAULT FALSE,
    mutex_invalidation_code TEXT,
    -- Stake / confidence
    stake_tier              TEXT,
    recommended_stake       NUMERIC,
    max_allowed_stake       NUMERIC,
    confidence_tier         TEXT,
    -- Safety
    can_execute             BOOLEAN NOT NULL DEFAULT FALSE,
    execution_rule          TEXT    NOT NULL DEFAULT 'DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS',
    notes                   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS llp_event_decisions_run_event_idx
    ON llp_event_decisions (run_id, event_key);
CREATE INDEX IF NOT EXISTS llp_event_decisions_event_key_idx
    ON llp_event_decisions (event_key);
"""

_DDL_LLP_EVENT_SETTLEMENTS = """
CREATE TABLE IF NOT EXISTS llp_event_settlements (
    id                      BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at              TIMESTAMPTZ,
    event_key               TEXT NOT NULL,
    run_id                  TEXT,
    decision_id             BIGINT REFERENCES llp_event_decisions(id) ON DELETE SET NULL,
    -- What was graded (ONLY the selected side — never both sides)
    selected_side           TEXT NOT NULL,
    official_event_result   TEXT,
    selected_side_result    TEXT,   -- WIN / LOSS / PUSH / UNKNOWN
    -- Settlement quality
    settlement_status       TEXT DEFAULT 'OPEN',  -- OPEN / SETTLED / VOID / DATA_UNOBTAINABLE
    settlement_source       TEXT,
    -- Calibration metrics
    model_probability       NUMERIC,
    brier_score             NUMERIC,
    log_loss                NUMERIC,
    calibration_bucket      TEXT,   -- e.g. "52-55%", "55-60%", "60-65%", "65-70%", "70%+"
    -- CLV
    entry_price             NUMERIC,
    closing_price           NUMERIC,
    clv                     NUMERIC,
    -- P&L (paper / research tracking only)
    gross_pnl               NUMERIC,
    net_pnl                 NUMERIC,
    -- Failure tagging (Item 5)
    process_pass_fail       TEXT,   -- PASS / FAIL
    failure_category        TEXT,
    dominant_failure_tag    TEXT,
    -- Unique-event accounting (Item 5) — exactly ONE settlement observation per game
    is_primary_observation  BOOLEAN DEFAULT TRUE,
    duplicate_suppressed    BOOLEAN DEFAULT FALSE,
    -- Safety
    can_execute             BOOLEAN NOT NULL DEFAULT FALSE,
    execution_rule          TEXT    NOT NULL DEFAULT 'DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS',
    notes                   TEXT
);
CREATE INDEX IF NOT EXISTS llp_event_settlements_event_key_idx
    ON llp_event_settlements (event_key);
CREATE INDEX IF NOT EXISTS llp_event_settlements_status_idx
    ON llp_event_settlements (settlement_status);
CREATE INDEX IF NOT EXISTS llp_event_settlements_settled_at_idx
    ON llp_event_settlements (settled_at DESC);
"""

_DDL_LLP_CALIBRATION_LEDGER = """
CREATE TABLE IF NOT EXISTS llp_calibration_ledger (
    id                      BIGSERIAL PRIMARY KEY,
    logged_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Identity
    event_key               TEXT,
    run_id                  TEXT,
    sport                   TEXT,
    league                  TEXT,
    market                  TEXT,
    side                    TEXT,
    -- Prices
    odds                    NUMERIC,
    line                    NUMERIC,
    book                    TEXT,
    close                   NUMERIC,
    opener                  NUMERIC,
    -- Probabilities
    model_probability       NUMERIC,
    no_vig_probability      NUMERIC,
    edge                    NUMERIC,
    -- Stage 2 probability schema fields
    raw_probability         NUMERIC,
    calibrated_probability  NUMERIC,
    lower_bound             NUMERIC,
    upper_bound             NUMERIC,
    model_timestamp         TIMESTAMPTZ,
    source_snapshot_id      TEXT,
    calibration_method      TEXT,
    -- Stake and label
    stake                   NUMERIC,
    final_label             TEXT,
    -- Settlement outcome
    result                  TEXT,   -- WIN / LOSS / PUSH
    roi                     NUMERIC,
    -- Calibration metrics
    brier_score             NUMERIC,
    log_loss                NUMERIC,
    calibration_bucket      TEXT,   -- normalised probability range bucket
    clv                     NUMERIC,
    -- Quality
    failure_tags            TEXT[],
    process_pass_fail       TEXT,
    failure_category        TEXT,
    dominant_failure_tag    TEXT,
    -- Unique-event accounting
    is_primary_observation  BOOLEAN DEFAULT TRUE,
    -- Audit
    brier_bucket            TEXT,   -- backward compat alias for calibration_bucket
    postmortem_note         TEXT,
    -- Safety
    can_execute             BOOLEAN NOT NULL DEFAULT FALSE,
    execution_rule          TEXT    NOT NULL DEFAULT 'DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS'
);
CREATE INDEX IF NOT EXISTS llp_calibration_ledger_logged_at_idx
    ON llp_calibration_ledger (logged_at DESC);
CREATE INDEX IF NOT EXISTS llp_calibration_ledger_event_key_idx
    ON llp_calibration_ledger (event_key);
CREATE INDEX IF NOT EXISTS llp_calibration_ledger_final_label_idx
    ON llp_calibration_ledger (final_label);
CREATE INDEX IF NOT EXISTS llp_calibration_ledger_bucket_idx
    ON llp_calibration_ledger (calibration_bucket);
"""

_DDL_LLP_SOURCE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS llp_source_snapshots (
    id                      BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Unique identifier referenced by candidates
    snapshot_id             TEXT NOT NULL UNIQUE,
    -- Source metadata
    source_name             TEXT NOT NULL,   -- e.g. "odds_api", "espn", "prizepicks"
    source_type             TEXT,            -- live / official / reconstructed / proxy
    fetch_timestamp         TIMESTAMPTZ NOT NULL,
    sport                   TEXT,
    market                  TEXT,
    -- Snapshot data
    raw_payload             JSONB,
    -- Quality
    data_complete           BOOLEAN DEFAULT TRUE,
    missing_fields          TEXT[],
    -- Safety
    can_execute             BOOLEAN NOT NULL DEFAULT FALSE,
    execution_rule          TEXT    NOT NULL DEFAULT 'DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS'
);
CREATE INDEX IF NOT EXISTS llp_source_snapshots_snapshot_id_idx
    ON llp_source_snapshots (snapshot_id);
CREATE INDEX IF NOT EXISTS llp_source_snapshots_source_name_idx
    ON llp_source_snapshots (source_name, fetch_timestamp DESC);
"""

# FK from llp_event_candidates.source_snapshot_id → llp_source_snapshots.snapshot_id
# Added after both tables exist (idempotent via DO block).
_DDL_CANDIDATE_SNAPSHOT_FK = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'llp_event_candidates_source_snapshot_id_fkey'
          AND conrelid = 'llp_event_candidates'::regclass
    ) THEN
        ALTER TABLE llp_event_candidates
            ADD CONSTRAINT llp_event_candidates_source_snapshot_id_fkey
            FOREIGN KEY (source_snapshot_id)
            REFERENCES llp_source_snapshots(snapshot_id)
            ON DELETE SET NULL;
    END IF;
END
$$;
"""


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_conn():
    import psycopg2  # type: ignore
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url, connect_timeout=10)


def _run_ddl(statements: list[str]) -> None:
    """Execute a list of DDL statements in one transaction. Silent on error."""
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        for stmt in statements:
            cur.execute(stmt)
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Individual table helpers
# ─────────────────────────────────────────────────────────────────────────────

def ensure_llp_research_runs() -> None:
    _run_ddl([_DDL_LLP_RESEARCH_RUNS])


def ensure_llp_source_snapshots() -> None:
    _run_ddl([_DDL_LLP_SOURCE_SNAPSHOTS])


def ensure_llp_events() -> None:
    _run_ddl([_DDL_LLP_EVENTS])


def ensure_llp_event_candidates() -> None:
    # Source snapshots must exist first (FK target)
    _run_ddl([_DDL_LLP_EVENT_CANDIDATES])


def ensure_llp_event_decisions() -> None:
    # Research runs must exist first
    _run_ddl([_DDL_LLP_EVENT_DECISIONS])


def ensure_llp_event_settlements() -> None:
    _run_ddl([_DDL_LLP_EVENT_SETTLEMENTS])


def ensure_llp_calibration_ledger() -> None:
    _run_ddl([_DDL_LLP_CALIBRATION_LEDGER])


def ensure_candidate_snapshot_fk() -> None:
    """Add FK from llp_event_candidates → llp_source_snapshots (safe to re-run)."""
    _run_ddl([_DDL_CANDIDATE_SNAPSHOT_FK])


# ─────────────────────────────────────────────────────────────────────────────
# Master bootstrap
# ─────────────────────────────────────────────────────────────────────────────

_TABLES_READY = False

import threading as _threading
_TABLES_LOCK = _threading.Lock()


def ensure_all_tables() -> None:
    """
    Create all 7 Stage 2 tables idempotently.
    Safe to call from multiple gunicorn workers — guarded by a process-local lock
    and a Postgres-level CREATE IF NOT EXISTS / DO-block for cross-worker safety.

    Creation order:
      1. llp_source_snapshots  (referenced by candidates FK)
      2. llp_research_runs     (referenced by candidates + decisions FK)
      3. llp_events
      4. llp_event_candidates
      5. llp_event_decisions
      6. llp_event_settlements
      7. llp_calibration_ledger
      8. candidate → snapshot FK (after both parent tables exist)
    """
    global _TABLES_READY
    if _TABLES_READY:
        return
    with _TABLES_LOCK:
        if _TABLES_READY:
            return
        try:
            conn = _get_conn()
            cur  = conn.cursor()
            for ddl in [
                _DDL_LLP_SOURCE_SNAPSHOTS,
                _DDL_LLP_RESEARCH_RUNS,
                _DDL_LLP_EVENTS,
                _DDL_LLP_EVENT_CANDIDATES,
                _DDL_LLP_EVENT_DECISIONS,
                _DDL_LLP_EVENT_SETTLEMENTS,
                _DDL_LLP_CALIBRATION_LEDGER,
                _DDL_CANDIDATE_SNAPSHOT_FK,
            ]:
                cur.execute(ddl)
            conn.commit()
            cur.close()
            conn.close()
            _TABLES_READY = True
        except Exception:
            pass  # graceful — tables may already exist; individual operations will surface errors


# ─────────────────────────────────────────────────────────────────────────────
# Calibration ledger write / read (replaces ephemeral JSONL in llp_governance)
# ─────────────────────────────────────────────────────────────────────────────

# Probability bucket definitions (consistent with ml_reporting.py)
_CALIBRATION_BUCKETS = [
    (0.52, 0.55, "52-55%"),
    (0.55, 0.60, "55-60%"),
    (0.60, 0.65, "60-65%"),
    (0.65, 0.70, "65-70%"),
    (0.70, 1.01, "70%+"),
]


def probability_to_bucket(prob: float | None) -> str | None:
    if prob is None:
        return None
    for lo, hi, label in _CALIBRATION_BUCKETS:
        if lo <= prob < hi:
            return label
    return None


def compute_log_loss(model_prob: float | None, outcome: int | None) -> float | None:
    """
    Binary log loss for one observation.
      outcome = 1 (WIN) or 0 (LOSS).
      log_loss = -(outcome * log(p) + (1-outcome) * log(1-p))
    Clipped to avoid log(0): p clamped to [1e-7, 1-1e-7].
    Returns None when inputs are invalid.
    """
    import math
    if model_prob is None or outcome is None:
        return None
    try:
        p = float(model_prob)
        o = int(outcome)
        if o not in (0, 1):
            return None
        p = max(1e-7, min(1 - 1e-7, p))
        return round(-(o * math.log(p) + (1 - o) * math.log(1 - p)), 8)
    except (TypeError, ValueError):
        return None


def log_calibration_entry_pg(entry: dict[str, Any]) -> bool:
    """
    Append one calibration entry to llp_calibration_ledger (Postgres).

    Computes calibration_bucket and log_loss from entry fields.
    Returns True on success, False on failure.
    """
    ensure_all_tables()

    prob    = entry.get("model_probability")
    result  = (entry.get("result") or "").upper()
    outcome = 1 if result == "WIN" else (0 if result == "LOSS" else None)

    bucket   = entry.get("calibration_bucket") or probability_to_bucket(prob)
    log_loss = entry.get("log_loss") or compute_log_loss(prob, outcome)

    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute(
            """
            INSERT INTO llp_calibration_ledger (
                event_key, run_id, sport, league, market, side,
                odds, line, book, close, opener,
                model_probability, no_vig_probability, edge,
                raw_probability, calibrated_probability,
                lower_bound, upper_bound, model_timestamp,
                source_snapshot_id, calibration_method,
                stake, final_label, result, roi,
                brier_score, log_loss, calibration_bucket, brier_bucket,
                clv, failure_tags, process_pass_fail, failure_category,
                dominant_failure_tag, is_primary_observation, postmortem_note,
                can_execute, execution_rule
            ) VALUES (
                %s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,
                %s,%s,%s,
                %s,%s,
                %s,%s,%s,
                %s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,
                FALSE, 'DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS'
            )
            """,
            (
                entry.get("event_key"),
                entry.get("run_id"),
                entry.get("sport"),
                entry.get("league"),
                entry.get("market"),
                entry.get("side"),
                entry.get("odds"),
                entry.get("line"),
                entry.get("book"),
                entry.get("close"),
                entry.get("opener"),
                prob,
                entry.get("no_vig_probability"),
                entry.get("edge"),
                entry.get("raw_probability"),
                entry.get("calibrated_probability"),
                entry.get("lower_bound"),
                entry.get("upper_bound"),
                entry.get("model_timestamp"),
                entry.get("source_snapshot_id"),
                entry.get("calibration_method"),
                entry.get("stake"),
                entry.get("final_label"),
                result or None,
                entry.get("roi"),
                entry.get("brier_score"),
                log_loss,
                bucket,
                bucket,   # brier_bucket = calibration_bucket (backward compat)
                entry.get("clv"),
                entry.get("failure_tags") or [],
                entry.get("process_pass_fail"),
                entry.get("failure_category"),
                entry.get("dominant_failure_tag"),
                entry.get("is_primary_observation", True),
                entry.get("postmortem_note"),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception:
        return False


def get_calibration_ledger_pg(limit: int = 200) -> list[dict[str, Any]]:
    """Read the most recent `limit` entries from llp_calibration_ledger."""
    ensure_all_tables()
    try:
        import psycopg2.extras  # type: ignore
        conn = _get_conn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM llp_calibration_ledger "
            "ORDER BY logged_at DESC LIMIT %s",
            (min(limit, 1000),),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        def _safe(v: Any) -> Any:
            if hasattr(v, "isoformat"):
                return v.isoformat()
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return v

        return [{k: _safe(v) for k, v in dict(r).items()} for r in rows]
    except Exception:
        return []
