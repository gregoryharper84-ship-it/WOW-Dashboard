"""
gate_engine/wnba/opportunity_engine.py  —  PATCH-WNBA-001
WNBA Opportunity and Role Engine

Gates WNBA rows on opportunity stability BEFORE the probability pipeline.
A row that lacks stable minutes, usage, and shot volume for the proposed
market cannot produce a reliable probability estimate.

Call order: after status_role.run() (player_status_and_role_lock), before
l5_l10_ledger (layers 1-2).  A hard-reject exits the per-row loop early.
A soft-hold applies MODEL_QUALIFIED_HOLD ceiling and lets the row continue.

Output schema per row (stored at row["gates"]["wnba_opportunity_gate"]):
{
  "gate_passed": bool,
  "gate_label": str,
  "expected_minutes": float,
  "minutes_floor": float,
  "minutes_ceiling": float,
  "minutes_stability_score": int,      # 0-100
  "usage_stability_score": int,
  "shot_attempt_stability_score": int,
  "assist_opportunity_stability_score": int,
  "rebound_opportunity_stability_score": int,
  "rotation_volatility_score": int,    # 0-100, HIGHER = more volatile
  "opportunity_stability_score": int,  # composite, 0-100
  "role_state": str,
  "role_confidence": float,
  "primary_teammate_dependency": list,
  "archetype": str,
  "blockers": list[str],
  "games_analyzed": int,
  "can_execute": false
}

Thresholds are module-level constants to allow calibration via forward-test
ledger once sufficient data accumulates (see PATCH-WNBA-001 spec).

can_execute=False is unconditional.
"""
from __future__ import annotations

import math
import os
from typing import Any

can_execute = False

# ---------------------------------------------------------------------------
# Configurable thresholds (forward-test ledger calibrates these over time)
# ---------------------------------------------------------------------------

THRESH_OSS_GENERAL   = 65    # opportunity_stability_score for non-composite markets
THRESH_OSS_PRA       = 70    # opportunity_stability_score for PRA / composite markets
THRESH_ROLE_CONF     = 0.80  # role_confidence floor
THRESH_MIN_STAB      = 60    # minutes_stability_score floor
THRESH_ROT_VOLT_HARD = 80    # rotation_volatility_score → hard reject above this
MIN_GAMES_REQUIRED   = 3     # minimum non-DNP games to compute scores

# ---------------------------------------------------------------------------
# Composite / PRA stat family set (mirrors wow_runtime_manifest normalisation)
# ---------------------------------------------------------------------------

_PRA_FAMILIES: frozenset[str] = frozenset({
    "pra", "points rebounds assists",
    "p r", "points rebounds",
    "p a", "points assists",
    "r a", "rebounds assists",
    "p+r", "p+a", "r+a",
    "points+rebounds", "points+assists", "rebounds+assists",
})

# ---------------------------------------------------------------------------
# Blocker / label constants
# ---------------------------------------------------------------------------

LABEL_REJECT_UNSTABLE     = "WNBA_REJECT_UNSTABLE_OPPORTUNITY"
LABEL_REJECT_ROTATION     = "WNBA_REJECT_ROTATION_VOLATILITY"
LABEL_HOLD_ROLE_UNCERTAIN = "WNBA_HOLD_ROLE_UNCERTAIN"

# ---------------------------------------------------------------------------
# DB DDL — opportunity_audits table (created lazily on first use)
# ---------------------------------------------------------------------------

