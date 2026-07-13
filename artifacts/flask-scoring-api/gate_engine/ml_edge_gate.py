"""
gate_engine/ml_edge_gate.py
WOW-PATCH-2026-07-13 — P0-3 + P1-4

P0-3: Breakeven and verified edge must be calculated before approval.
P1-4: Short-favorite compression gate (tiered by breakeven probability).

No ML pick may receive LLP_PLAYABLE or LLP_APPROVED unless all of:
    stake, listed_return, multiplier, breakeven_prob, model_prob,
    market_no_vig_prob, verified_edge, edge_floor
are present AND the verified_edge clears the compression floor for the
breakeven_prob bucket.

If any required live-price field is missing → LLP_WATCH (not APPROVED).
If compression floor not cleared → LLP_REJECT_PRICE_COMPRESSION.
"""
from __future__ import annotations

from typing import Any

from .ml_labels import (
    MLReasonCode, COMPRESSION_TABLE, compression_floor, compression_description
)


# ---------------------------------------------------------------------------
# Required fields for PLAYABLE/APPROVED
# ---------------------------------------------------------------------------

REQUIRED_EDGE_FIELDS = [
    "stake",
    "listed_return",
    "model_prob",
    "market_no_vig_prob",
]

# Fields we compute (not required from caller, but must be derivable)
COMPUTED_FIELDS = ["multiplier", "breakeven_prob", "verified_edge"]


# ---------------------------------------------------------------------------
# Core computations
# ---------------------------------------------------------------------------

def compute_breakeven(stake: float, listed_return: float) -> float:
    """
    breakeven_prob = stake / listed_return

    Example: stake=$24.50, listed_return=$35.00
        → breakeven_prob = 24.50/35.00 = 0.70000 (70.0%)
    """
    if listed_return <= 0:
        raise ValueError(f"listed_return must be > 0, got {listed_return}")
    return round(stake / listed_return, 6)


def compute_multiplier(stake: float, listed_return: float) -> float:
    """multiplier = listed_return / stake"""
    if stake <= 0:
        raise ValueError(f"stake must be > 0, got {stake}")
    return round(listed_return / stake, 6)


def compute_verified_edge(model_prob: float, breakeven_prob: float) -> float:
    """verified_edge = model_prob - breakeven_prob"""
    return round(model_prob - breakeven_prob, 6)


# ---------------------------------------------------------------------------
# P0-3 gate: edge fields must be complete for actionable labels
# ---------------------------------------------------------------------------

