"""
failure_path.py  —  Module F: Failure Path Matrix
WOW v16 / Section 29.1

Every prop must document three failure scenarios before approval scoring runs.
Abstract "failure paths reviewed" without populating all three = DATA_CONTRACT_FAIL.

Required paths:
  PRIMARY_KILL_PATH   — most likely single failure mode
  SECONDARY_KILL_PATH — second most likely failure mode
  BLACK_SWAN_PATH     — low-probability but catastrophic

Each path requires:
  scenario          str   — what breaks the prop
  probability_band  str   — e.g. "15–25%"
  model_adjustment  str   — e.g. "-3% applied to model_prob"
  evidence          str   — source or data point

FAILURE PATH RULES (Section 29.1):
  Primary kill path floor > 30%     → model_prob must be haircut; cannot approve without
  Two paths each with floor ≥ 20%   → downgrade prop one tier
  Undocumented kill path             → gate fails. Cannot approve.
  Role/minutes kill path (blowout sub, foul trouble, leash) → serious downgrade signal
  Abstract "paths reviewed" without all three scenarios = DATA_CONTRACT_FAIL
"""
from __future__ import annotations

import re
from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Path names
# ---------------------------------------------------------------------------

PATH_NAMES = ("PRIMARY_KILL_PATH", "SECONDARY_KILL_PATH", "BLACK_SWAN_PATH")

REQUIRED_PATH_FIELDS = ("scenario", "probability_band", "model_adjustment", "evidence")

# Role/minutes tags that trigger serious downgrade signal
ROLE_MINUTES_KEYWORDS = {
    "blowout", "sub", "foul trouble", "leash", "minutes", "role",
    "rotation", "benched", "dnp risk", "injury", "late scratch",
}

# Threshold for primary kill path auto-haircut requirement (floor %)
PRIMARY_HIGH_PROBABILITY_FLOOR = 30   # % — if floor > this, haircut required
# Threshold for double-path tier downgrade (each floor ≥ this %)
DOUBLE_PATH_DOWNGRADE_FLOOR = 20


def _parse_probability_band(band_str: str | None) -> tuple[float | None, float | None]:
    """
    Parse a probability band string like "15–25%" or "10-18" into (floor, ceiling).
    Returns (None, None) if unparseable.
    """
    if not band_str:
        return None, None
    nums = re.findall(r"[\d.]+", str(band_str))
    if len(nums) >= 2:
        return float(nums[0]), float(nums[1])
    if len(nums) == 1:
        return float(nums[0]), float(nums[0])
    return None, None


def _has_role_minutes_signal(scenario: str) -> bool:
    """Return True if scenario mentions role/minutes risk keywords."""
    s = scenario.lower()
    return any(kw in s for kw in ROLE_MINUTES_KEYWORDS)


def _is_abstract(path: dict[str, Any]) -> bool:
    """Return True if the path looks like a placeholder / abstract fill."""
    abstract_phrases = {
        "failure paths reviewed",
        "paths reviewed",
        "n/a",
        "none",
        "tbd",
        "todo",
        "placeholder",
        "see above",
    }
    combined = " ".join(str(v).lower() for v in path.values())
    return any(ph in combined for ph in abstract_phrases)