_DDL_OPP_AUDITS = """
CREATE TABLE IF NOT EXISTS opportunity_audits (
    id                              BIGSERIAL PRIMARY KEY,
    session_id                      TEXT,
    research_run_id                 TEXT,
    row_id                          TEXT,
    player_name                     TEXT NOT NULL,
    event_date                      DATE,
    stat_family                     TEXT,
    line                            NUMERIC,
    direction                       TEXT,
    expected_minutes                NUMERIC,
    minutes_floor                   NUMERIC,
    minutes_ceiling                 NUMERIC,
    minutes_stability_score         INTEGER,
    usage_stability_score           INTEGER,
    shot_attempt_stability_score    INTEGER,
    assist_opportunity_stability_score INTEGER,
    rebound_opportunity_stability_score INTEGER,
    rotation_volatility_score       INTEGER,
    opportunity_stability_score     INTEGER,
    role_state                      TEXT,
    role_confidence                 NUMERIC,
    primary_teammate_dependency     JSONB,
    archetype                       TEXT,
    gate_passed                     BOOLEAN,
    gate_label                      TEXT,
    blockers                        JSONB,
    logged_at                       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS opp_audit_player_idx  ON opportunity_audits(player_name);
CREATE INDEX IF NOT EXISTS opp_audit_session_idx ON opportunity_audits(session_id);
CREATE INDEX IF NOT EXISTS opp_audit_row_idx     ON opportunity_audits(row_id);
"""

_opp_table_ready = False


def _ensure_opp_table() -> None:
    global _opp_table_ready
    if _opp_table_ready:
        return
    try:
        import psycopg2  # type: ignore
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        cur  = conn.cursor()
        cur.execute(_DDL_OPP_AUDITS)
        conn.commit()
        cur.close()
        conn.close()
        _opp_table_ready = True
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm_stat(s: str) -> str:
    return (s or "").lower().strip().replace("+", " ").replace("-", " ").replace("_", " ")


def is_wnba_row(row: dict[str, Any]) -> bool:
    """Return True if row is a WNBA sport row."""
    sport = str(row.get("sport") or row.get("league") or "").strip().upper()
    return sport in {"WNBA", "W NBA", "WOMEN'S BASKETBALL"}


def is_pra_market(row: dict[str, Any]) -> bool:
    """Return True if the prop is a composite PRA-family market."""
    stat = _norm_stat(row.get("prop_type") or row.get("prop") or row.get("stat_type") or "")
    return stat in _PRA_FAMILIES


def _extract_float(game: dict[str, Any], keys: list[str]) -> float | None:
    """Try a list of key names and return the first parseable non-negative float."""
    for k in keys:
        v = game.get(k)
        if v is None:
            continue
        try:
            f = float(v)
            if f >= 0:
                return f
        except (TypeError, ValueError):
            pass
    return None


def _compute_stability_score(values: list[float]) -> int:
    """
    Convert a value series to a 0-100 stability score.
    100 = perfectly stable (zero variance).
    Uses coefficient of variation (std / mean), clamped at 1.0.
    Returns 50 when fewer than 2 values are available (neutral/unknown).
    """
    n = len(values)
    if n < 2:
        return 50
    mean = sum(values) / n
    if mean <= 0:
        return 50
    variance = sum((v - mean) ** 2 for v in values) / n
    cv = math.sqrt(variance) / mean
    return max(0, min(100, round(100 * (1.0 - min(cv, 1.0)))))


def _compute_rotation_volatility(minutes: list[float]) -> int:
    """
    Rotation volatility score: 0 = stable, 100 = chaotic.
    Measures how far individual game minutes deviate from the player's mean.
    A mean swing of ≥10 minutes maps to ~100.
    """
    n = len(minutes)
    if n < 2:
        return 50
    mean_min = sum(minutes) / n
    if mean_min <= 0:
        return 50
    mean_swing = sum(abs(m - mean_min) for m in minutes) / n
    # Normalize: 10+ minute average swing → 100 volatility
    volt = min(100, round(100 * mean_swing / max(mean_min * 0.30, 1.0)))
    return volt


