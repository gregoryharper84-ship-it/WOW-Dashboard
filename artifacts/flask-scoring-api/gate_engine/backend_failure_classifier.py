"""
WOW-PATCH-2026-08-07-BACKEND-FAILOVER-RESEARCH
Backend Failure Classifier

Implements the BACKEND_FAILOVER_RESEARCH tier hierarchy recommended by the
reviewer:

  Tier 1  GOVERNANCE_FAIL         — hard stop; governance mismatch is
                                    never recoverable by the caller
  Tier 2  MODEL_RUNTIME_FAIL      — uncaught backend exception; stop until
                                    the runtime error is resolved
  Tier 3  RESPONSE_SIZE_FAIL      — payload too large; retry in slim mode
  Tier 4  MODEL_ROUTE_FAIL        — wrong specialist / contract; reroute
  Tier 5  DATA_CONTRACT_FAIL      — schema / missing-field; web reconstruction
  Tier 6  SOURCE_ACQUISITION_FAIL — provider returned nothing; web reconstruction
  Tier 7  INPUT_FAILURE           — bad request shape; normalize and retry

The classifier attaches a ``failure_classification`` block to every
/gate-engine/run response. When ALL rows fail without completing candidate
evaluation, the terminal_disposition is upgraded from NO_PLAY to
RUN_PARTIAL_BACKEND_FAILURE so the caller knows this was a technical gap,
not a scored rejection.

Factual guards
--------------
* A run cannot be PLAY when failure_type is GOVERNANCE_FAIL.
* candidate_evaluation_completed=False whenever all rows fail.
* probability_publishable=False whenever candidate_evaluation_completed=False.
* can_execute is unconditionally False on every output block.

can_execute: False — unconditional throughout this module.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Tier / policy tables
# ---------------------------------------------------------------------------

#: Severity tier: 1 = most severe, 7 = least severe.
FAILURE_TIER: dict[str, int] = {
    "GOVERNANCE_FAIL":         1,
    "MODEL_RUNTIME_FAIL":      2,
    "RESPONSE_SIZE_FAIL":      3,
    "MODEL_ROUTE_FAIL":        4,
    "DATA_CONTRACT_FAIL":      5,
    "SOURCE_ACQUISITION_FAIL": 6,
    "INPUT_FAILURE":           7,
    "NONE":                    0,  # no failure
}

#: What the caller should do next for each failure type.
RETRY_POLICY: dict[str, str] = {
    "GOVERNANCE_FAIL":         "hard_stop",
    "MODEL_RUNTIME_FAIL":      "slim_mode_retry",
    "RESPONSE_SIZE_FAIL":      "slim_mode_retry",
    "MODEL_ROUTE_FAIL":        "reroute_specialist",
    "DATA_CONTRACT_FAIL":      "web_reconstruction",
    "SOURCE_ACQUISITION_FAIL": "web_reconstruction",
    "INPUT_FAILURE":           "normalize_and_retry",
    "NONE":                    "none",
}

#: Failures that cannot be resolved by the caller without backend changes.
IS_HARD_STOP: dict[str, bool] = {
    "GOVERNANCE_FAIL":         True,
    "MODEL_RUNTIME_FAIL":      True,
    "RESPONSE_SIZE_FAIL":      False,
    "MODEL_ROUTE_FAIL":        False,
    "DATA_CONTRACT_FAIL":      False,
    "SOURCE_ACQUISITION_FAIL": False,
    "INPUT_FAILURE":           False,
    "NONE":                    False,
}

# ---------------------------------------------------------------------------
# Blocker fragment sets used to sub-classify DATA_CONTRACT_FAIL rows
# ---------------------------------------------------------------------------

#: Blocker text fragments that indicate the failure was an acquisition gap,
#: not a schema/contract error.  Web reconstruction is the correct remedy.
_ACQUISITION_BLOCKER_FRAGMENTS: frozenset[str] = frozenset({
    "NO_GAME_LOG_PROVIDED",
    "NO_BOX_SCORE_LOG",
    "ACQUISITION_FAIL",
    "SOURCE_ACQUISITION",
    "NO_SEASON_LOG",
    "STATS_UNAVAILABLE",
    "L10:NO_GAME_LOG",
    "GAME_LOG_EMPTY",
    "BOX_SCORE_EMPTY",
    "NO_GAME_LOG",
    "GAME_LOG_MISSING",
    "EVIDENCE_UNAVAILABLE",
})

#: Blocker text fragments that indicate a routing / specialist mismatch.
_ROUTE_FAIL_BLOCKER_FRAGMENTS: frozenset[str] = frozenset({
    "NO_REGISTERED_MODEL",
    "ROUTE_CONFIGURATION",
    "NO_ROUTE",
    "MONEYLINE_ROUTED_TO_PROP",
    "INVALID_ROUTE",
    "RUN_INVALID_ROUTE",
})


# ---------------------------------------------------------------------------
# Row-level classification
# ---------------------------------------------------------------------------

def classify_row_failure(row: dict) -> str:
    """
    Classify a single scored prop_ledger row into one of the 7 failure types.

    Returns one of the FAILURE_TIER keys, or "NONE" when the row reached a
    non-failure terminal label.
    """
    label      = (row.get("terminal_label") or "").upper()
    blockers   = row.get("blockers") or []
    blocker_str = " ".join(str(b) for b in blockers).upper()

    # Route failures take precedence over data-contract failures
    if any(frag in blocker_str for frag in _ROUTE_FAIL_BLOCKER_FRAGMENTS):
        return "MODEL_ROUTE_FAIL"

    if label == "DATA_CONTRACT_FAIL":
        # Distinguish acquisition gaps from schema/contract errors
        if any(frag in blocker_str for frag in _ACQUISITION_BLOCKER_FRAGMENTS):
            return "SOURCE_ACQUISITION_FAIL"
        return "DATA_CONTRACT_FAIL"

    # Any other label means the row was at least partially evaluated
    return "NONE"


# ---------------------------------------------------------------------------
# Run-level classification
# ---------------------------------------------------------------------------

def classify_run_failure(
    result: dict,
    governance_ok: bool = True,
    strict_runtime_disposition: str = "",
) -> dict:
    """
    Classify a completed /gate-engine/run pipeline result.

    Parameters
    ----------
    result : dict
        The assembled pipeline output (prop_ledger, terminal_labels, …).
    governance_ok : bool
        True when the governance hash matched and required skills verified.
    strict_runtime_disposition : str
        The strict_runtime_disposition already computed by the endpoint.

    Returns
    -------
    dict
        failure_classification block — see _build_classification().
    """
    # Governance is a hard stop — overrides everything else
    if not governance_ok:
        return _build_classification(
            failure_type="GOVERNANCE_FAIL",
            candidate_evaluation_completed=False,
            probability_publishable=False,
            affected_rows=[],
            reconstruction_recommended=False,
        )

    prop_ledger: list[dict] = result.get("prop_ledger") or []

    if not prop_ledger:
        # No rows scored (e.g. all-OUTRIGHT run with an empty prop pipeline)
        return _build_classification(
            failure_type="NONE",
            candidate_evaluation_completed=True,
            probability_publishable=False,
            affected_rows=[],
            reconstruction_recommended=False,
        )

    row_types     = [classify_row_failure(r) for r in prop_ledger]
    failed_rows   = _summarise_failed_rows(prop_ledger, row_types)
    n_failed      = len(failed_rows)
    all_failed    = n_failed == len(prop_ledger)

    if not all_failed:
        # Some rows passed — the run produced a usable result.
        # Still surface which rows failed so the caller can reconstruct them.
        return _build_classification(
            failure_type="NONE",
            candidate_evaluation_completed=True,
            probability_publishable=True,
            affected_rows=failed_rows,
            reconstruction_recommended=bool(failed_rows),
        )

    # All rows failed — pick the dominant (highest-severity) failure type
    unique_types = set(row_types) - {"NONE"}
    dominant = min(
        unique_types,
        key=lambda ft: FAILURE_TIER.get(ft, 99),
        default="DATA_CONTRACT_FAIL",
    )

    reconstruction_recommended = dominant in {
        "DATA_CONTRACT_FAIL",
        "SOURCE_ACQUISITION_FAIL",
        "MODEL_ROUTE_FAIL",
    }

    return _build_classification(
        failure_type=dominant,
        candidate_evaluation_completed=False,
        probability_publishable=False,
        affected_rows=failed_rows,
        reconstruction_recommended=reconstruction_recommended,
    )


def _summarise_failed_rows(
    prop_ledger: list[dict],
    row_types:   list[str],
) -> list[dict]:
    """Build the affected_rows summary for rows that have a non-NONE type."""
    out = []
    for row, ft in zip(prop_ledger, row_types):
        if ft == "NONE":
            continue
        out.append({
            "row_id":       row.get("row_id"),
            "player":       row.get("player"),
            "prop_type":    row.get("prop_type") or row.get("stat_key"),
            "failure_type": ft,
            "retry_policy": RETRY_POLICY.get(ft, "none"),
            "blockers":     (row.get("blockers") or [])[:5],   # cap for response size
        })
    return out


def _build_classification(
    failure_type:                  str,
    candidate_evaluation_completed: bool,
    probability_publishable:       bool,
    affected_rows:                 list[dict],
    reconstruction_recommended:    bool,
) -> dict:
    """Assemble the canonical failure_classification block."""
    tier         = FAILURE_TIER.get(failure_type, 0)
    retry_policy = RETRY_POLICY.get(failure_type, "none")
    hard_stop    = IS_HARD_STOP.get(failure_type, False)

    return {
        "failure_type":                   failure_type,
        "tier":                           tier,
        "retry_policy":                   retry_policy,
        "is_hard_stop":                   hard_stop,
        "candidate_evaluation_completed": candidate_evaluation_completed,
        "probability_publishable":        probability_publishable,
        "reconstruction_recommended":     reconstruction_recommended,
        "affected_rows":                  affected_rows,
        "can_execute":                    False,
    }


# ---------------------------------------------------------------------------
# Partial-failure terminal block
# ---------------------------------------------------------------------------

def build_partial_failure_terminal(failure_classification: dict) -> dict:
    """
    Return the terminal_disposition / strict_runtime_disposition fields to
    inject into the response when all rows failed without completing
    candidate evaluation.

    Separates technical backend gaps from scored rejections so the caller
    never mistakes a DATA_CONTRACT_FAIL sweep for a NO_PLAY decision.
    """
    failure_type = failure_classification.get("failure_type", "DATA_CONTRACT_FAIL")

    if failure_type == "GOVERNANCE_FAIL":
        return {
            "terminal_disposition":            "RUN_INVALID_GOVERNANCE",
            "strict_runtime_disposition":      "RUN_INVALID_GOVERNANCE",
            "candidate_evaluation_completed":  False,
            "probability_publishable":         False,
        }

    return {
        "terminal_disposition":            "RUN_PARTIAL_BACKEND_FAILURE",
        "strict_runtime_disposition":      "RUN_PARTIAL_BACKEND_FAILURE",
        "candidate_evaluation_completed":  False,
        "probability_publishable":         False,
    }


# ---------------------------------------------------------------------------
# Source provenance helpers
# ---------------------------------------------------------------------------

def validate_source_provenance(provenance: Any) -> list[str]:
    """
    Validate a source_provenance list supplied in the enrichment packet.

    Returns a list of violation strings (empty = valid).  Each entry should
    be a dict with at least {field, source, source_type}.
    """
    if provenance is None:
        return []
    if not isinstance(provenance, list):
        return ["source_provenance must be a list"]

    violations: list[str] = []
    for i, entry in enumerate(provenance):
        if not isinstance(entry, dict):
            violations.append(f"source_provenance[{i}]: must be an object")
            continue
        for required_key in ("field", "source", "source_type"):
            if required_key not in entry:
                violations.append(
                    f"source_provenance[{i}]: missing required key '{required_key}'"
                )
    return violations
