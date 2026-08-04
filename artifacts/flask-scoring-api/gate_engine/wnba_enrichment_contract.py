"""
gate_engine/wnba_enrichment_contract.py

Server-side validator for WNBA enrichment field types.

The WNBA pipeline has two separate enrichment channels that must not be mixed:

  game_log      → list[number]
    Consumed by the L5/L10 ledger for plain numeric hit-rate computation.
    One float/int per game, most recent first.
    Example: [28, 32, 25, 30, 27]

  box_score_log → list[dict]
    Consumed by the WNBA opportunity engine for role/minutes/usage analysis.
    Each element is a per-game stat dict with keys MIN, PTS, REB, AST, FGA, USG%.
    Example: [{"MIN": 31, "PTS": 17, "REB": 5, "AST": 3, "FGA": 12, "USG%": 28.4}]

When types are mixed (e.g. box_score_log contains numbers, or game_log contains
dicts) the backend returns WNBA_ENRICHMENT_TYPE_MISMATCH rather than silently
failing or degrading.

Public API
----------
validate(enrichment) → (ok: bool, error_code: str | None, detail: str | None)
validate_or_raise(enrichment) → None  (raises ValueError on type mismatch)
"""
from __future__ import annotations

from typing import Any

ERROR_CODE = "WNBA_ENRICHMENT_TYPE_MISMATCH"

# Resubmission contract for each mismatched field — mirrors _GAP_CONTRACT in app.py
FIELD_CONTRACTS: dict[str, dict] = {
    "game_log": {
        "required_for":      "L5/L10 hit rate ledger",
        "accepted_format":   "list[number] — one value per game, most recent first",
        "resubmission_key":  "enrichment.game_log",
    },
    "box_score_log": {
        "required_for":      "WNBA opportunity gate (role/minutes/usage analysis)",
        "accepted_format":   "list[dict] — each dict has MIN, PTS, REB, AST, FGA, USG%",
        "resubmission_key":  "enrichment.box_score_log",
    },
}


def validate(enrichment: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    """
    Validate WNBA enrichment field types.

    Parameters
    ----------
    enrichment : dict
        The enrichment dict for a single leg (may be the whole request enrichment
        or a per-leg sub-dict).

    Returns
    -------
    (ok, error_code, detail)
        ok=True  → enrichment types are correct; no mismatch detected.
        ok=False → WNBA_ENRICHMENT_TYPE_MISMATCH with a human-readable detail string.

    Never raises.
    """
    if not enrichment or not isinstance(enrichment, dict):
        return (True, None, None)

    game_log      = enrichment.get("game_log")
    box_score_log = enrichment.get("box_score_log")

    errors: list[str] = []

    # ── game_log must be list[number] when present ──────────────────────────
    if game_log is not None:
        if not isinstance(game_log, list):
            errors.append(
                f"game_log must be list[number], got {type(game_log).__name__}"
            )
        elif game_log:
            first = game_log[0]
            if not isinstance(first, (int, float)):
                first_type = type(first).__name__
                errors.append(
                    f"game_log must be list[number] (L5/L10 ledger input); "
                    f"first element is {first_type} — "
                    f"if these are per-game stat dicts, use box_score_log instead"
                )

    # ── box_score_log must be list[dict] when present ───────────────────────
    if box_score_log is not None:
        if not isinstance(box_score_log, list):
            errors.append(
                f"box_score_log must be list[dict], got {type(box_score_log).__name__}"
            )
        elif box_score_log:
            first = box_score_log[0]
            if not isinstance(first, dict):
                first_type = type(first).__name__
                errors.append(
                    f"box_score_log must be list[dict] (WNBA opportunity engine input); "
                    f"first element is {first_type} — "
                    f"if this is a flat numeric series, use game_log instead"
                )

    if errors:
        return (False, ERROR_CODE, "; ".join(errors))
    return (True, None, None)


def validate_or_raise(enrichment: dict[str, Any]) -> None:
    """
    Raise ValueError with WNBA_ENRICHMENT_TYPE_MISMATCH detail if types are wrong.

    Intended for callers that treat type mismatches as hard errors.
    """
    ok, code, detail = validate(enrichment)
    if not ok:
        raise ValueError(f"{code}: {detail}")


def mismatch_response(detail: str) -> dict:
    """
    Build a structured error dict for a WNBA_ENRICHMENT_TYPE_MISMATCH response.

    Includes resubmission guidance for both mismatched fields.
    """
    return {
        "ok":         False,
        "error_code": ERROR_CODE,
        "detail":     detail,
        "remediation": [
            {
                "field":             "game_log",
                "required_for":      FIELD_CONTRACTS["game_log"]["required_for"],
                "accepted_format":   FIELD_CONTRACTS["game_log"]["accepted_format"],
                "resubmission_key":  FIELD_CONTRACTS["game_log"]["resubmission_key"],
            },
            {
                "field":             "box_score_log",
                "required_for":      FIELD_CONTRACTS["box_score_log"]["required_for"],
                "accepted_format":   FIELD_CONTRACTS["box_score_log"]["accepted_format"],
                "resubmission_key":  FIELD_CONTRACTS["box_score_log"]["resubmission_key"],
            },
        ],
    }