def validate_ml_edge_requirements(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Validate that all required edge fields are present and that computed
    fields can be derived.  Also computes breakeven_prob, multiplier, and
    verified_edge if not already supplied.

    Returns:
        {
          passed            : bool
          code              : str
          detail            : str
          reason_code       : str | None
          ceiling           : str | None   (LLP label ceiling if failed)
          computed          : dict         (derived fields)
        }
    """
    from gate_engine.llp_governance import LLPLabel

    computed: dict[str, Any] = {}

    # Check required raw fields
    missing = [f for f in REQUIRED_EDGE_FIELDS if candidate.get(f) is None]
    if missing:
        return {
            "passed":      False,
            "code":        "MISSING_EDGE_FIELDS",
            "detail":      f"Missing required ML edge fields: {missing}",
            "reason_code": MLReasonCode.MISSING_EDGE_FIELDS.value,
            "ceiling":     LLPLabel.WATCH.value,
            "computed":    computed,
        }

    # Parse values
    try:
        stake         = float(candidate["stake"])
        listed_return = float(candidate["listed_return"])
        model_prob    = float(candidate["model_prob"])
        no_vig_prob   = float(candidate["market_no_vig_prob"])
    except (TypeError, ValueError) as exc:
        return {
            "passed":      False,
            "code":        "EDGE_FIELD_UNPARSEABLE",
            "detail":      f"Cannot parse edge fields: {exc}",
            "reason_code": MLReasonCode.MISSING_EDGE_FIELDS.value,
            "ceiling":     LLPLabel.WATCH.value,
            "computed":    computed,
        }

    # Compute derived fields
    try:
        multiplier    = compute_multiplier(stake, listed_return)
        breakeven_prob = compute_breakeven(stake, listed_return)
        verified_edge  = compute_verified_edge(model_prob, breakeven_prob)
    except ValueError as exc:
        return {
            "passed":      False,
            "code":        "EDGE_COMPUTE_ERROR",
            "detail":      str(exc),
            "reason_code": MLReasonCode.MISSING_EDGE_FIELDS.value,
            "ceiling":     LLPLabel.WATCH.value,
            "computed":    computed,
        }

    computed.update({
        "multiplier":      multiplier,
        "breakeven_prob":  breakeven_prob,
        "verified_edge":   verified_edge,
        "edge_vs_market":  round(model_prob - no_vig_prob, 6),
    })

    # Check no-vig market probability is present (redundant safety check)
    if no_vig_prob is None:
        return {
            "passed":      False,
            "code":        "MISSING_NO_VIG_PROB",
            "detail":      "market_no_vig_prob required for ML approval",
            "reason_code": MLReasonCode.MISSING_EDGE_FIELDS.value,
            "ceiling":     LLPLabel.WATCH.value,
            "computed":    computed,
        }

    return {
        "passed":      True,
        "code":        "EDGE_FIELDS_COMPLETE",
        "detail":      (
            f"stake={stake}, listed_return={listed_return}, "
            f"multiplier={multiplier:.4f}, breakeven_prob={breakeven_prob:.4f}, "
            f"model_prob={model_prob:.4f}, verified_edge={verified_edge:.4f}"
        ),
        "reason_code": None,
        "ceiling":     None,
        "computed":    computed,
    }


# ---------------------------------------------------------------------------
# P1-4 gate: short-favorite compression
# ---------------------------------------------------------------------------

def validate_price_compression(
    breakeven_prob: float,
    verified_edge: float,
    model_prob: float,
) -> dict[str, Any]:
    """
    Validate that the verified edge clears the compression floor for the
    breakeven_prob bucket.

    Returns:
        {
          passed        : bool
          code          : str
          detail        : str
          reason_code   : str | None
          ceiling       : str | None
          floor_required: float | None
          bucket_desc   : str
        }
    """
    from gate_engine.llp_governance import LLPLabel

    if breakeven_prob < 0.52:
        return {
            "passed":         True,
            "code":           "BELOW_COMPRESSION_RANGE",
            "detail":         f"breakeven_prob={breakeven_prob:.4f} < 0.52; compression gate not applicable",
            "reason_code":    None,
            "ceiling":        None,
            "floor_required": None,
            "bucket_desc":    "below compression range",
        }

    floor = compression_floor(breakeven_prob)
    desc  = compression_description(breakeven_prob)

    if floor is None:
        return {
            "passed":         False,
            "code":           "COMPRESSION_BUCKET_UNRECOGNIZED",
            "detail":         f"breakeven_prob={breakeven_prob:.4f} not in any compression bucket",
            "reason_code":    MLReasonCode.PRICE_COMPRESSION.value,
            "ceiling":        LLPLabel.REJECT.value,
            "floor_required": None,
            "bucket_desc":    desc,
        }

    if verified_edge < floor:
        return {
            "passed":         False,
            "code":           "COMPRESSION_FLOOR_NOT_MET",
            "detail":         (
                f"verified_edge={verified_edge:.4f} < floor={floor:.4f} "
                f"for bucket [{desc}]. "
                f"model_prob={model_prob:.4f}, breakeven_prob={breakeven_prob:.4f}. "
                f"Label: LLP_REJECT_PRICE_COMPRESSION"
            ),
            "reason_code":    MLReasonCode.PRICE_COMPRESSION.value,
            "ceiling":        LLPLabel.REJECT.value,
            "floor_required": floor,
            "bucket_desc":    desc,
        }

    return {
        "passed":         True,
        "code":           "COMPRESSION_FLOOR_MET",
        "detail":         (
            f"verified_edge={verified_edge:.4f} >= floor={floor:.4f} "
            f"({desc})"
        ),
        "reason_code":    None,
        "ceiling":        None,
        "floor_required": floor,
        "bucket_desc":    desc,
    }


# ---------------------------------------------------------------------------
# Combined ML edge evaluation (P0-3 + P1-4 in one call)
# ---------------------------------------------------------------------------

def run_ml_edge_gate(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Run the full P0-3 + P1-4 edge gate on a single ML candidate.

    Returns:
        {
          passed          : bool
          final_label     : str         (LLP label ceiling applied)
          reason_code     : str | None
          edge_result     : dict        (P0-3 result)
          compression_result : dict     (P1-4 result — None if P0-3 failed)
          computed        : dict        (derived fields: multiplier, breakeven_prob etc.)
          blockers        : list[str]
        }
    """
    from gate_engine.llp_governance import LLPLabel, cap_label

    blockers: list[str] = []

    # --- P0-3: edge fields ---
    edge_result = validate_ml_edge_requirements(candidate)
    computed    = edge_result.get("computed", {})

    if not edge_result["passed"]:
        return {
            "passed":             False,
            "final_label":        edge_result["ceiling"],
            "reason_code":        edge_result["reason_code"],
            "edge_result":        edge_result,
            "compression_result": None,
            "computed":           computed,
            "blockers":           [edge_result["code"]],
        }

    # Merge computed values back (so compression gate can use them)
    bp = computed["breakeven_prob"]
    ve = computed["verified_edge"]
    mp = float(candidate["model_prob"])

    # --- P1-4: compression gate ---
    comp_result = validate_price_compression(bp, ve, mp)

    ceiling = None
    if not comp_result["passed"]:
        ceiling = comp_result["ceiling"]
        blockers.append(comp_result["code"])
        reason_code = comp_result["reason_code"]
    else:
        reason_code = None

    # Also apply edge_result ceiling (should be None if passed, but defensive)
    if edge_result.get("ceiling"):
        ceiling = cap_label(ceiling or LLPLabel.APPROVED.value, edge_result["ceiling"])

    passed = not blockers
    final_label = ceiling if ceiling else None  # None means "no cap from this gate"

    return {
        "passed":             passed,
        "final_label":        final_label,
        "reason_code":        reason_code,
        "edge_result":        edge_result,
        "compression_result": comp_result,
        "computed":           computed,
        "blockers":           blockers,
    }
