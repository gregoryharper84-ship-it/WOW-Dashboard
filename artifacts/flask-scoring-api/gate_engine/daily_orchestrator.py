"""
gate_engine/daily_orchestrator.py — Canonical WOW Daily Orchestration

WOW-PATCH-2026-08-19-DAILY-CANONICAL-v1.0 (Task #277)

Implements the single authoritative daily discovery-and-scoring orchestration
that the Custom GPT submits high-level intent to.  Replit owns:
  - Discovery (source union across all readable providers)
  - Canonical identity assignment (stable selection + market-version IDs)
  - Full-board preservation (no pre-score truncation)
  - Side-orientation resolution (fail-closed — no silent home default)
  - Specialist readiness contracts (WNBA_ML_V1, TENNIS_MATCH_WINNER_V1)
  - Soccer 1X2 canonical outcome normalisation (HOME / DRAW / AWAY server-side)
  - Evaluation via existing gate-engine pipeline
  - Exact reconciliation proof (discovered = sum of all terminal buckets)
  - Immutable run-manifest persistence
  - Idempotent exposure reservation (post-evaluation)
  - Compact GPT output + paginated manifest endpoint

Governance invariants — NEVER altered by this module
----------------------------------------------------
- can_execute = False  (unconditional)
- Probability formulas, calibration, thresholds, terminal labels unchanged
- Ceilings propagate lowest-ceiling-wins as before
- Playable classification downgrade on unverified provenance is preserved
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Module-level invariants
can_execute  = False
PATCH_ID     = "WOW-PATCH-2026-08-19-DAILY-CANONICAL-v1.0"
ENGINE_VER   = "DAILY_ORCH_v1.0"

# Sports the canonical orchestrator handles
_ALL_SPORTS = [
    "NBA", "WNBA", "MLB", "NFL", "NHL",
    "NCAAB", "NCAAF", "Soccer", "Tennis",
]

# Canonical terminal bucket names for reconciliation
_TERMINAL_BUCKETS = (
    "market_verified",
    "final_approved_internal",
    "model_qualified",
    "conditional",
    "watch",
    "reject",
    "data_insufficient",
    "no_play",
)

# Soccer outcome normalisation map (display value → canonical)
_SOCCER_OUTCOME_MAP = {
    "home":  "HOME",
    "h":     "HOME",
    "1":     "HOME",
    "draw":  "DRAW",
    "d":     "DRAW",
    "x":     "DRAW",
    "away":  "AWAY",
    "a":     "AWAY",
    "2":     "AWAY",
    # already canonical
    "HOME":  "HOME",
    "DRAW":  "DRAW",
    "AWAY":  "AWAY",
}

# Minimum WNBA_ML_V1 fields required for specialist to produce a probability
_WNBA_SPECIALIST_REQUIRED_PAIRS = [
    ("home_win_pct", "away_win_pct"),
    ("home_power",   "away_power"),
    ("home_elo",     "away_elo"),
]

# Minimum TENNIS_MATCH_WINNER_V1 input pairs for specialist activation
_TENNIS_SPECIALIST_REQUIRED_PAIRS = [
    ("surface_adjusted_form", "surface"),
    ("home_elo",              "away_elo"),
    ("hold_rate",             "break_rate"),
    ("h2h_win_rate",          "surface"),
]


class DailyRunDeadlineExceeded(RuntimeError):
    """Raised at lifecycle boundaries when the canonical run deadline expires."""


def _raise_if_deadline_exceeded(deadline_at: str | None) -> None:
    if not deadline_at:
        return
    deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= deadline:
        raise DailyRunDeadlineExceeded("WHOLE_RUN_DEADLINE_EXCEEDED")


# ---------------------------------------------------------------------------
# Side-orientation resolver (fail-closed replacement for _is_home_side)
# ---------------------------------------------------------------------------

def resolve_participant_side(row: dict[str, Any]) -> str:
    """
    Resolve the participant orientation of a candidate row.

    Returns
    -------
    "HOME"         — explicit home marker found
    "AWAY"         — explicit away marker found
    "SIDE_UNKNOWN" — no reliable marker (fail-closed; never defaults to home)

    This compatibility wrapper delegates to the same typed resolver used by
    direct moneyline callers, while preserving the canonical orchestrator's
    established SIDE_UNKNOWN serialization.
    """
    from gate_engine.moneyline.orientation import (
        ParticipantOrientation,
        resolve_participant_orientation,
    )

    enrichment = row.get("enrichment")
    if not isinstance(enrichment, dict):
        enrichment = {}
    resolution = resolve_participant_orientation(row, enrichment)
    if resolution.orientation == ParticipantOrientation.HOME:
        return "HOME"
    if resolution.orientation == ParticipantOrientation.AWAY:
        return "AWAY"
    return "SIDE_UNKNOWN"


# ---------------------------------------------------------------------------
# Soccer 1X2 outcome normalisation
# ---------------------------------------------------------------------------

def normalise_soccer_outcome(raw_outcome: str | None) -> str | None:
    """
    Map a raw soccer 1X2 outcome string to the canonical HOME/DRAW/AWAY form.
    Returns None when the input is None or unrecognisable.
    """
    if raw_outcome is None:
        return None
    key = raw_outcome.strip()
    # Try direct match (handles already-canonical values)
    if key in _SOCCER_OUTCOME_MAP:
        return _SOCCER_OUTCOME_MAP[key]
    # Case-insensitive
    key_lower = key.lower()
    if key_lower in _SOCCER_OUTCOME_MAP:
        return _SOCCER_OUTCOME_MAP[key_lower]
    return None


def normalise_soccer_props(props: list[dict]) -> list[dict]:
    """
    In-place normalisation of soccer 1X2 outcome fields on a prop list.
    Returns the same list (mutated).
    """
    for p in props:
        sport = (p.get("sport") or "").upper()
        if sport not in ("SOCCER", "FOOTBALL", "EPL", "MLS", "UCL", "LALIGA"):
            continue
        raw = p.get("outcome") or p.get("side") or p.get("result")
        canonical = normalise_soccer_outcome(raw)
        if canonical is not None:
            p["outcome"] = canonical
    return props


# ---------------------------------------------------------------------------
# Specialist readiness contracts
# ---------------------------------------------------------------------------

def wnba_ml_specialist_ready(enrichment_entry: dict[str, Any]) -> bool:
    """
    True only when the WNBA_ML_V1 specialist has at least one complete
    paired input it can consume to produce an independent probability.

    Requires BOTH members of at least one pair from _WNBA_SPECIALIST_REQUIRED_PAIRS.
    Partial hydration (e.g. home_win_pct only, without away_win_pct) is NOT ready.
    """
    for field_a, field_b in _WNBA_SPECIALIST_REQUIRED_PAIRS:
        if (
            enrichment_entry.get(field_a) is not None
            and enrichment_entry.get(field_b) is not None
        ):
            return True
    return False


def tennis_ml_specialist_ready(enrichment_entry: dict[str, Any]) -> bool:
    """
    True only when the TENNIS_MATCH_WINNER_V1 specialist has at least one
    complete paired input it can consume.
    """
    for field_a, field_b in _TENNIS_SPECIALIST_REQUIRED_PAIRS:
        if (
            enrichment_entry.get(field_a) is not None
            and enrichment_entry.get(field_b) is not None
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Canonical identity helpers
# ---------------------------------------------------------------------------

def _canonical_selection_id(
    sport: str,
    player: str,
    prop: str,
    side: str,
    line: float,
) -> str:
    """
    Stable, deterministic canonical selection identifier.
    Hash over (sport, player, prop, side, line_bucket) so minor float noise
    on the same market resolves to the same ID.
    line_bucket rounds to nearest 0.5.
    """
    line_bucket = round(float(line) * 2) / 2  # nearest 0.5
    parts = "|".join([
        sport.upper(),
        player.strip().lower(),
        prop.strip().lower(),
        side.upper(),
        str(line_bucket),
    ])
    digest = hashlib.sha256(parts.encode()).hexdigest()[:16]
    return f"SEL_{digest}"


def _market_version_id(
    canonical_selection_id: str,
    run_date: str,
    source_keys: tuple[str, ...],
) -> str:
    """
    Version key for a specific market snapshot within a run date.
    Changes when source availability or line moves.
    """
    parts = "|".join([canonical_selection_id, run_date] + list(source_keys))
    digest = hashlib.sha256(parts.encode()).hexdigest()[:12]
    return f"MKT_{digest}"


# ---------------------------------------------------------------------------
# Source union
# ---------------------------------------------------------------------------

def _union_props_for_sport(sport: str) -> tuple[list[dict], dict[str, str]]:
    """
    Fetch and union props from all readable sources for a sport.

    Returns (props, source_status_dict) where:
      props             — deduplicated union of all readable sources
      source_status_dict — {source_key: status_string}

    Never truncates.  Always calls both primary and backup, regardless of
    whether the primary succeeded, so the board is as complete as possible.
    """
    from services.odds_api  import fetch_all_props
    from services.rundown   import fetch_backup_props

    status: dict[str, str] = {}
    all_props: list[dict]  = []

    # Primary: Odds API
    try:
        primary_props, odds_status = fetch_all_props(sport)
        status[f"{sport}_odds"] = (
            odds_status.get("props", str(odds_status))
            if isinstance(odds_status, dict) else str(odds_status)
        )
        all_props.extend(primary_props or [])
    except Exception as exc:
        status[f"{sport}_odds"] = f"FAILED:{exc}"

    # Backup: TheRundown — always called, unioned (not fallback-only)
    try:
        backup_props, rd_status = fetch_backup_props(sport)
        status[f"{sport}_rundown"] = str(rd_status)
        all_props.extend(backup_props or [])
    except Exception as exc:
        status[f"{sport}_rundown"] = f"FAILED:{exc}"

    # Deduplicate: first-seen wins on exact (player, prop, side, line, sport)
    seen: dict[tuple, dict] = {}
    for p in all_props:
        key = (
            (p.get("player") or "").strip().lower(),
            (p.get("prop")   or "").strip().lower(),
            (p.get("side")   or "MORE").upper(),
            round(float(p.get("line") or 0) * 2) / 2,
            sport.upper(),
        )
        if key not in seen:
            seen[key] = p

    return list(seen.values()), status


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def _build_reconciliation(
    discovered_ids: set[str],
    scan_result: dict[str, Any],
) -> dict[str, Any]:
    """
    Prove that every discovered selection appears in exactly one terminal bucket.

    Returns
    -------
    dict with:
      reconciled          — bool (True = all selections accounted for)
      discovered_count    — int
      terminal_counts     — {bucket_name: count}
      total_terminal      — int
      duplicate_ids       — list of selection IDs that appear in >1 bucket
      missing_ids         — list of selection IDs not found in any bucket
      excess_ids          — list of terminal IDs not in the discovered board
    """
    terminal_ids: dict[str, str] = {}  # sel_id → bucket
    duplicate_ids: list[str]     = []

    for bucket_name in _TERMINAL_BUCKETS:
        for card in scan_result.get(bucket_name, []):
            sel_id = card.get("canonical_selection_id")
            if not sel_id:
                continue
            if sel_id in terminal_ids:
                duplicate_ids.append(sel_id)
            else:
                terminal_ids[sel_id] = bucket_name

    terminal_counts = {}
    for bucket_name in _TERMINAL_BUCKETS:
        terminal_counts[bucket_name] = len(scan_result.get(bucket_name, []))

    total_terminal  = sum(terminal_counts.values())
    terminal_id_set = set(terminal_ids.keys())

    missing_ids = sorted(discovered_ids - terminal_id_set)
    excess_ids  = sorted(terminal_id_set - discovered_ids)

    reconciled = (
        len(missing_ids)   == 0
        and len(excess_ids)   == 0
        and len(duplicate_ids) == 0
    )

    return {
        "reconciled":      reconciled,
        "discovered_count": len(discovered_ids),
        "total_terminal":  total_terminal,
        "terminal_counts": terminal_counts,
        "duplicate_ids":   duplicate_ids,
        "missing_ids":     missing_ids,
        "excess_ids":      excess_ids,
    }


# ---------------------------------------------------------------------------
# Compact GPT output builder
# ---------------------------------------------------------------------------

def _compact_card(card: dict[str, Any]) -> dict[str, Any]:
    """Build a compact, GPT-sized view of a scored card."""
    return {
        "id":     card.get("canonical_selection_id"),
        "player": card.get("player"),
        "sport":  card.get("sport"),
        "prop":   card.get("prop"),
        "side":   card.get("side"),
        "line":   card.get("line"),
        "score":  card.get("wow_score"),
        "bucket": card.get("terminal_bucket") or card.get("classification"),
        "blocker": card.get("final_approval_blocker"),
        "audit_valid": card.get("audit_valid"),
    }


# ---------------------------------------------------------------------------
# Main orchestration entry point
# ---------------------------------------------------------------------------

def run_daily_orchestration(
    *,
    run_id: str | None                    = None,
    sports: list[str] | None          = None,
    environment: str                   = "live",
    runtime_provenance: dict | None    = None,
    session_id: str | None             = None,
    deadline_at: str | None             = None,
    persist: bool                      = True,
    execution_owner: str | None        = None,
) -> dict[str, Any]:
    """
    Canonical WOW Daily orchestration.

    Caller responsibilities
    -----------------------
    - Supply runtime_provenance (built by Flask route from server-authoritative probes)
    - Supply session_id for exposure idempotency tracking
    - Set persist=False in tests to skip DB writes

    Returns
    -------
    Structured dict:
      run_id            — immutable run identifier
      run_date          — ISO date
      run_status        — COMPLETE | DEGRADED | PARTIAL | RECONCILIATION_FAILED
      counts            — bucket counts
      playable_card     — compact list of approved/qualified picks
      reconciliation    — exact board reconciliation proof
      source_union      — per-sport source availability
      missing_sports    — sports with zero discovered props
      failed_modules    — acquisition errors
      execution_notes   — audit trail
      runtime_provenance — echoed back
    """
    from jobs.wow_daily_scan import run_scan as _run_scan

    # ---- Run identity -------------------------------------------------------
    run_id   = run_id or str(uuid.uuid4())
    run_date = date.today().isoformat()
    started_at = datetime.now(timezone.utc).isoformat()

    requested_sports = list(sports) if sports is not None else list(_ALL_SPORTS)
    execution_notes: list[str]  = [f"DAILY_ORCH_START run_id={run_id}"]
    failed_modules: list[str]   = []
    source_union_counts: dict   = {}
    source_status_all: dict     = {}

    # ---- Bootstrap manifest table (once per process, fail-open) -------------
    if persist:
        try:
            from storage.daily_manifest import ensure_tables, create_run, mark_progress
            ensure_tables()
            create_run(
                run_id=run_id,
                run_date=run_date,
                started_at=started_at,
                environment=environment,
                requested_sports=requested_sports,
                session_id=session_id,
                runtime_provenance=runtime_provenance,
            )
            mark_progress(
                run_id=run_id,
                stage="DISCOVERY",
                detail="Preparing source union",
                execution_owner=execution_owner,
            )
        except Exception as exc:
            logger.warning("daily_manifest.create_run failed (non-fatal): %s", exc)
            execution_notes.append(f"MANIFEST_CREATE_FAILED:{exc}")

    # ---- Source union per sport ---------------------------------------------
    props_by_sport: dict[str, list[dict]] = {}
    discovered_ids: set[str] = set()
    scanned_sports: list[str] = []
    missing_sports: list[str] = []

    for sport in requested_sports:
        _raise_if_deadline_exceeded(deadline_at)
        if persist:
            try:
                from storage.daily_manifest import mark_progress
                mark_progress(
                    run_id=run_id,
                    stage="DISCOVERY",
                    detail=f"Discovering {sport}",
                    execution_owner=execution_owner,
                )
            except Exception as exc:
                execution_notes.append(f"MANIFEST_PROGRESS_FAILED:{exc}")
        execution_notes.append(f"--- {sport}: union ---")
        raw_props, src_status = _union_props_for_sport(sport)
        source_status_all.update(src_status)

        if not raw_props:
            missing_sports.append(sport)
            execution_notes.append(f"{sport}: no props from any source — MISSING")
            continue

        # Soccer 1X2 canonical outcome normalisation (server-side)
        if sport.upper() in ("SOCCER", "FOOTBALL", "EPL", "MLS", "UCL", "LALIGA"):
            raw_props = normalise_soccer_props(raw_props)

        # Assign canonical identities + side resolution
        for p in raw_props:
            csel_id = _canonical_selection_id(
                sport,
                p.get("player", ""),
                p.get("prop",   ""),
                p.get("side",   "MORE"),
                float(p.get("line") or 0),
            )
            p["canonical_selection_id"] = csel_id
            p["market_version_id"] = _market_version_id(
                csel_id, run_date,
                (src_status.get(f"{sport}_odds", ""), src_status.get(f"{sport}_rundown", "")),
            )

            # Participant-side resolution (fail-closed)
            p["_side_resolution"] = resolve_participant_side(p)
            discovered_ids.add(csel_id)

        source_union_counts[sport] = len(raw_props)
        props_by_sport[sport]      = raw_props
        scanned_sports.append(sport)
        execution_notes.append(
            f"{sport}: {len(raw_props)} props discovered (union)"
        )

    total_discovered = len(discovered_ids)
    execution_notes.append(
        f"DISCOVERY_COMPLETE: {total_discovered} unique selections across "
        f"{len(scanned_sports)} sports"
    )

    # ---- WNBA specialist readiness audit ------------------------------------
    for sport in scanned_sports:
        if sport.upper() != "WNBA":
            continue
        for p in props_by_sport.get(sport, []):
            enr = p.get("enrichment") or {}
            if not wnba_ml_specialist_ready(enr):
                p.setdefault("_specialist_readiness", "WNBA_ML_V1:PARTIAL_HYDRATION_NOT_READY")

    # ---- Tennis specialist readiness audit ----------------------------------
    for sport in scanned_sports:
        if sport.upper() not in ("ATP", "WTA", "TENNIS"):
            continue
        for p in props_by_sport.get(sport, []):
            enr = p.get("enrichment") or {}
            if not tennis_ml_specialist_ready(enr):
                p.setdefault("_specialist_readiness", "TENNIS_MATCH_WINNER_V1:PARTIAL_HYDRATION_NOT_READY")

    # ---- Evaluation via existing gate-engine pipeline -----------------------
    # pass the canonical board; no truncation; don't persist legacy scan rows
    try:
        _raise_if_deadline_exceeded(deadline_at)
        if persist:
            try:
                from storage.daily_manifest import mark_progress
                mark_progress(
                    run_id=run_id,
                    stage="SCORING",
                    detail="Evaluating canonical board",
                    execution_owner=execution_owner,
                )
            except Exception as exc:
                execution_notes.append(f"MANIFEST_PROGRESS_FAILED:{exc}")
        scan_result = _run_scan(
            sports=scanned_sports or requested_sports,
            environment=environment,
            limit_per_sport=None,       # no pre-score cap
            runtime_provenance=runtime_provenance,
            _props_by_sport=props_by_sport,
            _persist_results=False,     # orchestrator owns persistence
        )
        failed_modules.extend(scan_result.get("failed_modules", []))
    except Exception as exc:
        logger.error("daily_orchestrator: run_scan failed: %s", exc)
        failed_modules.append(f"run_scan:{exc}")
        # Fail partial: return error, but still persist the run header
        scan_result = {
            "run_status":   "FAILED",
            "failed_modules": [f"run_scan:{exc}"],
            "execution_notes": [f"run_scan raised: {exc}"],
        }
        for b in _TERMINAL_BUCKETS:
            scan_result.setdefault(b, [])

    # ---- Stamp canonical IDs onto every output card -------------------------
    # run_scan output cards don't carry canonical_selection_id yet; backfill
    # from the discovered board using (sport, player, prop, side, line) lookup.
    _id_lookup: dict[tuple, str] = {}
    for sport, props_list in props_by_sport.items():
        for p in props_list:
            key = (
                sport.upper(),
                (p.get("player") or "").strip().lower(),
                (p.get("prop")   or "").strip().lower(),
                (p.get("side")   or "MORE").upper(),
                round(float(p.get("line") or 0) * 2) / 2,
            )
            _id_lookup[key] = p["canonical_selection_id"]

    _side_res_lookup: dict[str, str] = {}
    for sport, props_list in props_by_sport.items():
        for p in props_list:
            _side_res_lookup[p.get("canonical_selection_id", "")] = p.get("_side_resolution", "SIDE_UNKNOWN")

    for bucket_name in _TERMINAL_BUCKETS:
        for card in scan_result.get(bucket_name, []):
            if "canonical_selection_id" not in card:
                key = (
                    (card.get("sport")  or "").upper(),
                    (card.get("player") or "").strip().lower(),
                    (card.get("prop")   or "").strip().lower(),
                    (card.get("side")   or "MORE").upper(),
                    round(float(card.get("line") or 0) * 2) / 2,
                )
                card["canonical_selection_id"] = _id_lookup.get(key, f"SEL_UNKNOWN_{key}")
            sel_id = card.get("canonical_selection_id", "")
            card["side_resolution"] = _side_res_lookup.get(sel_id, "SIDE_UNKNOWN")
            card["terminal_bucket"] = card.get("terminal_bucket") or card.get("classification", bucket_name)

    # ---- Reconciliation proof -----------------------------------------------
    _raise_if_deadline_exceeded(deadline_at)
    if persist:
        try:
            from storage.daily_manifest import mark_progress
            mark_progress(
                run_id=run_id,
                stage="RECONCILIATION",
                detail="Reconciling discovered selections to terminal rows",
                execution_owner=execution_owner,
            )
        except Exception as exc:
            execution_notes.append(f"MANIFEST_PROGRESS_FAILED:{exc}")
    reconciliation = _build_reconciliation(discovered_ids, scan_result)
    if not reconciliation["reconciled"]:
        execution_notes.append(
            f"RECONCILIATION_WARNING: "
            f"missing={len(reconciliation['missing_ids'])} "
            f"excess={len(reconciliation['excess_ids'])} "
            f"duplicates={len(reconciliation['duplicate_ids'])}"
        )

    # ---- Persist manifest rows ----------------------------------------------
    finished_at = datetime.now(timezone.utc).isoformat()
    run_status_inner = scan_result.get("run_status", "COMPLETE")
    if not reconciliation["reconciled"]:
        run_status_inner = "RECONCILIATION_WARNING"
    if failed_modules:
        run_status_inner = "DEGRADED"

    if persist:
        try:
            from storage.daily_manifest import finalize_run, mark_progress, save_run_row
            mark_progress(
                run_id=run_id,
                stage="PERSISTING_ROWS",
                detail="Persisting evaluated selections",
                execution_owner=execution_owner,
            )
            # Persist each evaluated row
            for bucket_name in _TERMINAL_BUCKETS:
                for card in scan_result.get(bucket_name, []):
                    _raise_if_deadline_exceeded(deadline_at)
                    sel_id = card.get("canonical_selection_id", "")
                    if not save_run_row(
                        run_id=run_id,
                        canonical_selection_id=sel_id,
                        market_version_id=card.get("market_version_id"),
                        run_date=run_date,
                        sport=card.get("sport", ""),
                        player=card.get("player", ""),
                        prop=card.get("prop", ""),
                        side=card.get("side", ""),
                        line=float(card.get("line") or 0),
                        game_date=card.get("game_date"),
                        terminal_bucket=card.get("terminal_bucket"),
                        classification=card.get("classification"),
                        wow_score=card.get("wow_score"),
                        final_approval_blocker=card.get("final_approval_blocker"),
                        audit_valid=card.get("audit_valid"),
                        side_resolution=card.get("side_resolution"),
                        reconciliation_status=(
                            "OK" if card.get("canonical_selection_id") in discovered_ids
                            else "EXCESS"
                        ),
                        full_row=card,
                        execution_owner=execution_owner,
                    ):
                        raise RuntimeError(
                            f"MANIFEST_ROW_PERSIST_FAILED:{sel_id}"
                        )
            _raise_if_deadline_exceeded(deadline_at)
            if not finalize_run(
                run_id=run_id,
                finished_at=finished_at,
                run_status=run_status_inner,
                scanned_sports=scanned_sports,
                missing_sports=missing_sports,
                failed_modules=failed_modules,
                total_discovered=total_discovered,
                source_union_counts=source_union_counts,
                reconciliation=reconciliation,
                execution_owner=execution_owner,
            ):
                raise RuntimeError("MANIFEST_FINALIZE_FAILED")
        except Exception as exc:
            logger.error("daily_manifest persistence failed: %s", exc)
            execution_notes.append(f"MANIFEST_PERSIST_FAILED:{exc}")
            try:
                from storage.daily_manifest import terminalize_run
                terminalize_run(
                    run_id=run_id,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    run_status="DEGRADED",
                    failure_reason=f"MANIFEST_PERSIST_FAILED:{exc}",
                    failure_module="daily_orchestrator.persistence",
                    reconciliation=reconciliation,
                    execution_owner=execution_owner,
                )
            except Exception:
                logger.exception("daily_manifest terminalization failed for %s", run_id)

    # ---- Build counts -------------------------------------------------------
    counts = {b: len(scan_result.get(b, [])) for b in _TERMINAL_BUCKETS}
    total_final = counts["market_verified"] + counts["final_approved_internal"]
    counts["total_final_approved"] = total_final
    counts["playable_count"]       = total_final + counts["model_qualified"]
    counts["total_discovered"]     = total_discovered

    # ---- Build compact GPT output -------------------------------------------
    playable_buckets = (
        list(scan_result.get("market_verified", []))
        + list(scan_result.get("final_approved_internal", []))
        + list(scan_result.get("model_qualified", []))
    )
    playable_compact = [_compact_card(c) for c in playable_buckets]

    execution_notes.extend(scan_result.get("execution_notes", []))
    execution_notes.append(
        f"DAILY_ORCH_COMPLETE run_id={run_id} "
        f"discovered={total_discovered} "
        f"reconciled={reconciliation['reconciled']} "
        f"status={run_status_inner}"
    )

    return {
        "ok":                  True,
        "run_id":              run_id,
        "run_date":            run_date,
        "started_at":          started_at,
        "finished_at":         finished_at,
        "run_status":          run_status_inner,
        "environment":         environment,
        "requested_sports":    requested_sports,
        "scanned_sports":      scanned_sports,
        "missing_sports":      missing_sports,
        "failed_modules":      failed_modules,
        "counts":              counts,
        "playable_card":       playable_compact,
        "reconciliation":      reconciliation,
        "source_union":        source_status_all,
        "source_union_counts": source_union_counts,
        "runtime_provenance":  runtime_provenance,
        "execution_notes":     execution_notes,
        "engine":              ENGINE_VER,
        "patch_id":            PATCH_ID,
        # Full bucket lists for manifest consumers; not included in compact GPT view
        "_buckets": {b: scan_result.get(b, []) for b in _TERMINAL_BUCKETS},
    }
