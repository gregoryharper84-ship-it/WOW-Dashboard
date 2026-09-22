"""V17 team/event evidence-handoff rank-eligibility runtime repair.

This patch fixes a composition gap in the existing preservation layer: the
canonical evidence hydrator supports WINNER, BEST_SIDE, FAVORITE, UNDERDOG, and
UPSET, but the Python wrapper only invoked it for WINNER/BEST_SIDE. As a result,
market-relative ML rows could retain a completed fitted model package while the
downstream probability audit / event governor still saw stale or unhydrated
canonical evidence.

The repair expands the handoff to every supported team/event decision intent and
adds a fail-closed diagnostic for contradictions where canonical upstream fields
are present but downstream governance still reports those exact fields missing.
It never upgrades rank eligibility, publication, or execution authority by
itself. Genuine evidence, freshness, calibration, identity, scorer, and terminal
failures remain blocking. can_execute is always false.
"""
from __future__ import annotations

from math import isfinite
from typing import Any

CAN_EXECUTE = False
RUN_INVALID_EVIDENCE_BINDING = "RUN_INVALID_EVIDENCE_BINDING"
_SUPPORTED_HANDOFF_INTENTS = frozenset({
    "WINNER",
    "BEST_SIDE",
    "FAVORITE",
    "UNDERDOG",
    "UPSET",
})

_MISSING_REASON_FIELDS: dict[str, tuple[str, ...]] = {
    "INDEPENDENT_PROBABILITY_MISSING": (
        "independent_home_probability",
        "independent_away_probability",
    ),
    "FAVORITE_FAILURE_PATHS_MISSING": ("favorite_failure_paths_json",),
    "FAVORITE_FAILURE_PATH_PROBABILITY_MISSING": (
        "favorite_failure_path_probability",
    ),
    "LARGEST_FAVORITE_LOSS_PATH_MISSING": ("largest_favorite_loss_path",),
    "UNDERDOG_UPSET_PATH_MISSING": ("underdog_upset_path_json",),
}


def _present(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        try:
            return isfinite(float(value))
        except (TypeError, ValueError):
            return False
    return True


def _collect_reason_codes(value: Any) -> set[str]:
    """Collect downstream reason/blocker strings without inventing semantics."""
    out: set[str] = set()
    if isinstance(value, str):
        if value:
            out.add(value)
        return out
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {
                "blockers",
                "reasons",
                "reason_codes",
                "rank_eligibility_reasons",
                "current_publication_blockers",
            }:
                out.update(_collect_reason_codes(item))
            elif isinstance(item, (dict, list, tuple, set)):
                out.update(_collect_reason_codes(item))
        return out
    if isinstance(value, (list, tuple, set)):
        for item in value:
            out.update(_collect_reason_codes(item))
    return out


def _lineup_confirmed(req: Any, model_result: dict[str, Any], side: str) -> bool:
    evidence = dict(getattr(req, "sport_specific_evidence", None) or {})
    direct = str(evidence.get(f"{side}_lineup_status") or "").upper()
    if direct == "CONFIRMED":
        return True
    lineup = model_result.get("lineup_context")
    return isinstance(lineup, dict) and str(lineup.get("status") or "").upper() == "CONFIRMED"


def _schema_mismatches(
    req: Any,
    model_result: dict[str, Any],
    downstream: Any,
) -> list[str]:
    """Detect only high-confidence present-upstream/missing-downstream contradictions.

    Calibration mismatch is deliberately excluded: a populated training-N field is
    not proof that it matches the canonical registry row. A real calibration
    provenance mismatch must remain a blocker.
    """
    reasons = _collect_reason_codes(downstream)
    mismatches: list[str] = []

    for reason, fields in _MISSING_REASON_FIELDS.items():
        if reason in reasons and all(_present(model_result.get(field)) for field in fields):
            mismatches.append(reason)

    if "HOME_LINEUP_NOT_CALLED" in reasons and _lineup_confirmed(req, model_result, "home"):
        mismatches.append("HOME_LINEUP_NOT_CALLED")
    if "AWAY_LINEUP_NOT_CALLED" in reasons and _lineup_confirmed(req, model_result, "away"):
        mismatches.append("AWAY_LINEUP_NOT_CALLED")

    return sorted(set(mismatches))


def _annotate_schema_mismatch(
    req: Any,
    model_result: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    mismatches = _schema_mismatches(req, model_result, result)
    if not mismatches:
        return result

    out = dict(result)
    typed = [f"V17_HANDOFF_SCHEMA_MISMATCH:{reason}" for reason in mismatches]
    out["blockers"] = sorted(set([*(out.get("blockers") or []), RUN_INVALID_EVIDENCE_BINDING, *typed]))
    out["run_validity_status"] = RUN_INVALID_EVIDENCE_BINDING
    out["evidence_handoff_schema_mismatch"] = {
        "status": "FAIL",
        "code": "V17_HANDOFF_SCHEMA_MISMATCH",
        "run_invalid_code": RUN_INVALID_EVIDENCE_BINDING,
        "contradictions": mismatches,
        "rank_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    # Diagnostic only. Never use a contradiction detector to bypass the normal
    # probability-audit, governor, final-refresh, or terminal-reducer chain.
    # The already-computed sporting package may remain visible for diagnosis,
    # but the run cannot masquerade as an ordinary no-pick while producer and
    # consumer schemas contradict one another.
    out["rank_eligible"] = False
    out["probability_publishable"] = False
    out["can_execute"] = False
    return out


def install_evidence_handoff_rank_fix(*, preservation: Any) -> bool:
    """Install the Sep-16 handoff fix onto the active preservation module."""
    if getattr(preservation, "_v17_sep16_evidence_handoff_rank_fix_installed", False):
        return True

    intents = getattr(preservation, "_PROBABILITY_ONLY_INTENTS", None)
    if not isinstance(intents, set):
        return False
    original = getattr(preservation, "_run_mlb_llp_governance_with_evidence_handoff", None)
    if not callable(original):
        return False

    # Existing SQL hydration explicitly accepts all five of these intents. The
    # Python composition layer must not suppress the handoff for three of them.
    intents.update(_SUPPORTED_HANDOFF_INTENTS)

    def repaired_handoff(
        req: Any,
        route: Any,
        model_result: dict[str, Any],
        envelope: Any | None = None,
        *,
        event_api: Any,
    ) -> dict[str, Any]:
        result = original(
            req,
            route,
            model_result,
            envelope=envelope,
            event_api=event_api,
        )
        if not isinstance(result, dict):
            return result
        return _annotate_schema_mismatch(req, model_result, result)

    preservation._run_mlb_llp_governance_with_evidence_handoff = repaired_handoff
    preservation._v17_sep16_evidence_handoff_original = original
    preservation._v17_sep16_evidence_handoff_rank_fix_installed = True
    return True


__all__ = [
    "RUN_INVALID_EVIDENCE_BINDING",
    "install_evidence_handoff_rank_fix",
]
