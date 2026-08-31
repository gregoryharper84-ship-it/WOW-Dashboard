"""
gate_engine/mlb/pitcher_prob_ledger_adapter.py
WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — Step 4 (MLB pitcher adapter).

Translates the acquisition packet for MLB pitcher prop rows (K and Outs)
into a canonical ProbabilityLedgerInput (contract v1).

Mapping requirements
--------------------
- starts / game-log K/Outs values → l10_distribution component (last 10 starts)
- pitch_count / workload / fatigue → role_usage component proxy
- opponent_k_pct / handedness / park_factor / weather / starter_confirmed →
  normalized_evidence
- failure_path model inputs (primary/secondary/black-swan scenarios) always
  populated so gate_engine/failure_path.py never silently skips on empty
  enrichment (failure_path.py itself is NOT modified — inputs are written
  into the enrichment dict it consumes).
- Any field not obtainable from current providers (BBRef blocked from the
  legacy_platform host, FanGraphs 403) is explicitly listed in missing_fields, never
  silently omitted.
- stat_key routing: strikeouts → "strikeouts", outs → "pitcher_outs".
  Exact-match guard first — never a substring test — per the
  _is_counting_stat exclusion pattern in auto_game_log.py.

Stage-2 probability fields are copied from real model outputs (caller /
GPT-supplied ledger, row-level calibrated fields) — never fabricated.

can_execute=False unconditional; no terminal-label authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from ..prob_ledger_schema import (
    CONTRACT_VERSION,
    MARKET_STATUS_STALE_MARKET,
    ProbabilityLedgerInput,
)

can_execute              = False
PRODUCTION_AUTHORITY     = False
TERMINAL_LABEL_AUTHORITY = False

# Component weights — within prob_ledger.COMPONENT_BOUNDS.
_MARKET_WEIGHT = 0.45
_L10_WEIGHT    = 0.30
_ROLE_WEIGHT   = 0.15

# Canonical stat-key routing — EXACT-match table, never substring.
_CANONICAL_STAT_KEYS: dict[str, str] = {
    "K":                  "strikeouts",
    "SO":                 "strikeouts",
    "STRIKEOUTS":         "strikeouts",
    "PITCHER_STRIKEOUTS": "strikeouts",
    "OUTS":               "pitcher_outs",
    "PITCHER_OUTS":       "pitcher_outs",
    "PITCHING_OUTS":      "pitcher_outs",
}

SUPPORTED_STAT_KEYS = frozenset(_CANONICAL_STAT_KEYS.values())

# Providers known-unobtainable from this host (documented in memory/audits).
_BLOCKED_PROVIDERS = {"bbref": "BLOCKED_FROM_HOST", "fangraphs": "HTTP_403"}

# Evidence fields mapped into normalized_evidence when present, and listed in
# missing_fields when absent (explicitly absent, never silently dropped).
_CONTEXT_FIELDS = (
    "opponent_k_pct", "handedness", "park_factor", "weather", "starter_confirmed",
)


def canonical_stat_key(raw: Any) -> Optional[str]:
    """Exact-match canonical routing for pitcher stat keys."""
    key = str(raw or "").strip().upper().replace(" ", "_")
    return _CANONICAL_STAT_KEYS.get(key)


def _numeric_log(values: Any) -> Optional[list[float]]:
    if not isinstance(values, (list, tuple)) or not values:
        return None
    out: list[float] = []
    for v in values:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            out.append(float(v))
        elif isinstance(v, str):
            try:
                out.append(float(v.strip()))
            except ValueError:
                return None
        else:
            return None
    return out


def _build_failure_path_inputs(row: dict, enr: dict, stat_key: str) -> dict:
    """
    Unconditional failure-path model inputs (primary / secondary / black-swan
    scenarios) for MLB pitcher props.  Written into the enrichment dict that
    failure_path.run(row, enrichment=...) consumes; values reflect real
    acquisition evidence — flags are booleans about data presence, never
    fabricated probabilities.
    """
    starter_confirmed = enr.get("starter_confirmed")
    workload = enr.get("workload") or enr.get("pitch_count")
    return {
        "primary_scenario": {
            "name": "early_hook" if stat_key == "pitcher_outs" else "low_whiff_day",
            "evidence": {
                "pitch_count":     enr.get("pitch_count"),
                "workload":        workload,
                "fatigue":         enr.get("fatigue"),
                "starter_confirmed": starter_confirmed,
            },
        },
        "secondary_scenario": {
            "name": "opponent_contact_profile",
            "evidence": {
                "opponent_k_pct": enr.get("opponent_k_pct"),
                "handedness":     enr.get("handedness"),
                "park_factor":    enr.get("park_factor"),
            },
        },
        "black_swan_scenario": {
            "name": "scratch_or_weather",
            "evidence": {
                "starter_confirmed": starter_confirmed,
                "weather":           enr.get("weather"),
            },
        },
        "populated_by": "mlb_pitcher_prob_ledger_adapter",
        "contract_version": CONTRACT_VERSION,
    }


def build_ledger_input(row: dict[str, Any], enr: dict[str, Any]) -> ProbabilityLedgerInput:
    """Build the canonical ProbabilityLedgerInput for an MLB K / Outs row."""
    stat_key = canonical_stat_key(row.get("stat_key") or row.get("prop_type"))
    if stat_key is None:
        raise ValueError(
            f"MLB_PITCHER_ADAPTER_UNSUPPORTED_STAT:"
            f"{row.get('stat_key') or row.get('prop_type')!r}"
        )

    missing: list[str] = []
    provider_status: dict[str, str] = dict(_BLOCKED_PROVIDERS)

    # --- game log (last 10 starts) → l10_distribution -----------------------
    game_log = _numeric_log(enr.get("game_log"))
    l10_component: Optional[dict] = None
    if game_log is None:
        missing.append("l10_distribution")
        provider_status["mlb_stats_api"] = (
            str(enr.get("mlb_stats_api_status")) if enr.get("mlb_stats_api_status")
            else "NOT_ATTEMPTED"
        )
    else:
        l10 = game_log[:10]
        provider_status["mlb_stats_api"] = "RETRIEVED"
        l10_component = {
            "name":   "l10_distribution",
            "weight": _L10_WEIGHT,
            "value":  round(sum(l10) / len(l10), 3),
            "source": "mlb_stats_api_game_log",
            "l5":     game_log[:5],
            "l10":    l10,
            "sample_size": len(l10),
            "stat_key": stat_key,
        }

    # --- pitch count / workload / fatigue → role_usage proxy ----------------
    role_fields = {
        f: enr.get(f) for f in ("pitch_count", "workload", "fatigue", "leash", "starts")
        if enr.get(f) is not None
    }
    role_component: Optional[dict] = None
    if role_fields:
        pc = role_fields.get("pitch_count") or role_fields.get("workload")
        role_component = {
            "name":   "role_usage",
            "weight": _ROLE_WEIGHT,
            "value":  float(pc) if isinstance(pc, (int, float)) and not isinstance(pc, bool) else None,
            "source": "pitcher_context_proxy",
            "fields": role_fields,
        }
    else:
        missing.append("role_usage")

    # --- context → normalized_evidence (explicit absence tracking) ----------
    normalized_evidence: dict[str, Any] = {"stat_key": stat_key}
    for f in _CONTEXT_FIELDS:
        if enr.get(f) is not None:
            normalized_evidence[f] = enr.get(f)
        else:
            missing.append(f)

    # --- unconditional failure_path inputs -----------------------------------
    if not enr.get("failure_path_inputs"):
        enr["failure_path_inputs"] = _build_failure_path_inputs(row, enr, stat_key)
    normalized_evidence["failure_path_inputs_populated"] = True

    # --- market no-vig (separate lane) ---------------------------------------
    market_component: Optional[dict] = None
    nv_prob = (
        enr.get("market_no_vig_prob")
        or enr.get("no_vig_probability")
        or enr.get("sharp_no_vig_prob")
    )
    if nv_prob is not None:
        market_component = {
            "name":          "market_no_vig",
            "weight":        _MARKET_WEIGHT,
            "value":         float(nv_prob),
            "source":        "sportsbook_no_vig",
            "snapshot_line": enr.get("sportsbook_line"),
            "snapshot_at":   enr.get("market_snapshot_at"),
        }
    else:
        missing.append(MARKET_STATUS_STALE_MARKET)

    # --- Stage-2 fields — copied from real model outputs only ----------------
    supplied = enr.get("model_probability_ledger") or {}
    if not isinstance(supplied, dict):
        supplied = {}

    def _pick(*vals):
        for v in vals:
            if v is not None:
                return v
        return None

    raw_p = _pick(supplied.get("raw_probability"), row.get("raw_probability"))
    cal_p = _pick(supplied.get("calibrated_probability"), row.get("calibrated_probability"))
    lb    = _pick(supplied.get("lower_bound"), row.get("calibrated_probability_lower_bound"))
    # NEVER synthesized: upper_bound and model_timestamp must come from a real
    # model output.  Absence is preserved as a typed missing field — the
    # ledger stays incomplete until the actual model supplies them.
    ub    = _pick(supplied.get("upper_bound"),
                  row.get("calibrated_probability_upper_bound"))
    cal_method = supplied.get("calibration_method")
    model_ts   = _pick(
        supplied.get("model_timestamp"),
        row.get("model_timestamp"),
    )
    snap_id = _pick(supplied.get("source_snapshot_id"),
                    enr.get("source_snapshot_id"), row.get("source_snapshot_id"))

    for f_name, val in (
        ("raw_probability", raw_p), ("calibrated_probability", cal_p),
        ("lower_bound", lb), ("upper_bound", ub),
        ("model_timestamp", model_ts), ("source_snapshot_id", snap_id),
        ("calibration_method", cal_method),
    ):
        if val is None:
            missing.append(f_name)

    return ProbabilityLedgerInput(
        row_id=str(row.get("row_id") or ""),
        event_id=row.get("event_id"),
        sport="MLB",
        market=str(row.get("prop_type") or ""),
        stat_key=stat_key,
        direction=str(row.get("direction") or ""),
        player_id=str(enr.get("player_id") or row.get("player_id") or "") or None,
        line=row.get("line"),
        source_timestamps={"adapter_built_at": datetime.now(timezone.utc).isoformat()},
        provenance={"adapter": "mlb_pitcher_prob_ledger_adapter",
                    "contract_version": CONTRACT_VERSION},
        raw_evidence={"game_log_len": len(game_log) if game_log else 0},
        normalized_evidence=normalized_evidence,
        acquisition_status="RETRIEVED" if (l10_component or role_component) else "ATTEMPTED",
        provider_status=provider_status,
        raw_probability=raw_p,
        calibrated_probability=cal_p,
        lower_bound=lb,
        upper_bound=ub,
        model_timestamp=model_ts,
        source_snapshot_id=snap_id,
        calibration_method=cal_method,
        market_no_vig=market_component,
        l10_distribution=l10_component,
        role_usage=role_component,
        missing_fields=missing,
    )