def _infer_role_state(
    row: dict[str, Any],
    mean_minutes: float | None,
    mean_usage: float | None,
    mean_ast: float | None,
) -> tuple[str, float]:
    """
    Infer (role_state, role_confidence) from available signals.

    Priority order:
    1. role_status from status_role gate (highest signal)
    2. Usage rate (if available)
    3. Minutes (fallback)
    """
    role_status = str(row.get("role_status") or "").upper()

    if "STARTER" in role_status and "UNRESOLVED" not in role_status:
        if mean_ast is not None and mean_ast >= 4.5:
            return "PRIMARY_CREATOR", 0.87
        if mean_ast is not None and mean_ast >= 2.5:
            return "SECONDARY_CREATOR", 0.83
        return "SECONDARY_CREATOR", 0.80

    if "BENCH" in role_status and "UNRESOLVED" not in role_status:
        if mean_minutes is not None and mean_minutes >= 22:
            return "BENCH_STARTER_HYBRID", 0.75
        return "BENCH_CONTRIBUTOR", 0.70

    if "UNRESOLVED" in role_status or "UNKNOWN" in role_status:
        return "ROLE_UNKNOWN", 0.45

    # Infer from usage rate when role_status not set
    if mean_usage is not None:
        if mean_usage >= 0.28:
            return "HIGH_USAGE_PRIMARY", 0.80
        if mean_usage >= 0.22:
            return "SECONDARY_CREATOR", 0.74
        if mean_usage >= 0.16:
            return "CEILING_DEPENDENT_SECONDARY_SCORER", 0.67
        return "SPOT_ROLE_CEILING_DEPENDENT", 0.55

    # Infer from minutes alone
    if mean_minutes is not None:
        if mean_minutes >= 30:
            return "SECONDARY_CREATOR", 0.72
        if mean_minutes >= 22:
            return "CEILING_DEPENDENT_SECONDARY_SCORER", 0.65
        return "SPOT_ROLE_CEILING_DEPENDENT", 0.52

    return "ROLE_UNKNOWN", 0.40


def _classify_archetype(
    prop_type: str,
    role_state: str,
    oss: int,
    rot_volt: int,
) -> str:
    """Map role + stability to an archetype label."""
    if rot_volt > 70 or oss < 45:
        return "VOLATILE"

    is_pra = is_pra_market({"prop_type": prop_type})
    is_primary = "PRIMARY" in role_state

    if is_pra:
        if is_primary and oss >= 70:
            return "FLOOR_DRIVEN_PRIMARY_PRA"
        if oss >= 60:
            return "BALANCED_PRA"
        return "CEILING_DEPENDENT_SECONDARY_SCORER"

    if "SPOT" in role_state or "BENCH" in role_state:
        return "SPOT_ROLE_CEILING_DEPENDENT"

    if is_primary:
        return "FLOOR_DRIVEN_PRIMARY" if oss >= 65 else "BALANCED_PRIMARY"

    return "BALANCED_SECONDARY"