def run(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Validate the failure path matrix.

    Expects enrichment["failure_path_matrix"] to be a dict with:
        {
          PRIMARY_KILL_PATH:   {scenario, probability_band, model_adjustment, evidence},
          SECONDARY_KILL_PATH: {scenario, probability_band, model_adjustment, evidence},
          BLACK_SWAN_PATH:     {scenario, probability_band, model_adjustment, evidence,
                                void_dnp_risk},
        }

    Returns:
        {
          passed:                   bool
          paths_present:            list[str]   — which paths were found
          paths_missing:            list[str]   — which paths are absent
          paths_abstract:           list[str]   — which paths are placeholder-only
          primary_floor:            float | None
          primary_requires_haircut: bool
          double_path_downgrade:    bool
          role_minutes_signal:      bool
          tier_downgrade:           bool    — True if any downgrade rule triggered
          code:                     str
          detail:                   str
        }
    """
    # Defensive: enrichment must be a dict; a string (e.g. "RETRIEVED") from a
    # malformed request would cause AttributeError on .get() below.
    enr = enrichment if isinstance(enrichment, dict) else {}
    matrix_raw = enr.get("failure_path_matrix") or {}
    matrix = matrix_raw if isinstance(matrix_raw, dict) else {}

    paths_present:  list[str] = []
    paths_missing:  list[str] = []
    paths_abstract: list[str] = []
    path_floors:    list[float] = []
    role_signal     = False
    violations:     list[str] = []

    for pname in PATH_NAMES:
        path = matrix.get(pname)
        if not path or not isinstance(path, dict):
            paths_missing.append(pname)
            violations.append(f"missing_path:{pname}")
            continue

        if _is_abstract(path):
            paths_abstract.append(pname)
            violations.append(f"abstract_path:{pname}")
            continue

        # Check required fields
        missing_fields = [f for f in REQUIRED_PATH_FIELDS if not path.get(f)]
        if missing_fields:
            violations.append(f"{pname}:missing_fields:{missing_fields}")
        else:
            paths_present.append(pname)

        # Parse probability band
        floor, _ceil = _parse_probability_band(path.get("probability_band"))
        if floor is not None:
            path_floors.append(floor)

        # Check role/minutes signal
        scenario = str(path.get("scenario") or "")
        if _has_role_minutes_signal(scenario):
            role_signal = True

    # Rule: primary kill path floor > 30% → haircut required
    # This check runs on the raw path regardless of whether all required fields
    # are present — a high-floor path always requires documented adjustment.
    primary_floor: float | None = None
    primary_requires_haircut = False
    primary_path_raw = matrix.get("PRIMARY_KILL_PATH") or {}
    if isinstance(primary_path_raw, dict) and not _is_abstract(primary_path_raw):
        primary_floor, _ = _parse_probability_band(primary_path_raw.get("probability_band"))
        if primary_floor is not None and primary_floor > PRIMARY_HIGH_PROBABILITY_FLOOR:
            primary_requires_haircut = True
            haircut_doc = str(primary_path_raw.get("model_adjustment") or "")
            if not haircut_doc or haircut_doc.strip().lower() in ("n/a", "none", ""):
                violations.append(
                    f"primary_kill_floor={primary_floor:.0f}%>30%_haircut_required"
                    "_but_model_adjustment_missing"
                )

    # Rule: two paths each ≥ 20% floor → downgrade one tier
    high_floors = [f for f in path_floors if f >= DOUBLE_PATH_DOWNGRADE_FLOOR]
    double_path_downgrade = len(high_floors) >= 2

    tier_downgrade = double_path_downgrade or role_signal or primary_requires_haircut

    passed = (
        len(paths_missing) == 0
        and len(paths_abstract) == 0
        and len(violations) == 0
    )

    # Data contract fail if any paths are missing or abstract
    contract_fail = len(paths_missing) > 0 or len(paths_abstract) > 0
    if contract_fail and not row.get("terminal_label"):
        row["terminal_label"] = PropLabel.DATA_CONTRACT_FAIL.value
        row["blockers"].append(
            "DATA_CONTRACT_FAIL:failure_path_matrix:abstract_or_missing:"
            + str(paths_missing + paths_abstract)
        )
    elif not passed and not contract_fail:
        for v in violations:
            row["blockers"].append(f"FAILURE_PATH:{v}")

    code = "FAILURE_PATH_OK" if passed else "FAILURE_PATH_FAIL"
    if contract_fail:
        code = "FAILURE_PATH_DATA_CONTRACT_FAIL"

    result: dict[str, Any] = {
        "passed":                   passed,
        "paths_present":            paths_present,
        "paths_missing":            paths_missing,
        "paths_abstract":           paths_abstract,
        "primary_floor":            primary_floor,
        "primary_requires_haircut": primary_requires_haircut,
        "double_path_downgrade":    double_path_downgrade,
        "role_minutes_signal":      role_signal,
        "tier_downgrade":           tier_downgrade,
        "code":                     code,
        "detail": (
            "All failure paths documented." if passed
            else f"Failure path violations: {'; '.join(violations)}"
        ),
    }

    row.setdefault("gates", {})["failure_path"] = result
    return result
