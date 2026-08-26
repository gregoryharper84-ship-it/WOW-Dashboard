"""
validation/outcome_ingestion.py

WOW 1IP Automated Outcome Ingestion.

Finds unresolved 1IP predictions (game_date before today, no outcome attached)
and attaches verified first-inning pitch counts from Baseball Savant.

DESIGN INVARIANTS
-----------------
1. Fail-closed: any identity ambiguity, missing data, or mismatch → typed
   skip. Never guess, synthesize, or mark verified without clear evidence.
2. Idempotent: rows already settled by any means → ALREADY_SETTLED skip.
3. Dry-run default: `dry_run=True` by default so accidental invocation
   without --no-dry-run never writes to the DB.
4. Secrets never logged: DB connection errors omit the connection string.
5. No model/gate logic touched; no scoring endpoints called.
6. can_execute=False unconditional (observational data collection only).

Typed per-row status codes
--------------------------
ATTACHED               — pitch count fetched and outcome written to DB.
DRY_RUN                — would attach; dry_run=True blocked the write.
ALREADY_SETTLED        — idempotent skip; prior outcome matches or differs
                         (conflicting outcomes are separate CONFLICT_ codes).
NO_DATA                — Savant returned no pitches for this pitcher/date.
FETCH_ERROR            — Network/API error fetching Savant data.
AMBIGUOUS_DOUBLEHEADER — Multiple starts on same date; fail-closed.
IDENTITY_MISMATCH      — Returned game_pk group does not match expected date.
INVALID_PITCH_COUNT    — Pitch count is zero or negative (data quality).
GAME_NOT_YET_PLAYED    — game_date is today or in the future.
OUTCOME_ATTACH_ERROR   — Unexpected error from attach_outcome().
DB_UNAVAILABLE         — Cannot connect to the database.
"""
from __future__ import annotations

can_execute = False

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Module-level imports for test patchability.
# (Lazy imports inside functions cannot be patched via patch("module.name").)
from validation.outcome_logger import attach_outcome, OutcomeLogError  # noqa: E402

# ---------------------------------------------------------------------------
# Per-row status codes (typed, exhaustive)
# ---------------------------------------------------------------------------

TYPED_STATUS_DOCS: dict[str, str] = {
    "ATTACHED":               "Pitch count fetched; outcome written to wow_validation_outcome_log.",
    "DRY_RUN":                "Would attach; dry_run=True prevented DB write.",
    "ALREADY_SETTLED":        "Prediction already settled with same or compatible outcome.",
    "NO_DATA":                "Savant returned no first-inning pitches for this pitcher/date.",
    "FETCH_ERROR":            "Network or API error fetching Savant pitch data.",
    "AMBIGUOUS_DOUBLEHEADER": "Pitcher made 2+ starts on this date; cannot identify which game; fail-closed.",
    "IDENTITY_MISMATCH":      "Savant data game_date does not match expected date; fail-closed.",
    "INVALID_PITCH_COUNT":    "Pitch count is 0 or negative; data quality issue.",
    "GAME_NOT_YET_PLAYED":    "game_date is today or in the future; outcome not available.",
    "OUTCOME_ATTACH_ERROR":   "Unexpected error from validation.outcome_logger.attach_outcome().",
    "DB_UNAVAILABLE":         "Cannot open DB connection; check DATABASE_URL.",
}

_TERMINAL_OK      = frozenset({"ATTACHED", "DRY_RUN", "ALREADY_SETTLED"})
_TERMINAL_SKIP    = frozenset(TYPED_STATUS_DOCS) - _TERMINAL_OK


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class RowResult:
    log_dedup_key:    str
    prediction_id:    str
    pitcher_name:     str
    pitcher_mlbam_id: int
    game_date:        str
    line:             float
    direction:        str
    status:           str
    pitch_count:      Optional[int]
    game_pk:          Optional[str]
    fetch_method:     Optional[str]
    detail:           Optional[str]
    dry_run:          bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "log_dedup_key":    self.log_dedup_key,
            "prediction_id":    self.prediction_id,
            "pitcher_name":     self.pitcher_name,
            "pitcher_mlbam_id": self.pitcher_mlbam_id,
            "game_date":        self.game_date,
            "line":             self.line,
            "direction":        self.direction,
            "status":           self.status,
            "pitch_count":      self.pitch_count,
            "game_pk":          self.game_pk,
            "fetch_method":     self.fetch_method,
            "detail":           self.detail,
            "dry_run":          self.dry_run,
        }