def _apply_ceiling(row: dict[str, Any], ceiling: str) -> None:
    """Cap terminal_label at ceiling without downgrading REJECT labels."""
    _TIER: dict[str, int] = {
        "RESEARCH_INTEREST":    0,
        "MODEL_QUALIFIED_HOLD": 1,
        "MARKET_VERIFIED_HOLD": 2,
        "MONEY_QUALIFIED":      3,
        "FINAL_APPROVED":       4,
    }
    current = row.get("terminal_label")
    if current is None:
        return
    if current and current.upper().startswith("REJECT"):
        return  # never soften a hard reject
    if _TIER.get(current, 99) > _TIER.get(ceiling, 99):
        row["terminal_label"] = ceiling


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(row: dict[str, Any], enrichment: dict[str, Any] | None = None) -> None:
    """
    Per-row entry point.  Only acts on WNBA rows.

    Reads:
      enrichment["box_score_log"]      — list of per-game stat dicts
      row["role_status"]          — set by status_role gate (called before this)
      row["terminal_label"]       — existing label (not overwritten unless blocking)

    Writes:
      row["gates"]["wnba_opportunity_gate"]  — full gate report
      row["blockers"]                        — extended on gate failure
      row["terminal_label"]                  — set to REJECT/HOLD label on failure
      row["can_execute"]                     — always False
    """
    if not is_wnba_row(row):
        return

    row["can_execute"] = False
    row.setdefault("gates", {})
    row.setdefault("blockers", [])

    enr      = enrichment or {}
    game_log = enr.get("box_score_log") or []

    # -------------------------------------------------------------------
    # Extract per-game time-series (exclude DNPs: minutes < 3)
    # -------------------------------------------------------------------
    minutes_series: list[float] = []
    ast_series:     list[float] = []
    reb_series:     list[float] = []
    fga_series:     list[float] = []
    usage_series:   list[float] = []

    for game in game_log:
        if not isinstance(game, dict):
            continue
        min_val = _extract_float(game, ["MIN", "min", "minutes", "MP", "min_played"])
        if min_val is None or min_val < 3:
            continue  # DNP or garbage time

        minutes_series.append(min_val)

        ast = _extract_float(game, ["AST", "ast", "assists", "Ast"])
        if ast is not None:
            ast_series.append(ast)

        reb = _extract_float(game, ["REB", "reb", "rebounds", "TRB", "Reb"])
        if reb is not None:
            reb_series.append(reb)

        fga = _extract_float(game, ["FGA", "fga", "field_goal_attempts", "FGAttempts"])
        if fga is not None:
            fga_series.append(fga)

        usg = _extract_float(game, ["USG", "USG%", "usg_pct", "usage_rate", "Usage%"])
        if usg is not None:
            usage_series.append(usg / 100.0 if usg > 1 else usg)

    n_games = len(minutes_series)

    # -------------------------------------------------------------------
    # Insufficient data → soft hold  (with specific caller guidance)
    # -------------------------------------------------------------------
    if n_games < MIN_GAMES_REQUIRED:
        game_log_supplied = bool(enr.get("box_score_log"))
        if not game_log_supplied:
            # Caller did not supply game_log at all — give a clear action.
            hold_reason = (
                f"box_score_log not supplied in enrichment — "
                f"add enrichment.box_score_log (list of ≥{MIN_GAMES_REQUIRED} per-game "
                f"dicts with MIN, PTS/REB/AST, FGA, USG%) for a defensible score"
            )
            hold_tag = "WNBA_HOLD_ROLE_UNCERTAIN:game_log_missing"
        else:
            # game_log was present but too sparse (all DNPs, or only 1-2 games).
            hold_reason = (
                f"Need ≥{MIN_GAMES_REQUIRED} non-DNP games (MIN≥3); "
                f"got {n_games} qualifying games from the supplied box_score_log"
            )
            hold_tag = (
                f"WNBA_HOLD_ROLE_UNCERTAIN:insufficient_game_data"
                f":games={n_games}<{MIN_GAMES_REQUIRED}"
            )

        row["blockers"].append(hold_tag)
        row["gates"]["wnba_opportunity_gate"] = {
            "gate_passed":                False,
            "gate_label":                 LABEL_HOLD_ROLE_UNCERTAIN,
            "reason":                     hold_reason,
            "game_log_supplied":          game_log_supplied,
            "games_analyzed":             n_games,
            "games_required":             MIN_GAMES_REQUIRED,
            "opportunity_stability_score": None,
            "role_confidence":            None,
            "caller_action":              (
                None if game_log_supplied else
                "Supply enrichment.box_score_log with ≥5 non-DNP games to get a defensible score"
            ),
            "can_execute":                False,
        }
        _apply_ceiling(row, "MODEL_QUALIFIED_HOLD")
        return

    # -------------------------------------------------------------------
    # Compute stability scores
    # -------------------------------------------------------------------
    mean_min  = sum(minutes_series) / n_games
    min_floor = min(minutes_series)
    min_ceil  = max(minutes_series)

    min_stab   = _compute_stability_score(minutes_series)
    usage_stab = _compute_stability_score(usage_series) if len(usage_series) >= 3 else 55
    fga_stab   = _compute_stability_score(fga_series)   if len(fga_series)   >= 3 else 55
    ast_stab   = _compute_stability_score(ast_series)   if len(ast_series)   >= 3 else 55
    reb_stab   = _compute_stability_score(reb_series)   if len(reb_series)   >= 3 else 55
    rot_volt   = _compute_rotation_volatility(minutes_series)

    # Composite OSS — weighted by influence on WNBA prop outcomes
    # Minutes: 35%  Usage: 25%  FGA attempts: 20%  AST: 10%  REB: 10%
    oss = round(
        0.35 * min_stab +
        0.25 * usage_stab +
        0.20 * fga_stab +
        0.10 * ast_stab +
        0.10 * reb_stab
    )

    # -------------------------------------------------------------------
    # Role inference
    # -------------------------------------------------------------------
    mean_usg = (sum(usage_series) / len(usage_series)) if usage_series else None
    mean_ast = (sum(ast_series)   / len(ast_series))   if ast_series   else None
    role_state, role_confidence = _infer_role_state(row, mean_min, mean_usg, mean_ast)

    # -------------------------------------------------------------------
    # Archetype classification
    # -------------------------------------------------------------------
    prop_type = row.get("prop_type") or row.get("prop") or ""
    archetype = _classify_archetype(prop_type, role_state, oss, rot_volt)

    # Teammate dependency from enrichment (enricher may supply this)
    teammate_deps = (
        enr.get("primary_teammate_dependency") or
        row.get("primary_teammate_dependency") or
        []
    )

    # -------------------------------------------------------------------
    # Gate threshold evaluation
    # -------------------------------------------------------------------
    is_pra      = is_pra_market(row)
    oss_thresh  = THRESH_OSS_PRA if is_pra else THRESH_OSS_GENERAL

    hard_blocks:  list[str] = []
    soft_blocks:  list[str] = []

    if rot_volt > THRESH_ROT_VOLT_HARD:
        hard_blocks.append(
            f"{LABEL_REJECT_ROTATION}:rotation_volatility={rot_volt}>{THRESH_ROT_VOLT_HARD}"
        )

    if min_stab < THRESH_MIN_STAB:
        hard_blocks.append(
            f"{LABEL_REJECT_UNSTABLE}:minutes_stability={min_stab}<{THRESH_MIN_STAB}"
        )

    if oss < oss_thresh:
        hard_blocks.append(
            f"{LABEL_REJECT_UNSTABLE}:opportunity_stability_score={oss}<{oss_thresh}"
        )

    if role_confidence < THRESH_ROLE_CONF:
        soft_blocks.append(
            f"{LABEL_HOLD_ROLE_UNCERTAIN}:role_confidence={role_confidence:.3f}<{THRESH_ROLE_CONF}"
        )

    all_blocks = hard_blocks + soft_blocks
    gate_passed = len(hard_blocks) == 0

    # -------------------------------------------------------------------
    # Determine gate_label and terminal outcome
    # -------------------------------------------------------------------
    if hard_blocks:
        if any("ROTATION_VOLATILITY" in b for b in hard_blocks):
            gate_label     = LABEL_REJECT_ROTATION
            terminal_label = LABEL_REJECT_ROTATION
        else:
            gate_label     = LABEL_REJECT_UNSTABLE
            terminal_label = LABEL_REJECT_UNSTABLE

        row["terminal_label"] = terminal_label
        row["blockers"].extend(hard_blocks)
        if soft_blocks:
            row["blockers"].extend(soft_blocks)

    elif soft_blocks:
        gate_label = LABEL_HOLD_ROLE_UNCERTAIN
        row["blockers"].extend(soft_blocks)
        _apply_ceiling(row, "MODEL_QUALIFIED_HOLD")
    else:
        gate_label = "PASS"

    # -------------------------------------------------------------------
    # Stamp gate result
    # -------------------------------------------------------------------
    row["gates"]["wnba_opportunity_gate"] = {
        "gate_passed":                        gate_passed,
        "gate_label":                         gate_label,
        "expected_minutes":                   round(mean_min, 1),
        "minutes_floor":                      round(min_floor, 1),
        "minutes_ceiling":                    round(min_ceil, 1),
        "minutes_stability_score":            min_stab,
        "usage_stability_score":              usage_stab,
        "shot_attempt_stability_score":       fga_stab,
        "assist_opportunity_stability_score": ast_stab,
        "rebound_opportunity_stability_score": reb_stab,
        "rotation_volatility_score":          rot_volt,
        "opportunity_stability_score":        oss,
        "oss_threshold":                      oss_thresh,
        "role_state":                         role_state,
        "role_confidence":                    round(role_confidence, 3),
        "primary_teammate_dependency":        list(teammate_deps),
        "archetype":                          archetype,
        "blockers":                           all_blocks,
        "games_analyzed":                     n_games,
        "is_pra_market":                      is_pra,
        "can_execute":                        False,
    }


