"""
wnba_generative_gate.py  —  WOW v16 Clean Core
Pipeline gate for the WNBA Generative Probability Engine.

Binding rule: no WNBA probability may be published until the visible Full Model
evidence packet proves exact-stat L10, contextual box-score L10, fresh role/status,
role-valid ESS, and opportunity/matchup/game-script readiness.

Market comparison and settlement/money evidence remain separate objectives: missing
exact market evidence may cap market/money lanes without erasing an otherwise complete
sporting model.

can_execute=False is unconditional.
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel
from .wnba import opportunity_engine as _opp_engine
from .wnba import generative_model as _gen
from .wnba import full_model_evidence_gate as _evidence

can_execute = False

_CEILING = PropLabel.MODEL_QUALIFIED_HOLD.value
_MIN_STRICT_ROLE_MATCH_GAMES = 4

_ABOVE_CEILING = frozenset({
    PropLabel.FINAL_APPROVED.value,
    PropLabel.MONEY_QUALIFIED.value,
    PropLabel.MARKET_VERIFIED_HOLD.value,
})

_PROBABILITY_FIELDS = (
    "raw_probability",
    "model_probability",
    "calibrated_probability",
    "calibrated_probability_lower_bound",
    "calibrated_probability_upper_bound",
    "lower_bound",
    "upper_bound",
)


def _clear_publishable_probability(row: dict[str, Any]) -> None:
    for key in _PROBABILITY_FIELDS:
        row.pop(key, None)
    row["probability_publication_allowed"] = False


def _append_blockers(row: dict[str, Any], blockers: list[str]) -> None:
    existing = set(row.setdefault("blockers", []))
    for blocker in blockers:
        if blocker not in existing:
            row["blockers"].append(blocker)
            existing.add(blocker)


def _settlement_packet(row: dict[str, Any]) -> dict[str, Any]:
    gates = row.get("gates") or {}
    settlement = (
        gates.get("settlement")
        or gates.get("settlement_audit")
        or gates.get("exact_line_settlement")
        or {}
    )
    status = (
        settlement.get("status")
        or settlement.get("gate_label")
        or settlement.get("label")
        or settlement.get("code")
        or "UNAVAILABLE"
    ) if isinstance(settlement, dict) else "UNAVAILABLE"
    normalized = str(status).upper()
    verified = normalized in {
        "PASS", "VERIFIED", "SETTLEMENT_VERIFIED", "EXACT_SETTLEMENT_VERIFIED"
    }
    return {
        "status": "VERIFIED" if verified else "UNAVAILABLE",
        "detail": settlement if isinstance(settlement, dict) else {},
        "blocks_model_probability": False,
    }


def _as_number(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _canonical_stat(row: dict[str, Any]) -> str:
    raw = str(
        row.get("stat_key") or row.get("stat_type") or row.get("prop_type")
        or row.get("prop") or ""
    ).upper().strip().replace(" ", "_")
    return _gen._STAT_KEY_ALIASES.get(raw, raw)


def _component_value(item: dict[str, Any], aliases: tuple[str, ...]) -> float | None:
    for key in aliases:
        if key in item:
            parsed = _as_number(item.get(key))
            if parsed is not None:
                return parsed
    return None


def _exact_value_from_structured_row(item: dict[str, Any], canonical: str) -> float | None:
    """Normalize a provider-neutral exact-stat row without inventing a category."""
    direct = _as_number(item.get("stat"))
    if direct is not None:
        return direct

    pts = _component_value(item, ("points", "PTS", "pts"))
    reb = _component_value(item, ("rebounds", "REB", "reb", "TRB"))
    ast = _component_value(item, ("assists", "AST", "ast"))
    stl = _component_value(item, ("steals", "STL", "stl"))
    blk = _component_value(item, ("blocks", "BLK", "blk"))
    tov = _component_value(item, ("turnovers", "TOV", "tov", "TO"))
    threepm = _component_value(item, ("three_pointers_made", "3PM", "FG3M", "3pm"))

    if canonical in {"PTS", "POINTS"}:
        return pts
    if canonical in {"REB", "REBOUNDS"}:
        return reb
    if canonical in {"AST", "ASSISTS"}:
        return ast
    if canonical in {"STL", "STEALS"}:
        return stl
    if canonical in {"BLK", "BLOCKS"}:
        return blk
    if canonical in {"TOV", "TO"}:
        return tov
    if canonical in {"3PM", "FG3M"}:
        return threepm
    if canonical in {"PRA", "PTS+REB+AST"} and None not in (pts, reb, ast):
        return float(pts + reb + ast)
    if canonical == "PTS+REB" and None not in (pts, reb):
        return float(pts + reb)
    if canonical == "PTS+AST" and None not in (pts, ast):
        return float(pts + ast)
    if canonical == "REB+AST" and None not in (reb, ast):
        return float(reb + ast)
    return None


def _normalize_exact_game_log(
    row: dict[str, Any], enrichment: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """
    Enforce the pre-model `game_log=list[number]` contract.

    Legacy provider-neutral structured exact-stat rows are normalized into a NEW
    numeric object. `box_score_log` is never overwritten or backfilled from this
    normalization, so contextual history remains a separate contract.
    """
    out = dict(enrichment)
    raw = out.get("game_log")
    if not isinstance(raw, list):
        return out, []
    if not raw or not any(isinstance(v, dict) for v in raw):
        return out, []
    if any(not isinstance(v, dict) for v in raw):
        return out, ["WNBA_ACQUISITION:GAME_LOG_MIXED_TYPES"]

    canonical = _canonical_stat(row)
    values: list[float] = []
    dropped = 0
    for item in raw:
        value = _exact_value_from_structured_row(item, canonical)
        if value is None:
            dropped += 1
        else:
            values.append(value)

    out["game_log_structured_source"] = raw
    out["game_log"] = values
    out["game_log_normalization_audit"] = {
        "source_type": "list[dict]",
        "target_type": "list[number]",
        "canonical_stat": canonical,
        "rows_in": len(raw),
        "values_out": len(values),
        "rows_unresolved": dropped,
        "box_score_log_untouched": True,
    }
    blockers: list[str] = []
    if dropped:
        blockers.append(
            f"WNBA_ACQUISITION:EXACT_GAME_LOG_NORMALIZATION_PARTIAL:{dropped}_ROWS_UNRESOLVED"
        )
    return out, blockers


def _evidence_row(row: dict[str, Any], enrichment: dict[str, Any]) -> dict[str, Any]:
    """Flatten already-observed role fields for evidence inspection only."""
    evidence_row = dict(row)
    role = row.get("role_status")
    if isinstance(role, dict):
        evidence_row["role_status"] = (
            role.get("usage_role") or role.get("role") or role.get("role_state")
        )
        evidence_row["role_timestamp"] = (
            role.get("role_timestamp") or enrichment.get("role_timestamp")
        )
        evidence_row["starter_flag"] = (
            role.get("starter_flag")
            if role.get("starter_flag") is not None
            else role.get("expected_start")
        )
        evidence_row["status"] = (
            role.get("active_status") or role.get("status") or row.get("status")
        )
        if enrichment.get("projected_minutes") is None and role.get("projected_minutes") is not None:
            enrichment["projected_minutes"] = role.get("projected_minutes")
    return evidence_row


def _enforce_strict_role_match(packet: dict[str, Any]) -> None:
    """Require multiple role/opportunity signals before a history row earns full-role credit."""
    role_valid = packet.get("role_valid_sample") or {}
    rows = role_valid.get("rows") if isinstance(role_valid, dict) else []
    strict_matches = 0
    for hist in rows or []:
        if hist.get("comparability_class") != "ROLE_MATCH":
            continue
        reasons = hist.get("reason") or []
        matched_dimensions = [
            reason for reason in reasons
            if reason in {"starter_match", "role_match", "minutes_band_match"}
        ]
        if len(matched_dimensions) >= 2:
            strict_matches += 1

    role_valid["strict_role_match_games"] = strict_matches
    role_valid["strict_role_match_floor"] = _MIN_STRICT_ROLE_MATCH_GAMES
    if strict_matches < _MIN_STRICT_ROLE_MATCH_GAMES:
        role_valid["status"] = "BLOCKED"
        blocker = (
            "WNBA_EVIDENCE:STRICT_ROLE_MATCH_SAMPLE_BLOCKED:"
            f"n={strict_matches}<{_MIN_STRICT_ROLE_MATCH_GAMES}"
        )
        packet.setdefault("model_blockers", []).append(blocker)
        packet["model_input_ready"] = False
        packet["probability_publication_allowed"] = False
        packet["terminal_ceiling"] = "REJECT_DATA_QUALITY"


def _unsupported_result(canonical: str, packet: dict[str, Any]) -> dict[str, Any]:
    blocker = (
        "WNBA_2PM_CONTROLLING_MODEL_UNSUPPORTED"
        if canonical in {"2PM", "2PTM", "TWO_POINTERS_MADE", "TWO_POINT_FIELD_GOALS_MADE"}
        else f"WNBA_CONTROLLING_MODEL_UNSUPPORTED:{canonical or 'UNKNOWN_STAT'}"
    )
    return {
        "can_execute": False,
        "model_status": "MODEL_UNAVAILABLE",
        "failure_class": "CONTROLLING_MODEL_UNAVAILABLE",
        "probability_publication_allowed": False,
        "blockers": [blocker],
        "final_label": "REJECT",
        "evidence_packet": packet,
    }


def _model_completed(result: dict[str, Any]) -> bool:
    status = str(result.get("model_status") or "").upper()
    if any(token in status for token in ("ERROR", "FAILED", "UNAVAILABLE", "NOT_STARTED")):
        return False
    return any(
        result.get(key) is not None
        for key in ("cal_selected", "cal_lower_bound", "raw_selected", "p_more", "p_over")
    ) or status in {"PASS", "COMPLETE", "COMPLETED", "SCORED"}


def run(row: dict[str, Any], enr: dict[str, Any] | None = None) -> None:
    row["can_execute"] = False

    if not _opp_engine.is_wnba_row(row):
        return

    # Preserve the long-standing no-op contract for WNBA stat families that
    # are not governed by this generative specialist. Unsupported rows must
    # be routed elsewhere; they must not be relabeled as acquisition failures
    # by a model that does not control them.
    canonical = _canonical_stat(row)
    if canonical not in _gen.SUPPORTED_STAT_KEYS:
        return

    row.setdefault("gates", {})
    row.setdefault("blockers", [])

    effective_enr: dict[str, Any] = {}
    if enr:
        effective_enr.update(enr)
    per_row = row.get("enrichment") or {}
    if isinstance(per_row, dict):
        for key, value in per_row.items():
            effective_enr.setdefault(key, value)

    effective_enr, normalization_blockers = _normalize_exact_game_log(row, effective_enr)
    _append_blockers(row, normalization_blockers)
    evidence_row = _evidence_row(row, effective_enr)

    packet = _evidence.build(evidence_row, effective_enr)
    packet["settlement"] = _settlement_packet(row)
    packet["exact_game_log_normalization"] = effective_enr.get("game_log_normalization_audit")
    _enforce_strict_role_match(packet)
    row["gates"]["wnba_full_model_evidence"] = packet

    if normalization_blockers:
        packet.setdefault("model_blockers", []).extend(normalization_blockers)
        packet["model_input_ready"] = False
        packet["probability_publication_allowed"] = False

    if not packet.get("model_input_ready"):
        blockers = list(dict.fromkeys(packet.get("model_blockers") or []))
        blockers.insert(0, "RUN_INVALID_ACQUISITION_INCOMPLETE")
        _append_blockers(row, blockers)
        _clear_publishable_probability(row)
        # Preserve any lower/stricter terminal ceiling already earned by an
        # earlier gate (for example market-adverse push/loss). Acquisition
        # incompleteness may add blockers but must not erase prior provenance.
        if not row.get("terminal_label"):
            row["terminal_label"] = PropLabel.REJECT_DATA_QUALITY.value
        row["gates"]["wnba_generative"] = {
            "can_execute": False,
            "model_status": "NOT_STARTED",
            "status": "RUN_INCOMPLETE",
            "failure_class": "RUN_INVALID_ACQUISITION_INCOMPLETE",
            "probability_publication_allowed": False,
            "blockers": blockers,
            "final_label": "REJECT",
            "evidence_packet": packet,
        }
        row["can_execute"] = False
        return

    role_valid = packet.get("role_valid_sample") or {}
    effective_enr["effective_sample_size"] = role_valid.get("effective_sample_size")
    effective_enr["role_valid_games"] = role_valid.get("strict_role_match_games")
    effective_enr["l10_discernment_status"] = role_valid.get("status")
    effective_enr["weighted_comparable_l10_prior"] = None

    try:
        result = _gen.score(row, effective_enr)
    except Exception as exc:
        result = {
            "can_execute": False,
            "model_status": "GENERATIVE_MODEL_ERROR",
            "blockers": [
                f"WNBA_GENERATIVE_ERROR:{type(exc).__name__}:{str(exc)[:120]}"
            ],
            "final_label": "REJECT",
        }

    completed = _model_completed(result)
    result["evidence_packet"] = _evidence.attach_model_output(packet, result)
    result["probability_publication_allowed"] = bool(
        packet.get("model_input_ready") and completed
    )
    result["can_execute"] = False
    row["gates"]["wnba_full_model_evidence"] = result["evidence_packet"]
    row["gates"]["wnba_generative"] = result

    _append_blockers(row, list(result.get("blockers") or []))

    if result["probability_publication_allowed"]:
        cal = result.get("cal_selected")
        if cal is not None:
            row["calibrated_probability"] = cal

        cal_lb = result.get("cal_lower_bound")
        if cal_lb is not None:
            row["calibrated_probability_lower_bound"] = cal_lb
        row["probability_publication_allowed"] = True
    else:
        _clear_publishable_probability(row)

    cur = row.get("terminal_label") or ""
    if cur in _ABOVE_CEILING:
        row["terminal_label"] = _CEILING
        _append_blockers(
            row, ["WNBA_GENERATIVE:PROVISIONAL_CEILING:MODEL_QUALIFIED_HOLD"]
        )

    final_lbl = result.get("final_label", "")
    if final_lbl == "REJECT":
        if not row.get("terminal_label"):
            row["terminal_label"] = PropLabel.REJECT_DATA_QUALITY.value
        lb_value = result.get("cal_lower_bound")
        try:
            lb_text = f"{float(lb_value):.3f}" if lb_value is not None else "n/a"
        except (TypeError, ValueError):
            lb_text = "n/a"
        _append_blockers(row, [f"WNBA_GENERATIVE:REJECT:lb={lb_text}"])
    elif final_lbl in ("HOLD", "WATCH") and not row.get("terminal_label"):
        row["terminal_label"] = _CEILING

    row["can_execute"] = False
