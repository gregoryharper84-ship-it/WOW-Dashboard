"""WNBA Full Model evidence-packet gate — WOW v16 Clean Core.

Binding pre-model gate for WNBA player props.  This module deliberately keeps
exact-stat history (``game_log``) separate from contextual box-score history
(``box_score_log``).  Neither object may satisfy the other's requirement.

The gate is evidence-only: it never creates a probability, market edge, stake,
or execution instruction.  ``can_execute`` is always False.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any

can_execute = False

RUN_INVALID = "RUN_INVALID_ACQUISITION_INCOMPLETE"
RUN_INCOMPLETE = "RUN_INCOMPLETE"
PASS = "PASS"

_MIN_EXACT_GAMES = 10
_MIN_BOX_GAMES = 10
_MIN_ROLE_VALID_ESS = 4.0


def _float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _first_number(mapping: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in mapping:
            val = _float(mapping.get(key))
            if val is not None:
                return val
    return None


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _stat_key(row: dict[str, Any]) -> str:
    return str(
        row.get("stat_key") or row.get("stat_type") or row.get("prop_type")
        or row.get("prop") or ""
    ).upper().strip().replace(" ", "_")


def _line(row: dict[str, Any]) -> float | None:
    for key in ("line", "threshold", "exact_line"):
        val = _float(row.get(key))
        if val is not None:
            return val
    return None


def _exact_side_ledger(values: list[float], line: float | None) -> dict[str, Any]:
    if line is None:
        return {
            "more": None, "less": None, "push": None,
            "more_rate": None, "less_rate": None, "push_rate": None,
        }
    more = sum(v > line for v in values)
    less = sum(v < line for v in values)
    push = sum(v == line for v in values)
    n = len(values)
    return {
        "more": more,
        "less": less,
        "push": push,
        "more_rate": round(more / n, 6) if n else None,
        "less_rate": round(less / n, 6) if n else None,
        "push_rate": round(push / n, 6) if n else None,
    }


def _numeric_exact_log(enrichment: dict[str, Any]) -> tuple[list[float], list[str]]:
    blockers: list[str] = []
    raw = enrichment.get("game_log")
    if not isinstance(raw, list):
        return [], ["WNBA_EVIDENCE:GAME_LOG_MISSING_OR_NOT_LIST"]
    if any(isinstance(v, dict) for v in raw):
        return [], ["WNBA_EVIDENCE:GAME_LOG_WRONG_TYPE:REQUIRES_LIST_NUMBER"]
    vals: list[float] = []
    for value in raw:
        parsed = _float(value)
        if parsed is None:
            blockers.append("WNBA_EVIDENCE:GAME_LOG_CONTAINS_NON_NUMERIC_VALUE")
            continue
        vals.append(parsed)
    if len(vals) < _MIN_EXACT_GAMES:
        blockers.append(
            f"WNBA_EVIDENCE:EXACT_L10_INCOMPLETE:n={len(vals)}<{_MIN_EXACT_GAMES}"
        )
    return vals, blockers


def _box_log(row: dict[str, Any], enrichment: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], str]:
    """Return contextual box-score rows without ever aliasing numeric game_log."""
    raw = enrichment.get("box_score_log")
    source = "enrichment.box_score_log"
    if not isinstance(raw, list):
        packet = (
            ((row.get("gates") or {}).get("wnba_evidence_acquisition") or {})
            .get("opportunity_packet") or {}
        )
        raw = packet.get("box_score_log")
        source = "wnba_evidence_acquisition.opportunity_packet.box_score_log"

    if not isinstance(raw, list):
        return [], ["WNBA_EVIDENCE:BOX_SCORE_LOG_MISSING_OR_NOT_LIST"], source
    if raw is enrichment.get("game_log"):
        return [], ["WNBA_EVIDENCE:GAME_LOG_BOX_SCORE_ALIAS_PROHIBITED"], source
    if any(not isinstance(v, dict) for v in raw):
        return [], ["WNBA_EVIDENCE:BOX_SCORE_LOG_WRONG_TYPE:REQUIRES_LIST_DICT"], source

    rows = list(raw)
    blockers: list[str] = []
    if len(rows) < _MIN_BOX_GAMES:
        blockers.append(
            f"WNBA_EVIDENCE:BOX_SCORE_L10_INCOMPLETE:n={len(rows)}<{_MIN_BOX_GAMES}"
        )

    required = {
        "minutes": ("minutes", "MIN", "min", "MP", "min_played"),
        "points": ("points", "PTS", "pts"),
        "rebounds": ("rebounds", "REB", "reb", "TRB"),
        "assists": ("assists", "AST", "ast"),
        "field_goal_attempts": ("field_goal_attempts", "FGA", "fga", "FGAttempts"),
        "usage": ("usage", "usage_rate", "USG", "USG%", "usg_pct", "usage_or_opportunity"),
    }
    for idx, item in enumerate(rows[-_MIN_BOX_GAMES:]):
        for field, aliases in required.items():
            value = _first_present(item, aliases)
            if field == "usage" and isinstance(value, dict):
                value = _first_present(value, ("usage", "usage_rate", "USG", "USG%"))
            if value is None or _float(value) is None:
                blockers.append(f"WNBA_EVIDENCE:BOX_SCORE_ROW_{idx}:{field.upper()}_MISSING")
    return rows, blockers, source


def _current_role(row: dict[str, Any], enrichment: dict[str, Any]) -> dict[str, Any]:
    packet = (
        ((row.get("gates") or {}).get("wnba_evidence_acquisition") or {})
        .get("opportunity_packet") or {}
    )
    role_obj = packet.get("role_status") if isinstance(packet.get("role_status"), dict) else {}
    return {
        "status": _first_present(role_obj, ("status",)) or row.get("status") or row.get("player_status"),
        "role": _first_present(role_obj, ("usage_role", "role")) or row.get("role_status"),
        "starter_flag": _first_present(role_obj, ("starter_flag", "is_starter")) or row.get("starter_flag"),
        "projected_minutes": _first_number(role_obj, ("projected_minutes", "minutes"))
            or _first_number(enrichment, ("projected_minutes", "expected_minutes")),
        "role_timestamp": _first_present(role_obj, ("role_timestamp",))
            or row.get("role_timestamp") or enrichment.get("role_timestamp"),
    }


def _role_discernment(
    box_rows: list[dict[str, Any]], current_role: dict[str, Any]
) -> dict[str, Any]:
    """Auditable role comparability; raw hit rate is never converted to probability."""
    current_starter = current_role.get("starter_flag")
    current_role_name = str(current_role.get("role") or "").strip().upper()
    projected_minutes = _float(current_role.get("projected_minutes"))

    weights: list[float] = []
    classifications: list[dict[str, Any]] = []
    for item in box_rows[-_MIN_BOX_GAMES:]:
        starter = _first_present(item, ("starter_flag", "is_starter", "starter"))
        role_name = str(_first_present(item, ("role", "role_state")) or "").strip().upper()
        minutes = _first_number(item, ("minutes", "MIN", "min", "MP", "min_played"))

        signals = 0
        matched = 0
        reasons: list[str] = []
        if current_starter not in (None, "") and starter not in (None, ""):
            signals += 1
            same = str(starter).strip().lower() == str(current_starter).strip().lower()
            matched += int(same)
            reasons.append("starter_match" if same else "starter_mismatch")
        if current_role_name and role_name:
            signals += 1
            same = current_role_name == role_name
            matched += int(same)
            reasons.append("role_match" if same else "role_mismatch")
        if projected_minutes and minutes is not None and projected_minutes > 0:
            signals += 1
            ratio = minutes / projected_minutes
            same = 0.75 <= ratio <= 1.25
            matched += int(same)
            reasons.append("minutes_band_match" if same else "minutes_band_mismatch")

        if signals == 0:
            weight = 0.0
            cls = "NON_COMPARABLE"
            reasons.append("no_role_comparability_dimensions")
        else:
            score = matched / signals
            if score >= 0.999:
                weight, cls = 1.0, "ROLE_MATCH"
            elif score >= 0.5:
                weight, cls = 0.5, "ROLE_PARTIAL_MATCH"
            else:
                weight, cls = 0.0, "ROLE_MISMATCH"
        weights.append(weight)
        classifications.append({
            "game_date": _first_present(item, ("game_date", "date", "GAME_DATE")),
            "opponent": _first_present(item, ("opponent", "opp", "OPP")),
            "minutes": minutes,
            "comparability_class": cls,
            "weight": weight,
            "reason": reasons,
            "source_timestamp": _first_present(item, ("source_timestamp", "retrieved_at", "timestamp")),
        })

    total = sum(weights)
    denom = sum(w * w for w in weights)
    ess = (total * total / denom) if denom > 0 else 0.0
    role_valid = sum(w >= 1.0 for w in weights)
    partial = sum(0.0 < w < 1.0 for w in weights)
    status = "PASS" if ess >= _MIN_ROLE_VALID_ESS else "BLOCKED"
    return {
        "status": status,
        "raw_games_retrieved": len(classifications),
        "role_valid_games": role_valid,
        "partial_comparable_games": partial,
        "non_comparable_games": sum(w == 0 for w in weights),
        "effective_sample_size": round(ess, 3),
        "ess_floor": _MIN_ROLE_VALID_ESS,
        "rows": classifications,
    }


def _context_value(row: dict[str, Any], enrichment: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    val = _first_present(enrichment, aliases)
    if val is not None:
        return val
    return _first_present(row, aliases)


def _opportunity_packet(row: dict[str, Any], enrichment: dict[str, Any], stat_key: str) -> tuple[dict[str, Any], list[str]]:
    gate = (
        ((row.get("gates") or {}).get("wnba_opportunity_gate") or {})
        or (enrichment.get("opportunity_ledger") or {})
    )
    packet = {
        "gate_label": gate.get("gate_label"),
        "gate_passed": gate.get("gate_passed"),
        "projected_minutes": gate.get("expected_minutes")
            or _context_value(row, enrichment, ("projected_minutes", "expected_minutes")),
        "usage_stability_score": gate.get("usage_stability_score"),
        "shot_attempt_stability_score": gate.get("shot_attempt_stability_score"),
        "opportunity_stability_score": gate.get("opportunity_stability_score"),
        "role_state": gate.get("role_state"),
        "role_confidence": gate.get("role_confidence"),
        "pace": _context_value(row, enrichment, ("projected_pace", "pace", "game_pace", "pace_factor")),
        "opponent_matchup": _context_value(row, enrichment, ("opponent_defense", "opponent_matchup", "matchup", "defensive_rating")),
        "rest": _context_value(row, enrichment, ("rest_days", "days_rest", "back_to_back", "b2b")),
        "blowout_risk": _context_value(row, enrichment, ("blowout_probability", "blowout_risk", "projected_margin", "spread")),
        "game_script": _context_value(row, enrichment, ("game_script", "script_adjustment", "game_script_adjustment")),
    }
    blockers: list[str] = []
    if gate.get("gate_passed") is not True or gate.get("gate_label") != "PASS":
        blockers.append("WNBA_EVIDENCE:OPPORTUNITY_GATE_NOT_PASS")
    for key in ("projected_minutes", "usage_stability_score", "shot_attempt_stability_score"):
        if packet.get(key) is None:
            blockers.append(f"WNBA_EVIDENCE:OPPORTUNITY_{key.upper()}_MISSING")
    for key in ("pace", "opponent_matchup", "rest", "blowout_risk", "game_script"):
        if packet.get(key) in (None, "", [], {}):
            blockers.append(f"WNBA_EVIDENCE:CONTEXT_{key.upper()}_MISSING")

    if stat_key in {"2PM", "2PTM", "TWO_POINTERS_MADE", "TWO_POINT_FIELD_GOALS_MADE"}:
        packet["two_point_attempts_per_minute"] = _context_value(
            row, enrichment, ("two_point_attempts_per_minute", "2pa_per_min", "twopa_per_min")
        )
        packet["script_adjusted_two_point_opportunity"] = _context_value(
            row, enrichment, ("script_adjusted_two_point_opportunity", "two_point_script_adjustment")
        )
        if packet["two_point_attempts_per_minute"] is None:
            blockers.append("WNBA_EVIDENCE:2PM_TWO_POINT_ATTEMPTS_PER_MINUTE_MISSING")
        if packet["script_adjusted_two_point_opportunity"] is None:
            blockers.append("WNBA_EVIDENCE:2PM_SCRIPT_ADJUSTED_OPPORTUNITY_MISSING")
    return packet, blockers


def _market_status(row: dict[str, Any]) -> dict[str, Any]:
    gates = row.get("gates") or {}
    market = gates.get("market") or gates.get("market_data") or gates.get("market_sanity") or {}
    status = market.get("status") or market.get("gate_label") or market.get("label") or "UNAVAILABLE"
    verified = str(status).upper() in {"PASS", "VERIFIED", "MARKET_VERIFIED"}
    return {
        "status": "VERIFIED" if verified else "UNAVAILABLE",
        "detail": market,
        "blocks_model_probability": False,
    }


def build(row: dict[str, Any], enrichment: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the mandatory visible pre-model WNBA evidence packet."""
    enrichment = dict(enrichment or {})
    now = datetime.now(timezone.utc).isoformat()
    stat_key = _stat_key(row)
    line = _line(row)
    model_blockers: list[str] = []

    exact_values, exact_blockers = _numeric_exact_log(enrichment)
    model_blockers.extend(exact_blockers)
    l10 = exact_values[-_MIN_EXACT_GAMES:] if len(exact_values) >= _MIN_EXACT_GAMES else exact_values[:]
    l5 = l10[-5:] if len(l10) >= 5 else l10[:]

    box_rows, box_blockers, box_source = _box_log(row, enrichment)
    model_blockers.extend(box_blockers)

    role = _current_role(row, enrichment)
    role_ts_gate = ((row.get("gates") or {}).get("role_timestamp") or {})
    if not role.get("role_timestamp"):
        model_blockers.append("WNBA_EVIDENCE:ROLE_TIMESTAMP_MISSING")
    if role_ts_gate.get("role_staleness") != "FRESH" or role_ts_gate.get("passed") is not True:
        model_blockers.append(
            "WNBA_EVIDENCE:ROLE_TIMESTAMP_NOT_FRESH:"
            f"{role_ts_gate.get('role_staleness', 'UNKNOWN')}"
        )

    role_disc = _role_discernment(box_rows, role)
    if role_disc["status"] != "PASS":
        model_blockers.append(
            "WNBA_EVIDENCE:ROLE_VALID_SAMPLE_BLOCKED:"
            f"ESS={role_disc['effective_sample_size']}<{role_disc['ess_floor']}"
        )

    opportunity, opp_blockers = _opportunity_packet(row, enrichment, stat_key)
    model_blockers.extend(opp_blockers)

    exact_l10 = _exact_side_ledger(l10, line)
    exact_l5 = _exact_side_ledger(l5, line)
    l10_mean = statistics.fmean(l10) if l10 else None
    l10_median = statistics.median(l10) if l10 else None
    l5_mean = statistics.fmean(l5) if l5 else None
    divergence_pp = None
    if exact_l10["more_rate"] is not None and exact_l5["more_rate"] is not None:
        divergence_pp = round(abs(exact_l5["more_rate"] - exact_l10["more_rate"]), 6)

    acquisition_gate = ((row.get("gates") or {}).get("wnba_evidence_acquisition") or {})
    source_timestamps = {
        "packet_built_at": now,
        "acquisition_run": ((acquisition_gate.get("acquisition_audit") or {}).get("run_ts")),
        "role_timestamp": role.get("role_timestamp"),
        "box_score_source_timestamps": [
            _first_present(item, ("source_timestamp", "retrieved_at", "timestamp"))
            for item in box_rows[-_MIN_BOX_GAMES:]
            if _first_present(item, ("source_timestamp", "retrieved_at", "timestamp")) is not None
        ],
    }

    # Deduplicate while preserving order.
    model_blockers = list(dict.fromkeys(model_blockers))
    model_ready = not model_blockers
    market = _market_status(row)

    return {
        "gate": "WNBA_FULL_MODEL_EVIDENCE_PACKET",
        "status": PASS if model_ready else RUN_INCOMPLETE,
        "failure_class": None if model_ready else RUN_INVALID,
        "model_input_ready": model_ready,
        "probability_publication_allowed": model_ready,
        "evidence_packet_visible": True,
        "exact_board_identity": {
            "player": row.get("player") or row.get("player_name"),
            "event": row.get("event") or row.get("event_id"),
            "opponent": row.get("opponent"),
            "stat": stat_key,
            "line": line,
            "direction": row.get("direction") or row.get("side"),
        },
        "historical_ledger": {
            "game_log_type": "list[number]" if exact_values else "INVALID_OR_EMPTY",
            "box_score_log_type": "list[dict]" if box_rows else "INVALID_OR_EMPTY",
            "game_log_box_score_log_are_distinct_contracts": True,
            "box_score_source": box_source,
            "l10_values": l10,
            "l5_values": l5,
            "sample_count": len(l10),
            "l10_mean": round(l10_mean, 4) if l10_mean is not None else None,
            "l10_median": round(float(l10_median), 4) if l10_median is not None else None,
            "l5_mean": round(l5_mean, 4) if l5_mean is not None else None,
            "l5_trend_vs_l10_mean": (
                round(l5_mean - l10_mean, 4)
                if l5_mean is not None and l10_mean is not None else None
            ),
            "l10_exact_line_results": exact_l10,
            "l5_exact_line_results": exact_l5,
            "l5_l10_divergence_pp": divergence_pp,
            "l5_l10_divergence_flag": bool(divergence_pp is not None and divergence_pp > 0.20),
        },
        "role_status": role,
        "role_timestamp_gate": role_ts_gate,
        "role_valid_sample": role_disc,
        "opportunity_ledger": opportunity,
        "matchup_game_script_model": {
            "status": "COMPLETE" if all(
                opportunity.get(k) not in (None, "", [], {})
                for k in ("pace", "opponent_matchup", "rest", "blowout_risk", "game_script")
            ) else "INCOMPLETE"
        },
        "market_comparison": market,
        "source_timestamps": source_timestamps,
        "model_blockers": model_blockers,
        "market_blockers": [] if market["status"] == "VERIFIED" else ["MARKET_COMPARISON_UNAVAILABLE"],
        "terminal_ceiling": None if model_ready else "REJECT_DATA_QUALITY",
        "can_execute": False,
    }


def attach_model_output(packet: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Attach post-model probability/calibration fields to the visible packet."""
    audit = dict(packet)
    audit["probability_ledger"] = {
        "more_probability": result.get("p_over") or result.get("p_more") or result.get("probability_more"),
        "less_probability": result.get("p_under") or result.get("p_less") or result.get("probability_less"),
        "push_probability": result.get("p_push") or result.get("probability_push") or 0.0,
        "raw_probability": result.get("raw_selected") or result.get("raw_probability"),
        "calibrated_probability": result.get("cal_selected") or result.get("calibrated_probability"),
        "lower_bound": result.get("cal_lower_bound") or result.get("calibrated_probability_lower_bound"),
        "upper_bound": result.get("cal_upper_bound") or result.get("calibrated_probability_upper_bound"),
        "calibration_method": result.get("calibration_method") or result.get("calibration_status"),
    }
    audit["model_output_visible"] = True
    audit["can_execute"] = False
    return audit