@dataclass
class IngestResult:
    dry_run:               bool
    run_timestamp:         str
    predictions_queried:   int
    before_date:           str
    after_date:            Optional[str]
    max_rows:              int
    rows:                  list[RowResult] = field(default_factory=list)
    top_level_error:       Optional[str]   = None

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rows:
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts

    @property
    def n_attached(self) -> int:
        return sum(1 for r in self.rows if r.status in ("ATTACHED", "DRY_RUN"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run":             self.dry_run,
            "run_timestamp":       self.run_timestamp,
            "predictions_queried": self.predictions_queried,
            "before_date":         self.before_date,
            "after_date":          self.after_date,
            "max_rows":            self.max_rows,
            "summary":             self.summary,
            "n_attached":          self.n_attached,
            "rows":                [r.to_dict() for r in self.rows],
            "top_level_error":     self.top_level_error,
        }


# ---------------------------------------------------------------------------
# DB helpers (own connection; never imports from app.py)
# ---------------------------------------------------------------------------

def _get_conn():
    try:
        import psycopg2
    except ImportError:
        raise RuntimeError("psycopg2 not available")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(db_url, connect_timeout=8)


def _find_unresolved(
    conn,
    before_date: str,
    after_date: Optional[str],
    max_rows: int,
) -> list[dict]:
    """
    Return prediction rows that have no attached outcome and whose game_date
    is strictly before before_date (exclusive).
    """
    params: list[Any] = [before_date, max_rows]
    after_clause = ""
    if after_date:
        after_clause = " AND p.game_date >= %s"
        params = [before_date, after_date, max_rows]

    query = f"""
        SELECT p.log_dedup_key, p.prediction_id,
               p.pitcher_name, p.pitcher_mlbam_id,
               p.game_date, p.line, p.direction, p.frozen_at
        FROM   wow_validation_prediction_log p
        WHERE  p.game_date < %s
        {after_clause}
          AND  NOT EXISTS (
                  SELECT 1 FROM wow_validation_outcome_log o
                  WHERE o.log_dedup_key = p.log_dedup_key
               )
        ORDER  BY p.game_date ASC
        LIMIT  %s
    """
    with conn.cursor() as cur:
        cur.execute(query, params)
        cols = ["log_dedup_key", "prediction_id", "pitcher_name",
                "pitcher_mlbam_id", "game_date", "line", "direction", "frozen_at"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Savant pitch-count fetch for a single game
# ---------------------------------------------------------------------------

def _fetch_game_pitch_count(pitcher_mlbam_id: int, game_date: str) -> dict[str, Any]:
    """
    Fetch the first-inning pitch count for pitcher on a specific game date.

    Fail-closed on doubleheaders: if pitcher made 2+ starts and pitched in
    more than one of them, return AMBIGUOUS_DOUBLEHEADER. If pitcher pitched
    in exactly 1 of N game_pks (other game_pks are relief appearances or
    non-starts), return OK with that game_pk.

    Returns dict:
      status:         "OK" | "NO_DATA" | "AMBIGUOUS_DOUBLEHEADER" | "FETCH_ERROR" | "IDENTITY_MISMATCH"
      pitch_count:    int | None
      game_pk:        str | None
      game_pks_found: list[str]
      fetch_method:   str
      error:          str | None
      outcome_verified: bool   (True = savant_csv_direct; False = pybaseball_fallback)
    """
    from gate_engine.mlb.savant_1ip_ledger import (
        _fetch_savant_csv_direct,
        _fetch_pybaseball_fallback,
        _ensure_pandas,
    )

    # Tight date window: [game_date - 1d, game_date + 1d] to capture the game
    try:
        gd    = date.fromisoformat(game_date)
        start = (gd - timedelta(days=1)).isoformat()
        end   = (gd + timedelta(days=1)).isoformat()
    except (ValueError, TypeError) as e:
        return {
            "status": "FETCH_ERROR", "pitch_count": None, "game_pk": None,
            "game_pks_found": [], "fetch_method": "none",
            "error": f"invalid game_date {game_date!r}: {e}",
            "outcome_verified": False,
        }

    # ── Primary: direct Savant CSV ────────────────────────────────────────
    df, fetch_method, err = _fetch_savant_csv_direct(pitcher_mlbam_id, start, end)
    outcome_verified = True

    if df is None:
        # ── Fallback: pybaseball ──────────────────────────────────────────
        df, fetch_method, err2 = _fetch_pybaseball_fallback(pitcher_mlbam_id, start, end)
        outcome_verified = False
        if df is None:
            return {
                "status": "FETCH_ERROR", "pitch_count": None, "game_pk": None,
                "game_pks_found": [], "fetch_method": fetch_method,
                "error": f"primary={err}; fallback={err2}",
                "outcome_verified": False,
            }

    if not _ensure_pandas():
        return {
            "status": "FETCH_ERROR", "pitch_count": None, "game_pk": None,
            "game_pks_found": [], "fetch_method": fetch_method,
            "error": "pandas not available",
            "outcome_verified": False,
        }

    # ── Verify date column matches expected game_date ─────────────────────
    if "game_date" in df.columns:
        import pandas as pd
        df["game_date"] = pd.to_datetime(df["game_date"])
        df = df[df["game_date"].dt.strftime("%Y-%m-%d") == game_date]
        if df.empty:
            return {
                "status": "IDENTITY_MISMATCH", "pitch_count": None, "game_pk": None,
                "game_pks_found": [], "fetch_method": fetch_method,
                "error": f"No rows with game_date={game_date!r} after date filter",
                "outcome_verified": False,
            }

    if df.empty:
        return {
            "status": "NO_DATA", "pitch_count": None, "game_pk": None,
            "game_pks_found": [], "fetch_method": fetch_method,
            "error": "DataFrame empty after filtering",
            "outcome_verified": False,
        }

    # ── Group by game_pk (doubleheader detection) ─────────────────────────
    group_col = "game_pk" if "game_pk" in df.columns else "game_date"
    groups = {}
    for gp, grp in df.groupby(group_col):
        groups[str(gp)] = len(grp)   # pitch count per game_pk

    all_pks = list(groups.keys())

    if len(groups) == 0:
        return {
            "status": "NO_DATA", "pitch_count": None, "game_pk": None,
            "game_pks_found": [], "fetch_method": fetch_method,
            "error": "No game groups after parsing",
            "outcome_verified": False,
        }

    if len(groups) == 1:
        # Single clear start
        gp_key    = all_pks[0]
        p_count   = groups[gp_key]
        if p_count <= 0:
            return {
                "status": "INVALID_PITCH_COUNT", "pitch_count": p_count,
                "game_pk": gp_key, "game_pks_found": all_pks,
                "fetch_method": fetch_method,
                "error": f"pitch_count={p_count} is not positive",
                "outcome_verified": False,
            }
        return {
            "status": "OK", "pitch_count": p_count, "game_pk": gp_key,
            "game_pks_found": all_pks, "fetch_method": fetch_method,
            "error": None, "outcome_verified": outcome_verified,
        }

    # Multiple game_pks: doubleheader scenario
    # Pitcher may have pitched in only 1 game (other is relief/off day)
    nonzero_pks = {pk: cnt for pk, cnt in groups.items() if cnt > 0}

    if len(nonzero_pks) == 0:
        return {
            "status": "NO_DATA", "pitch_count": None, "game_pk": None,
            "game_pks_found": all_pks, "fetch_method": fetch_method,
            "error": f"Pitcher appears in {len(groups)} game_pks but all have 0 pitches",
            "outcome_verified": False,
        }

    if len(nonzero_pks) == 1:
        # Doubleheader but pitcher only started one game — resolvable
        gp_key  = list(nonzero_pks.keys())[0]
        p_count = nonzero_pks[gp_key]
        return {
            "status": "OK", "pitch_count": p_count, "game_pk": gp_key,
            "game_pks_found": all_pks, "fetch_method": fetch_method,
            "error": None, "outcome_verified": outcome_verified,
        }

    # Pitcher had pitches in 2+ game_pks — cannot identify which was the bet game
    return {
        "status": "AMBIGUOUS_DOUBLEHEADER", "pitch_count": None, "game_pk": None,
        "game_pks_found": all_pks, "fetch_method": fetch_method,
        "error": (
            f"Pitcher {pitcher_mlbam_id} pitched in {len(nonzero_pks)} games on "
            f"{game_date}: {dict(list(nonzero_pks.items()))}. Cannot identify "
            f"which game the prediction covers. Manual resolution required."
        ),
        "outcome_verified": False,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def ingest_outcomes(
    *,
    dry_run: bool = True,
    before_date: Optional[str] = None,
    after_date: Optional[str]  = None,
    max_rows: int               = 50,
    outcome_source: str         = "baseball_savant_ingest",
    verbose: bool               = False,
) -> IngestResult:
    """
    Find unresolved 1IP predictions and attach pitch-count outcomes from Savant.

    Parameters
    ----------
    dry_run       If True (default), fetch data and compute outcomes but do
                  NOT write to wow_validation_outcome_log. Safe to run anytime.
    before_date   ISO date (exclusive upper bound). Defaults to today (UTC).
    after_date    ISO date (inclusive lower bound). Optional.
    max_rows      Maximum predictions to process per run. Default 50.
    outcome_source Source label written to outcome record. Default
                  "baseball_savant_ingest".
    verbose       If True, log per-row detail at INFO level.

    Returns
    -------
    IngestResult  with per-row statuses and summary.
    """
    now_utc = datetime.now(timezone.utc)
    run_ts  = now_utc.isoformat()

    today_str  = now_utc.strftime("%Y-%m-%d")
    before_str = before_date or today_str     # exclusive: game_date < before_date
    max_rows   = max(1, min(max_rows, 500))

    result = IngestResult(
        dry_run             = dry_run,
        run_timestamp       = run_ts,
        predictions_queried = 0,
        before_date         = before_str,
        after_date          = after_date,
        max_rows            = max_rows,
    )

    # ── 1. Connect and find unresolved predictions ────────────────────────
    try:
        conn = _get_conn()
    except Exception as e:
        result.top_level_error = f"DB_UNAVAILABLE:{str(e)[:100]}"
        return result

    try:
        preds = _find_unresolved(conn, before_str, after_date, max_rows)
    except Exception as e:
        result.top_level_error = f"QUERY_FAILED:{str(e)[:100]}"
        conn.close()
        return result

    conn.close()
    result.predictions_queried = len(preds)

    if not preds:
        return result

    # ── 2. Process each prediction ────────────────────────────────────────
    for pred in preds:
        key    = pred["log_dedup_key"]
        gdate  = str(pred["game_date"])[:10]
        pid    = int(pred["pitcher_mlbam_id"])
        pname  = pred["pitcher_name"]
        pred_id = pred["prediction_id"]
        line   = float(pred["line"])
        direction = str(pred["direction"])

        def _row(status: str, pitch_count=None, game_pk=None,
                 fetch_method=None, detail=None) -> RowResult:
            return RowResult(
                log_dedup_key    = key,
                prediction_id    = pred_id,
                pitcher_name     = pname,
                pitcher_mlbam_id = pid,
                game_date        = gdate,
                line             = line,
                direction        = direction,
                status           = status,
                pitch_count      = pitch_count,
                game_pk          = game_pk,
                fetch_method     = fetch_method,
                detail           = detail,
                dry_run          = dry_run,
            )

        # Guard: game must be in the past
        try:
            game_dt = date.fromisoformat(gdate)
            today   = date.fromisoformat(today_str)
            if game_dt >= today:
                result.rows.append(_row(
                    "GAME_NOT_YET_PLAYED",
                    detail=f"game_date={gdate} is not before today {today_str}",
                ))
                continue
        except (ValueError, TypeError) as e:
            result.rows.append(_row("FETCH_ERROR", detail=f"date_parse_error:{e}"))
            continue

        if verbose:
            logger.info("[ingest] %s %s %s %.1f%s — fetching Savant",
                        gdate, pname, pid, line, direction)

        # ── Fetch pitch count ─────────────────────────────────────────────
        try:
            fetch = _fetch_game_pitch_count(pid, gdate)
        except Exception as e:
            result.rows.append(_row("FETCH_ERROR", detail=f"unexpected:{type(e).__name__}:{e}"))
            continue

        fstatus  = fetch["status"]
        fcount   = fetch["pitch_count"]
        fgpk     = fetch["game_pk"]
        fmethod  = fetch["fetch_method"]
        ferror   = fetch.get("error")
        fverified = fetch.get("outcome_verified", False)

        if fstatus != "OK":
            result.rows.append(_row(fstatus, pitch_count=fcount, game_pk=fgpk,
                                    fetch_method=fmethod, detail=ferror))
            continue

        # ── Attach outcome ────────────────────────────────────────────────
        if dry_run:
            result.rows.append(_row(
                "DRY_RUN",
                pitch_count  = fcount,
                game_pk      = fgpk,
                fetch_method = fmethod,
                detail       = f"would_attach:pitches={fcount}:game_pk={fgpk}",
            ))
            continue

        try:
            attach_result = attach_outcome(
                log_dedup_key      = key,
                actual_pitches     = fcount,
                outcome_source     = outcome_source,
                outcome_verified   = fverified,
                notes              = f"game_pk={fgpk};fetch_method={fmethod}",
                outcome_timestamp  = run_ts,
            )
            action = attach_result.get("action", "")
            if action == "OUTCOME_ATTACHED":
                status = "ATTACHED"
            elif action == "ALREADY_SETTLED":
                status = "ALREADY_SETTLED"
            else:
                status = "OUTCOME_ATTACH_ERROR"
            result.rows.append(_row(
                status,
                pitch_count  = fcount,
                game_pk      = fgpk,
                fetch_method = fmethod,
                detail       = json.dumps({k: v for k, v in attach_result.items()
                                           if k not in ("prediction_id",)})[:200],
            ))
        except OutcomeLogError as oe:
            # ALREADY_SETTLED with same pitches is idempotent; re-raise is fine here
            if oe.code == "ALREADY_SETTLED" or "ALREADY" in oe.code:
                result.rows.append(_row("ALREADY_SETTLED", pitch_count=fcount,
                                        game_pk=fgpk, fetch_method=fmethod,
                                        detail=oe.detail))
            else:
                result.rows.append(_row("OUTCOME_ATTACH_ERROR", pitch_count=fcount,
                                        game_pk=fgpk, fetch_method=fmethod,
                                        detail=f"{oe.code}:{oe.detail[:120]}"))
        except Exception as e:
            result.rows.append(_row("OUTCOME_ATTACH_ERROR", pitch_count=fcount,
                                    game_pk=fgpk, fetch_method=fmethod,
                                    detail=f"unexpected:{type(e).__name__}:{str(e)[:80]}"))

    return result
