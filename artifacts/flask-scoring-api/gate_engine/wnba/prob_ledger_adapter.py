"""
gate_engine/wnba/prob_ledger_adapter.py
WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — Step 3 (WNBA ingestion adapter).

Translates the raw ESPN/BDL acquisition packet for a WNBA player prop row
into a canonical ProbabilityLedgerInput (contract v1).

Rules
-----
- Runs AFTER gate_engine/wnba_enrichment_contract.py validation, never
  instead of it (type mismatches are rejected with
  WNBA_ENRICHMENT_TYPE_MISMATCH before this adapter maps anything).
- game_log must be a numeric sequence; numeric strings are coerced.
- box_score_log must be a list of dicts.
- ESPN per-game records → canonical L5/L10 arrays → l10_distribution
  component.  Retrieved data is never silently dropped.
- role/minutes/usage/opportunity fields from box_score_log → role_usage.
- Absent market no-vig → market_no_vig=None + STALE_MARKET recorded in
  missing_fields; the sporting-model component is NOT failed.
- provider_status is recorded for every source attempted.
- Stage-2 probability fields are copied from real model outputs
  (wnba_generative gate result / caller-supplied ledger) — never fabricated.

can_execute=False unconditional; no terminal-label authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .. import wnba_enrichment_contract as _wec
from ..prob_ledger_schema import (
    CONTRACT_VERSION,
    MARKET_STATUS_STALE_MARKET,
    ProbabilityLedgerInput,
)

can_execute              = False
PRODUCTION_AUTHORITY     = False
TERMINAL_LABEL_AUTHORITY = False

# Adapter-level typed error (module-level constant, mirrors the enrichment
# contract's canonical code).
WNBA_ENRICHMENT_TYPE_MISMATCH = _wec.ERROR_CODE

# Component weights — within prob_ledger.COMPONENT_BOUNDS.
_MARKET_WEIGHT = 0.45
_L10_WEIGHT    = 0.30
_ROLE_WEIGHT   = 0.15

_ROLE_BOX_FIELDS = (
    "minutes", "min", "usage", "usage_rate", "usg",
    "possessions", "touches", "role", "starter",
    "fga", "fta", "reb_chances", "opportunities",
)


def _coerce_numeric_seq(values: Any) -> tuple[Optional[list[float]], Optional[str]]:
    """Coerce a game_log to list[float]; numeric strings accepted."""
    if not isinstance(values, (list, tuple)):
        return None, f"game_log:not_a_sequence:{type(values).__name__}"
    out: list[float] = []
    for i, v in enumerate(values):
        if isinstance(v, bool):
            return None, f"game_log[{i}]:bool_not_numeric"
        if isinstance(v, (int, float)):
            out.append(float(v))
            continue
        if isinstance(v, str):
            try:
                out.append(float(v.strip()))
                continue
            except ValueError:
                return None, f"game_log[{i}]:non_numeric_string:{v!r}"
        return None, f"game_log[{i}]:invalid_type:{type(v).__name__}"
    return out, None


def _extract_role_usage(box_score_log: list[dict]) -> Optional[dict]:
    """Aggregate role/minutes/usage/opportunity evidence from box_score_log."""
    if not box_score_log:
        return None
    collected: dict[str, list[float]] = {}
    extras: dict[str, Any] = {}
    for game in box_score_log:
        if not isinstance(game, dict):
            continue
        for f in _ROLE_BOX_FIELDS:
            if f not in game:
                continue
            v = game[f]
            if isinstance(v, bool):
                extras[f] = v
            elif isinstance(v, (int, float)):
                collected.setdefault(f, []).append(float(v))
            elif isinstance(v, str):
                try:
                    collected.setdefault(f, []).append(float(v))
                except ValueError:
                    extras[f] = v
    if not collected and not extras:
        return None
    averages = {f: round(sum(vs) / len(vs), 3) for f, vs in collected.items() if vs}
    return {
        "name":       "role_usage",
        "weight":     _ROLE_WEIGHT,
        "value":      averages.get("minutes") or averages.get("min"),
        "source":     "espn_box_score_log",
        "averages":   averages,
        "categorical": extras,
        "games":      len(box_score_log),
    }


def build_ledger_input(row: dict[str, Any], enr: dict[str, Any]) -> ProbabilityLedgerInput:
    """
    Build the canonical ProbabilityLedgerInput for a WNBA player prop row.

    Raises ValueError(WNBA_ENRICHMENT_TYPE_MISMATCH ...) on enrichment-type
    violations (same contract as wnba_enrichment_contract.validate).
    """
    # Preserve the existing enrichment-contract validation — run first.
    ok, code, detail = _wec.validate(enr)
    if not ok:
        raise ValueError(f"{code}: {detail}")

    missing: list[str] = []
    provider_status: dict[str, str] = {}

    # Provider status for every source attempted
    acq = enr.get("acquisition_packet") or enr.get("wnba_acquisition") or {}
    for src in ("espn", "bdl", "fallback"):
        st = None
        if isinstance(acq, dict):
            st = (acq.get("provider_status") or {}).get(src) or acq.get(f"{src}_status")
        if st is None and enr.get(f"{src}_status") is not None:
            st = enr.get(f"{src}_status")
        provider_status[src] = str(st) if st is not None else "NOT_ATTEMPTED"
    if enr.get("game_log") is not None and provider_status["espn"] == "NOT_ATTEMPTED":
        provider_status["espn"] = "RETRIEVED"

    # --- game_log → L5/L10 arrays → l10_distribution component -------------
    l10_component: Optional[dict] = None
    game_log_raw = enr.get("game_log")
    if game_log_raw is None:
        missing.append("l10_distribution")
    else:
        seq, err = _coerce_numeric_seq(game_log_raw)
        if err is not None:
            raise ValueError(f"{WNBA_ENRICHMENT_TYPE_MISMATCH}: {err}")
        l10 = [float(v) for v in seq[:10]]
        l5  = [float(v) for v in seq[:5]]
        if not l10:
            missing.append("l10_distribution")
        else:
            l10_component = {
                "name":   "l10_distribution",
                "weight": _L10_WEIGHT,
                "value":  round(sum(l10) / len(l10), 3),
                "source": "espn_game_log",
                "l5":     l5,
                "l10":    l10,
                "sample_size": len(l10),
            }

    # --- box_score_log → role_usage component ------------------------------
    box_log = enr.get("box_score_log")
    role_component = _extract_role_usage(box_log) if isinstance(box_log, list) else None
    if role_component is None:
        missing.append("role_usage")

    # --- market no-vig (separate lane; absence never fails the model) ------
    market_component: Optional[dict] = None
    nv_prob = (
        enr.get("market_no_vig_prob")
        or enr.get("no_vig_probability")
        or enr.get("sharp_no_vig_prob")
    )
    if nv_prob is None:
        gen = (row.get("gates") or {}).get("wnba_generative") or {}
        nv_prob = gen.get("market_no_vig_prob")
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

    # --- Stage-2 fields — copied from real model output only ---------------
    gen = (row.get("gates") or {}).get("wnba_generative") or {}
    supplied = enr.get("model_probability_ledger") or {}
    if not isinstance(supplied, dict):
        supplied = {}

    def _pick(*vals):
        for v in vals:
            if v is not None:
                return v
        return None

    raw_p = _pick(supplied.get("raw_probability"), gen.get("raw_selected"))
    cal_p = _pick(supplied.get("calibrated_probability"),
                  row.get("calibrated_probability"), gen.get("cal_selected"))
    lb    = _pick(supplied.get("lower_bound"),
                  row.get("calibrated_probability_lower_bound"),
                  gen.get("cal_lower_bound"))
    # NEVER synthesized: upper_bound and model_timestamp must come from a real
    # model output (supplied ledger / row / generative gate result).  Absence
    # is preserved as a typed missing field — the ledger stays incomplete
    # until the actual model supplies them.
    ub    = _pick(supplied.get("upper_bound"),
                  row.get("calibrated_probability_upper_bound"),
                  gen.get("cal_upper_bound"))
    # calibration_method is provenance — copied only when the model/caller
    # actually emitted it; never inferred from the mere presence of a
    # calibrated probability.
    cal_method = _pick(
        supplied.get("calibration_method"),
        gen.get("calibration_method"),
    )
    model_ts = _pick(
        supplied.get("model_timestamp"),
        gen.get("model_timestamp"),
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
        sport="WNBA",
        market=str(row.get("prop_type") or ""),
        stat_key=str(row.get("stat_key") or row.get("prop_type") or ""),
        direction=str(row.get("direction") or ""),
        player_id=str(enr.get("player_id") or row.get("player_id") or "") or None,
        line=row.get("line"),
        source_timestamps={"adapter_built_at": datetime.now(timezone.utc).isoformat()},
        provenance={"adapter": "wnba_prob_ledger_adapter", "contract_version": CONTRACT_VERSION},
        raw_evidence={"game_log_len": len(game_log_raw) if isinstance(game_log_raw, list) else 0,
                      "box_score_log_len": len(box_log) if isinstance(box_log, list) else 0},
        normalized_evidence={
            "l5": (l10_component or {}).get("l5"),
            "l10": (l10_component or {}).get("l10"),
        },
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