def get_gate_status() -> dict[str, Any]:
    """Return current gate configuration (for /wow/patch-flags)."""
    return {
        "patch":                  "PATCH-WNBA-001",
        "THRESH_OSS_GENERAL":     THRESH_OSS_GENERAL,
        "THRESH_OSS_PRA":         THRESH_OSS_PRA,
        "THRESH_ROLE_CONF":       THRESH_ROLE_CONF,
        "THRESH_MIN_STAB":        THRESH_MIN_STAB,
        "THRESH_ROT_VOLT_HARD":   THRESH_ROT_VOLT_HARD,
        "MIN_GAMES_REQUIRED":     MIN_GAMES_REQUIRED,
        "can_execute":            False,
    }


def log_opportunity_audit(
    row: dict[str, Any],
    session_id: str = "",
    research_run_id: str = "",
) -> bool:
    """
    Write the opportunity gate result to the opportunity_audits table.
    Called from the settle endpoint after a result is known.
    Silent on DB failure (never blocks a response).
    """
    _ensure_opp_table()
    gate = (row.get("gates") or {}).get("wnba_opportunity_gate")
    if not gate:
        return False

    try:
        import psycopg2
        import json
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        cur  = conn.cursor()

        event_date = row.get("event_date") or row.get("game_date") or row.get("slate_date")
        if isinstance(event_date, str) and event_date:
            try:
                from dateutil import parser as _dp  # type: ignore
                event_date = _dp.parse(event_date).date()
            except Exception:
                event_date = None

        cur.execute(
            """
            INSERT INTO opportunity_audits (
                session_id, research_run_id, row_id, player_name, event_date,
                stat_family, line, direction,
                expected_minutes, minutes_floor, minutes_ceiling,
                minutes_stability_score, usage_stability_score,
                shot_attempt_stability_score,
                assist_opportunity_stability_score,
                rebound_opportunity_stability_score,
                rotation_volatility_score, opportunity_stability_score,
                role_state, role_confidence, primary_teammate_dependency,
                archetype, gate_passed, gate_label, blockers
            ) VALUES (
                %s,%s,%s,%s,%s,
                %s,%s,%s,
                %s,%s,%s,
                %s,%s,
                %s,
                %s,
                %s,
                %s,%s,
                %s,%s,%s,
                %s,%s,%s,%s
            )
            ON CONFLICT DO NOTHING
            """,
            (
                session_id or None,
                research_run_id or None,
                row.get("row_id"),
                (row.get("player") or row.get("player_name") or "")[:200],
                event_date,
                (row.get("prop_type") or row.get("prop") or "")[:80],
                row.get("line"),
                (row.get("direction") or "")[:10],
                gate.get("expected_minutes"),
                gate.get("minutes_floor"),
                gate.get("minutes_ceiling"),
                gate.get("minutes_stability_score"),
                gate.get("usage_stability_score"),
                gate.get("shot_attempt_stability_score"),
                gate.get("assist_opportunity_stability_score"),
                gate.get("rebound_opportunity_stability_score"),
                gate.get("rotation_volatility_score"),
                gate.get("opportunity_stability_score"),
                (gate.get("role_state") or "")[:60],
                gate.get("role_confidence"),
                json.dumps(gate.get("primary_teammate_dependency") or []),
                (gate.get("archetype") or "")[:60],
                gate.get("gate_passed"),
                (gate.get("gate_label") or "")[:80],
                json.dumps(gate.get("blockers") or []),
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception:
        return False
