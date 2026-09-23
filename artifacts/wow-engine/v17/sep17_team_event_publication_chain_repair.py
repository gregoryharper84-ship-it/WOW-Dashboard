"""V17 Sep-17 MLB team/event publication-chain repair.

Closes a circular publication dependency without weakening row-level governance.
The fitted MLB specialist owns ``model_probability_publishable``: it means the
numeric sporting-probability package is valid enough to enter the governed audit
chain.  The global/row ``probability_publishable`` field is a *downstream* result
of probability audit, event governance, final refresh, rank eligibility and the
terminal reducer.  Requiring the latter before those stages run creates a
circular deadlock.

For probability-only WINNER/BEST_SIDE requests only, this repair forwards a
validated, fresh package to the existing governance function with its model-side
publication prerequisite satisfied when ``model_probability_publishable`` is
true.  It never writes rank_eligible itself, never changes market-relative
FAVORITE/UNDERDOG/UPSET behavior, and never grants execution authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

CAN_EXECUTE = False
_PROBABILITY_ONLY_INTENTS = frozenset({"WINNER", "BEST_SIDE"})


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _model_contract_ready(req: Any, model_result: dict[str, Any], team_runtime: Any) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    intent = str(getattr(req, "decision_intent", "BEST_SIDE") or "BEST_SIDE").upper()
    if intent not in _PROBABILITY_ONLY_INTENTS:
        blockers.append("MARKET_RELATIVE_INTENT_NOT_ELIGIBLE")
        return False, blockers

    if model_result.get("probability_fields_withheld") is True:
        blockers.append("PROBABILITY_FIELDS_WITHHELD")
    if model_result.get("model_probability_publishable") is not True:
        blockers.append("MODEL_PROBABILITY_NOT_PUBLISHABLE")
    if model_result.get("probability_package_valid") is not True:
        blockers.append("PROBABILITY_PACKAGE_NOT_VALID")
    if model_result.get("dynamic_calibration_complete") is not True:
        blockers.append("DYNAMIC_CALIBRATION_NOT_COMPLETE")
    if str(model_result.get("calibration_health_status") or "").upper() != "PASS":
        blockers.append("CALIBRATION_HEALTH_NOT_PASS")
    if not (model_result.get("score_snapshot_id") or model_result.get("base_score_snapshot_id")):
        blockers.append("SCORE_SNAPSHOT_ID_MISSING")
    if model_result.get("can_execute") is not False:
        blockers.append("EXECUTION_INVARIANT_NOT_PROVEN")

    validate = getattr(team_runtime, "_validate_model_output_lossless", None)
    if not callable(validate):
        blockers.append("MODEL_OUTPUT_VALIDATOR_UNAVAILABLE")
    else:
        try:
            valid, errors = validate(model_result)
        except Exception as exc:  # fail closed; never convert a validator failure into publication
            valid, errors = False, [f"MODEL_OUTPUT_VALIDATOR_FAILED:{type(exc).__name__}"]
        if not valid:
            blockers.extend(str(error) for error in (errors or ["MODEL_OUTPUT_INVALID"]))

    model_ts = _parse_timestamp(model_result.get("model_timestamp"))
    latest_ts = _parse_timestamp(model_result.get("latest_material_update_timestamp"))
    if model_ts is None:
        blockers.append("MODEL_TIMESTAMP_MISSING_OR_INVALID")
    if latest_ts is None:
        blockers.append("LATEST_MATERIAL_UPDATE_TIMESTAMP_MISSING_OR_INVALID")
    if model_ts is not None and latest_ts is not None and model_ts < latest_ts:
        blockers.append("MODEL_RERUN_REQUIRED")

    return not blockers, sorted(set(blockers))


def _authoritative_value(nested: dict[str, Any], output: dict[str, Any], nested_key: str, output_key: str) -> Any:
    """Prefer a concrete nested governance value over duplicated stale telemetry."""
    value = nested.get(nested_key)
    if value not in (None, "", "NOT_PROVEN", "NOT_CALLED", "UNKNOWN"):
        return value
    return output.get(output_key)


def _reconcile_governance_stage_state(output: dict[str, Any]) -> dict[str, Any]:
    """Mirror authoritative nested governance into duplicated top-level stage state.

    This is serialization reconciliation only. It does not change the nested
    governance result, terminal label, probability publication, or rank
    eligibility. A completed stage may be true while the row is still held by a
    later stage such as final refresh.
    """
    governance = output.get("llp_governance")
    if not isinstance(governance, dict):
        return output

    out = dict(output)
    audit = _authoritative_value(
        governance, out, "probability_audit_result", "llp_probability_audit_result"
    )
    decision = _authoritative_value(governance, out, "event_decision", "llp_event_decision")
    mutex = _authoritative_value(governance, out, "event_mutex_status", "event_mutex_status")

    if audit not in (None, ""):
        out["llp_probability_audit_result"] = audit
        out["probability_audit_passed"] = str(audit).upper() in {"PASS", "PASS_PROBABILITY_AUDIT"}
    if decision not in (None, ""):
        out["llp_event_decision"] = decision
    if mutex not in (None, ""):
        out["event_mutex_status"] = mutex

    decision_proven = str(decision or "").upper() not in {
        "", "NOT_PROVEN", "NOT_CALLED", "UNKNOWN"
    }
    out["event_governor_complete"] = bool(
        str(mutex or "").upper() == "PASS" and decision_proven
    )
    out["can_execute"] = False
    return out


def install_team_event_publication_chain_repair(*, preservation: Any, team_runtime: Any) -> bool:
    """Install the probability-only model/publication boundary repair.

    The wrapper changes only the input prerequisite consumed by the *existing*
    governance function.  That function still owns probability audit, event
    mutex, postmodel/final gates, terminal label and rank eligibility.
    """
    if getattr(preservation, "_v17_sep17_publication_chain_repair_installed", False):
        return True

    original = getattr(preservation, "_original_run_mlb_llp_governance", None)
    if not callable(original):
        return False

    def repaired_governance(
        req: Any,
        route: Any,
        model_result: dict[str, Any],
        envelope: Any | None = None,
        *,
        event_api: Any,
    ) -> dict[str, Any]:
        ready, blockers = _model_contract_ready(req, model_result, team_runtime)
        original_probability_publishable = model_result.get("probability_publishable") is True
        forwarded = model_result
        applied = False

        if ready and not original_probability_publishable:
            forwarded = dict(model_result)
            # This is a scoped adapter for the legacy prerequisite inside the
            # existing governance function. It is NOT a terminal publication
            # decision; downstream governance still must set rank/publication.
            forwarded["probability_publishable"] = True
            forwarded["probability_only_model_contract_ready"] = True
            forwarded["can_execute"] = False
            applied = True

        result = original(
            req,
            route,
            forwarded,
            envelope=envelope,
            event_api=event_api,
        )
        if not isinstance(result, dict):
            return result

        out = _reconcile_governance_stage_state(dict(result))
        out["probability_only_model_publication_repair"] = {
            "status": "APPLIED" if applied else "NOT_APPLIED",
            "intent": str(getattr(req, "decision_intent", "") or "").upper(),
            "model_probability_publishable": model_result.get("model_probability_publishable") is True,
            "original_probability_publishable": original_probability_publishable,
            "model_contract_ready": ready,
            "blockers": blockers,
            "rank_eligibility_mutated_by_repair": False,
            "terminal_authority_mutated_by_repair": False,
            "can_execute": False,
        }
        out["can_execute"] = False
        return out

    preservation._v17_sep17_publication_chain_original = original
    preservation._original_run_mlb_llp_governance = repaired_governance
    preservation._v17_sep17_publication_chain_repair_installed = True
    return True


__all__ = ["install_team_event_publication_chain_repair"]
