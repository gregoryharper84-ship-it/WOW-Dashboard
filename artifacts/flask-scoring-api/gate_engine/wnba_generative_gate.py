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

can_execute = False  # UNCONDITIONAL — never set True

_CEILING = PropLabel.MODEL_QUALIFIED_HOLD.value

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
    """Prevent stale/upstream probability fields leaking through a blocked WNBA row."""
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
    # The existing WNBA model may omit model_status on successful legacy paths.
    # Presence of calibrated output is sufficient evidence that it actually ran.
    return any(
        result.get(key) is not None
        for key in ("cal_selected", "cal_lower_bound", "raw_selected", "p_more", "p_over")
    ) or status in {"PASS", "COMPLETE", "COMPLETED", "SCORED"}


def run(row: dict[str, Any], enr: dict[str, Any] | None = None) -> None:
    """Gate entry point. Mutates ``row`` in-place; always can_execute=False."""
    row["can_execute"] = False

    if not _opp_engine.is_wnba_row(row):
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

    # Binding evidence-packet completeness check BEFORE model invocation.
    packet = _evidence.build(row, effective_enr)
    packet["settlement"] = _settlement_packet(row)
    row["gates"]["wnba_full_model_evidence"] = packet

    if not packet.get("model_input_ready"):
        blockers = list(packet.get("model_blockers") or [])
        blockers.insert(0, "RUN_INVALID_ACQUISITION_INCOMPLETE")
        _append_blockers(row, blockers)
        _clear_publishable_probability(row)
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

    # Stat support is a controlling-model capability gate, not a qualitative fallback.
    raw = str(
        row.get("stat_key") or row.get("stat_type") or row.get("prop_type")
        or row.get("prop") or ""
    ).upper().strip().replace(" ", "_")
    canonical = _gen._STAT_KEY_ALIASES.get(raw, raw)
    if canonical not in _gen.SUPPORTED_STAT_KEYS:
        result = _unsupported_result(canonical, packet)
        row["gates"]["wnba_generative"] = result
        _append_blockers(row, list(result["blockers"]))
        _clear_publishable_probability(row)
        row["terminal_label"] = PropLabel.REJECT_DATA_QUALITY.value
        row["can_execute"] = False
        return

    # Pass ESS/discernment forward without ever substituting raw L10 hit rate.
    role_valid = packet.get("role_valid_sample") or {}
    effective_enr["effective_sample_size"] = role_valid.get("effective_sample_size")
    effective_enr["role_valid_games"] = role_valid.get("role_valid_games")
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

    # Publish model probability whenever the model genuinely completed, even if
    # the candidate itself is later rejected for low probability. Evidence/model
    # completeness, not outcome attractiveness, controls publication.
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

    # Apply PROVISIONAL ceiling — downstream gates may add a stricter ceiling.
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
