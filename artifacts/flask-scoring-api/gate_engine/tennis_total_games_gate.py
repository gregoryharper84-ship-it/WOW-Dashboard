"""
gate_engine/tennis_total_games_gate.py

Pipeline gate for the WOW v16 Tennis Total Games lane.

Called from pipeline.py in the second per-row loop (after wnba_composite_gate,
before classifier.classify).  Only fires when sport == TENNIS and
stat_key == TOTAL_GAMES.  No-op for all other rows.

Stamps gate result onto row["gates"]["tennis_total_games"] and updates
the calibrated probability fields so the classifier and prob_ledger
consumers see the model output.

Applies MODEL_QUALIFIED_HOLD ceiling (PROVISIONAL model status per governance).
can_execute = False is unconditional.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

can_execute = False   # WOW governance: unconditional

_SPORT_VALUES  = frozenset({"TENNIS"})
_STAT_KEY      = "TOTAL_GAMES"

# Labels imported lazily to avoid circular import with pipeline.py
def _get_labels():
    from gate_engine.labels import PropLabel
    return PropLabel


def _apply_ceiling(row: dict[str, Any], ceiling: str) -> None:
    """Downgrade terminal_label to ceiling if current label is higher-tier."""
    _TIER = {
        "RESEARCH_INTEREST":    0,
        "MODEL_QUALIFIED_HOLD": 1,
        "MARKET_VERIFIED_HOLD": 2,
        "MONEY_QUALIFIED":      3,
        "FINAL_APPROVED":       4,
    }
    current = row.get("terminal_label")
    if current is None:
        return
    if current and current.startswith("REJECT"):
        return
    ceiling_tier = _TIER.get(ceiling, 99)
    current_tier = _TIER.get(current, 99)
    if current_tier > ceiling_tier:
        row["terminal_label"] = ceiling


def _is_tennis_total_games(row: dict[str, Any]) -> bool:
    sport     = str(row.get("sport") or row.get("league") or "").strip().upper()
    stat_key  = str(row.get("stat_key") or row.get("prop_type") or "").strip().upper()
    return sport in _SPORT_VALUES and stat_key == _STAT_KEY


def run(row: dict[str, Any]) -> None:
    """
    Per-row entry point.  Called for every row before classifier.classify().

    Actions
    ───────
    1. Detect TENNIS / TOTAL_GAMES rows; no-op for everything else.
    2. Import and call tennis_total_games.score(row, enrichment).
    3. Stamp gate report onto row["gates"]["tennis_total_games"].
    4. Set row["can_execute"] = False.
    5. Update row["calibrated_probability"] and row["calibrated_lower_bound"]
       with the model's calibrated output so the classifier and reporting
       consumers see the correct probability.
    6. Apply MODEL_QUALIFIED_HOLD ceiling (PROVISIONAL model).
    7. For Reject classification: stamp terminal_label = REJECT_DATA_QUALITY
       if no other terminal label is already set.
    8. Append model blockers to row["blockers"].
    """
    if not _is_tennis_total_games(row):
        return

    # Unconditional governance flag
    row["can_execute"] = False
    row.setdefault("gates", {})

    # Retrieve enrichment (may be pre-stamped on the row or absent)
    enrichment: dict[str, Any] | None = row.get("enrichment") or row.get("_enrichment")

    # ── call the model ────────────────────────────────────────────────────────
    try:
        from gate_engine import tennis_total_games as _ttg
        result = _ttg.score(row, enrichment)
        # ── fail-closed probability validation ──────────────────────────────
        # If the solver returns out-of-range probabilities, treat as MODEL_ERROR.
        # Tennis rows with out-of-range probabilities must not propagate.
        _cal_sel = result.get("cal_selected")
        _raw_m   = result.get("raw_more")
        _raw_e   = result.get("raw_exact")
        _raw_l   = result.get("raw_less")
        _prob_violation = None
        if _cal_sel is not None:
            if not (0.0 <= float(_cal_sel) <= 1.0):
                _prob_violation = f"cal_selected={_cal_sel} out of [0,1]"
        if _prob_violation is None and _raw_m is not None and _raw_e is not None and _raw_l is not None:
            _triple_sum = float(_raw_m) + float(_raw_e) + float(_raw_l)
            if abs(_triple_sum - 1.0) > 0.01:
                _prob_violation = (
                    f"raw_more({_raw_m})+raw_exact({_raw_e})+raw_less({_raw_l})"
                    f"={_triple_sum:.4f} ≠ 1.0"
                )
        if _prob_violation:
            logger.warning(
                "tennis_total_games_gate: probability out-of-range → MODEL_ERROR: %s",
                _prob_violation,
            )
            result = {
                "can_execute":     False,
                "model_status":    "MODEL_ERROR",
                "classification":  "Reject",
                "blockers":        [f"TENNIS_TG_PROBABILITY_OUT_OF_RANGE:{_prob_violation}"],
                "cal_selected":    None,
                "cal_lower_bound": None,
            }
    except Exception as exc:
        logger.exception("tennis_total_games_gate: model error: %s", exc)
        result = {
            "can_execute":     False,
            "model_status":    "ERROR",
            "classification":  "Reject",
            "blockers":        [f"TENNIS_TG_MODEL_ERROR:{exc}"],
            "cal_selected":    None,
            "cal_lower_bound": None,
        }

    # ── stamp gate report ─────────────────────────────────────────────────────
    row["gates"]["tennis_total_games"] = result

    # ── propagate blockers ────────────────────────────────────────────────────
    model_blockers = result.get("blockers") or []
    existing_blockers = row.setdefault("blockers", [])
    for b in model_blockers:
        if b not in existing_blockers:
            existing_blockers.append(b)

    # ── update probability fields ─────────────────────────────────────────────
    cal_sel = result.get("cal_selected")
    cal_lb  = result.get("cal_lower_bound")
    if cal_sel is not None:
        row["calibrated_probability"]   = cal_sel
        row["calibrated_lower_bound"]   = cal_lb
        row["model_probability"]        = result.get("raw_selected")
        row["model_used"]               = "tennis_total_games_markov_v1"
        row["model_status"]             = result.get("model_status", "PROVISIONAL")
        row["tg_classification"]        = result.get("classification")
        # Stamp the full More/Exact/Less triple for downstream display
        row["raw_more"]                 = result.get("raw_more")
        row["raw_exact"]                = result.get("raw_exact")
        row["raw_less"]                 = result.get("raw_less")
        row["cal_more"]                 = result.get("cal_more")
        row["cal_exact"]                = result.get("cal_exact")
        row["cal_less"]                 = result.get("cal_less")

    # ── ceiling: PROVISIONAL → MODEL_QUALIFIED_HOLD ──────────────────────────
    try:
        PropLabel = _get_labels()
        ceiling_label = PropLabel.MODEL_QUALIFIED_HOLD.value
    except Exception:
        ceiling_label = "MODEL_QUALIFIED_HOLD"

    _apply_ceiling(row, ceiling_label)

    # ── Reject: stamp terminal_label if not already set ───────────────────────
    classification = result.get("classification", "Reject")
    if classification == "Reject" and not row.get("terminal_label"):
        try:
            PropLabel = _get_labels()
            row["terminal_label"] = PropLabel.REJECT_DATA_QUALITY.value
        except Exception:
            row["terminal_label"] = "REJECT_DATA_QUALITY"
        logger.debug(
            "tennis_total_games_gate: Reject classification → terminal_label=REJECT_DATA_QUALITY"
        )

    logger.debug(
        "tennis_total_games_gate: line=%.1f side=%s cal=%.4f lb=%.4f classification=%s",
        result.get("line", 0),
        result.get("side", "?"),
        cal_sel if cal_sel is not None else -1,
        cal_lb  if cal_lb  is not None else -1,
        classification,
    )
