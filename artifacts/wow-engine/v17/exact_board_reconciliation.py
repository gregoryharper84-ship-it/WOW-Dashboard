"""Exact source-row identity reconciliation for V17 prop batch responses.

This is a publication/completion guard only. It never computes or mutates a
sporting probability. Completed model packages are checked against the exact
source row whenever the package exposes identity fields. Every response row also
receives a canonical request_identity echo so downstream consumers cannot
silently join to an older player/stat/line/direction ledger row.

A material identity mismatch blocks the batch from completion while preserving
the scorer receipt for audit. ``can_execute`` remains false.
"""
from __future__ import annotations

import math
from typing import Any

EXACT_BOARD_IDENTITY_MISMATCH = "EXACT_BOARD_IDENTITY_MISMATCH"


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().split())


def _stat(value: Any) -> str:
    raw = "_".join(_text(value).replace("-", " ").split())
    aliases = {
        "K": "PITCHER_STRIKEOUTS",
        "KS": "PITCHER_STRIKEOUTS",
        "SO": "PITCHER_STRIKEOUTS",
        "STRIKEOUT": "PITCHER_STRIKEOUTS",
        "STRIKEOUTS": "PITCHER_STRIKEOUTS",
        "PITCHER_K": "PITCHER_STRIKEOUTS",
        "PITCHER_KS": "PITCHER_STRIKEOUTS",
    }
    return aliases.get(raw, raw)


def _line(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _source_identity(row: Any, index: int) -> tuple[str, dict[str, Any]]:
    getter = row.get if isinstance(row, dict) else lambda key, default=None: getattr(row, key, default)
    row_key = str(getter("row_key") or f"row-{index + 1}")
    return row_key, {
        "event_id": str(getter("event_id") or "").strip(),
        "player": " ".join(str(getter("player") or "").strip().split()),
        "sport": _text(getter("sport")),
        "stat_type": _stat(getter("stat_type")),
        "line": _line(getter("line")),
        "direction": _text(getter("direction")),
    }


def _candidate_packages(outcome: dict[str, Any]) -> list[dict[str, Any]]:
    result = outcome.get("result")
    if not isinstance(result, dict):
        return []
    packages = [result]
    for key in (
        "prediction",
        "probability_package",
        "model_probability_package",
        "governed_probability_package",
        "full_model_probability",
        "research_model_output",
        "candidate_model_output",
        "requested_scope",
    ):
        nested = result.get(key)
        if isinstance(nested, dict):
            packages.append(nested)
    return packages


def _observed_identity(outcome: dict[str, Any]) -> dict[str, Any]:
    observed: dict[str, Any] = {}
    for package in _candidate_packages(outcome):
        for key in ("event_id", "player", "sport", "stat_type", "line", "direction"):
            if key not in observed and package.get(key) is not None:
                observed[key] = package.get(key)
        # Candidate bridge uses side rather than direction and exact_line rather than line.
        if "direction" not in observed and package.get("side") is not None:
            observed["direction"] = package.get("side")
        if "line" not in observed and package.get("exact_line") is not None:
            observed["line"] = package.get("exact_line")
    return observed


def _mismatches(expected: dict[str, Any], observed: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    for key in ("event_id", "player", "sport", "stat_type", "direction"):
        if key not in observed:
            continue
        left = _stat(expected[key]) if key == "stat_type" else _text(expected[key])
        right = _stat(observed[key]) if key == "stat_type" else _text(observed[key])
        if left != right:
            mismatches.append(key)
    if "line" in observed:
        left_line = _line(expected.get("line"))
        right_line = _line(observed.get("line"))
        if left_line is None or right_line is None or abs(left_line - right_line) > 1e-9:
            mismatches.append("line")
    return mismatches


def enforce_exact_board_identity(response: dict[str, Any], source_rows: list[Any]) -> dict[str, Any]:
    """Echo source identity and block completion on any observed mismatch."""
    out = dict(response)
    identities = dict(_source_identity(row, index) for index, row in enumerate(source_rows))
    rows = [dict(row) for row in (out.get("rows") or []) if isinstance(row, dict)]
    mismatch_rows: list[dict[str, Any]] = []
    unverifiable_rows: list[str] = []

    for outcome in rows:
        row_key = str(outcome.get("row_key") or "")
        expected = identities.get(row_key)
        if expected is None:
            continue
        outcome["request_identity"] = {**expected, "can_execute": False}
        if outcome.get("model_evaluated") is not True:
            continue
        observed = _observed_identity(outcome)
        if not observed:
            unverifiable_rows.append(row_key)
            continue
        mismatches = _mismatches(expected, observed)
        if mismatches:
            mismatch_rows.append({
                "row_key": row_key,
                "fields": mismatches,
                "expected": expected,
                "observed": observed,
                "can_execute": False,
            })

    out["rows"] = rows
    audit = {
        "required": bool(identities),
        "rows_in_scope": len(identities),
        "mismatch_count": len(mismatch_rows),
        "mismatches": mismatch_rows,
        "identity_unverifiable_row_ids": sorted(set(unverifiable_rows)),
        "balanced": not mismatch_rows,
        "can_execute": False,
    }
    out["exact_board_identity_reconciliation"] = audit
    if mismatch_rows:
        out["ok"] = False
        out["run_controller_status"] = "BLOCKED"
        out["reconciliation_pass"] = False
        out["completion_blocker"] = EXACT_BOARD_IDENTITY_MISMATCH
        blockers = list(out.get("blockers") or [])
        if EXACT_BOARD_IDENTITY_MISMATCH not in blockers:
            blockers.append(EXACT_BOARD_IDENTITY_MISMATCH)
        out["blockers"] = blockers
    return out


__all__ = ["EXACT_BOARD_IDENTITY_MISMATCH", "enforce_exact_board_identity"]
