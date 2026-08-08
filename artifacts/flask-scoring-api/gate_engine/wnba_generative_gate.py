"""
wnba_generative_gate.py  —  WOW v16 Clean Core
Pipeline gate for the WNBA Generative Probability Engine.

Runs in the second per-row loop, BEFORE wnba_composite_gate.
No-ops for all non-WNBA rows and all rows with unsupported stat keys.

Stamps row["gates"]["wnba_generative"] with the full model output.
Updates row["calibrated_probability"] and row["calibrated_probability_lower_bound"].
Applies MODEL_QUALIFIED_HOLD ceiling as PROVISIONAL — wnba_composite_gate
re-applies the milestone-based ceiling and DB logging afterward.

can_execute=False is unconditional.
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel
from .wnba import opportunity_engine as _opp_engine
from .wnba import generative_model as _gen

can_execute = False  # UNCONDITIONAL — never set True

_CEILING = PropLabel.MODEL_QUALIFIED_HOLD.value

# Labels that are above the PROVISIONAL ceiling and must be capped
_ABOVE_CEILING = frozenset({
    PropLabel.FINAL_APPROVED.value,
    PropLabel.MONEY_QUALIFIED.value,
    PropLabel.MARKET_VERIFIED_HOLD.value,
})


def run(row: dict[str, Any], enr: dict[str, Any] | None = None) -> None:
    """
    Gate entry point.  Mutates ``row`` in-place.  Always sets can_execute=False.
    No-ops for non-WNBA rows and rows with unsupported stat keys.

    ``enr`` is the per-row enrichment dict from the pipeline's first loop.
    Falls back to ``row.get("enrichment")`` when not supplied.
    """
    row["can_execute"] = False

    if not _opp_engine.is_wnba_row(row):
        return

    # Quick stat-key check before calling the model (model also validates)
    raw = str(
        row.get("stat_key") or row.get("prop_type") or row.get("prop") or ""
    ).upper().strip().replace(" ", "_")
    canonical = _gen._STAT_KEY_ALIASES.get(raw, raw)
    if canonical not in _gen.SUPPORTED_STAT_KEYS:
        return

    row.setdefault("gates",    {})
    row.setdefault("blockers", [])

    # Resolve enrichment: prefer pipeline-supplied enr, fall back to row field
    effective_enr: dict[str, Any] = {}
    if enr:
        effective_enr.update(enr)
    per_row = row.get("enrichment") or {}
    if isinstance(per_row, dict):
        for k, v in per_row.items():
            if k not in effective_enr:
                effective_enr[k] = v

    try:
        result = _gen.score(row, effective_enr)
    except Exception as exc:
        result = {
            "can_execute":  False,
            "model_status": "GENERATIVE_MODEL_ERROR",
            "blockers": [
                f"WNBA_GENERATIVE_ERROR:{type(exc).__name__}:{str(exc)[:120]}"
            ],
            "final_label": "REJECT",
        }

    # Stamp gate report
    row["gates"]["wnba_generative"] = result

    # Propagate blockers (deduplicate)
    existing = set(row["blockers"])
    for b in result.get("blockers", []):
        if b not in existing:
            row["blockers"].append(b)
            existing.add(b)

    # Update calibrated probability fields used by downstream gates
    cal = result.get("cal_selected")
    if cal is not None:
        row["calibrated_probability"] = cal

    cal_lb = result.get("cal_lower_bound")
    if cal_lb is not None:
        row["calibrated_probability_lower_bound"] = cal_lb

    # Apply PROVISIONAL ceiling — wnba_composite_gate will re-apply its own
    cur = row.get("terminal_label") or ""
    if cur in _ABOVE_CEILING:
        row["terminal_label"] = _CEILING
        row["blockers"].append(
            "WNBA_GENERATIVE:PROVISIONAL_CEILING:MODEL_QUALIFIED_HOLD"
        )

    # Propagate model REJECT label when the lower bound is below all floors
    final_lbl = result.get("final_label", "")
    if final_lbl == "REJECT" and not row.get("terminal_label"):
        row["terminal_label"] = PropLabel.REJECT_DATA_QUALITY.value
        row["blockers"].append(
            f"WNBA_GENERATIVE:REJECT:"
            f"lb={result.get('cal_lower_bound', 0.0):.3f}"
        )
    elif final_lbl in ("HOLD", "WATCH") and not row.get("terminal_label"):
        row["terminal_label"] = _CEILING

    row["can_execute"] = False
