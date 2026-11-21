"""Deterministic V17 continuous-learning coordinator (advisory only).

Reuses existing, append-only prop/event observations and Core Intelligence
hypotheses. This module never modifies source predictions, model weights,
calibrators, terminal decisions, or wagering capability.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import re
from typing import Any

from v17.core_intelligence import AUTHORITY
from v17.core_intelligence_runtime import (
    capture_settled_prop_observations,
    intelligence_summary,
)
from v17.core_intelligence_event_runtime import capture_settled_event_observations
from v17.core_intelligence_compounding_runtime import run_compounding_memory_cycle

CAN_EXECUTE = False
AUTOMATIC_PROMOTION_ALLOWED = False
STAGE_ORDER = ("PROP_CAPTURE", "EVENT_CAPTURE", "COHORT_SUMMARY", "COMPOUNDING_MEMORY")


def _validate_bound(name: str, value: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"INVALID_{name}")


def _stage(name: str, action: Callable[[], Mapping[str, Any]], *, require_pass: bool = True) -> dict[str, Any]:
    """Preserve partial results but never mistake an unsafe stage for success."""
    try:
        raw = action()
        if not isinstance(raw, Mapping):
            raise ValueError("STAGE_PAYLOAD_INVALID")
        if raw.get("can_execute") is not False:
            raise ValueError("STAGE_SAFETY_INVARIANT_FAILED")
        if raw.get("authority") != AUTHORITY:
            raise ValueError("STAGE_AUTHORITY_INVARIANT_FAILED")
        if (require_pass and raw.get("status") != "PASS") or (
            not require_pass and raw.get("status") not in (None, "PASS")
        ):
            # Cohort summaries are legacy status-less payloads. Preserve that
            # documented case, but never convert an explicit FAILED or HOLD
            # response into a successful four-stage receipt.
            raise ValueError("STAGE_STATUS_NOT_PASS")
    except Exception as exc:
        # Intentionally avoid surfacing exception messages, which may contain
        # connection strings, sensitive source fields or credentials.
        failure = {
            "stage": name,
            "status": "FAILED",
            "error_type": type(exc).__name__,
            "authority": AUTHORITY,
            "can_execute": False,
        }
        # Preserve a vetted static database boundary, not exception text or
        # arbitrary path/URL. Operators can isolate the failed table without
        # leaking credentials from nested provider exceptions.
        boundary = getattr(exc, "boundary", None)
        if isinstance(boundary, str) and re.fullmatch(r"wow_[a-z0-9_]+\.[a-z0-9_]+", boundary):
            failure["error_boundary"] = boundary
        return failure
    return {"stage": name, "status": "PASS", "data": dict(raw), "authority": AUTHORITY, "can_execute": False}


def _summary(stage: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    if stage["status"] != "PASS":
        return {key: value for key, value in stage.items() if key != "data"}
    return {
        "stage": stage["stage"],
        "status": "PASS",
        **{name: stage["data"].get(name) for name in fields},
        "authority": AUTHORITY,
        "can_execute": False,
    }


def _skip(name: str, upstream: str) -> dict[str, Any]:
    return {
        "stage": name,
        "status": "SKIPPED_UPSTREAM_FAILURE",
        "blocked_by": upstream,
        "authority": AUTHORITY,
        "can_execute": False,
    }


def run_continuous_intelligence_cycle(
    db: Any,
    *,
    max_outcomes: int = 1000,
    min_samples: int = 30,
    max_observations: int = 5000,
) -> dict[str, Any]:
    """Capture both sports lanes, then evaluate and persist advisory learning.

    Each capture is independent. A failure in one does not erase the other, but
    downstream summaries/proposals must not claim a complete refresh. Existing
    source adapters enforce immutable prediction/outcome joins and append-only
    idempotent writes.
    """
    _validate_bound("MAX_OUTCOMES", max_outcomes, 5000)
    _validate_bound("MIN_SAMPLES", min_samples, 1000)
    _validate_bound("MAX_OBSERVATIONS", max_observations, 20000)

    prop = _stage(
        "PROP_CAPTURE",
        lambda: capture_settled_prop_observations(db, max_outcomes=max_outcomes),
    )
    event = _stage(
        "EVENT_CAPTURE",
        lambda: capture_settled_event_observations(db, max_outcomes=max_outcomes),
    )
    if prop["status"] != "PASS" or event["status"] != "PASS":
        blocking = prop["stage"] if prop["status"] != "PASS" else event["stage"]
        summary = _skip("COHORT_SUMMARY", blocking)
        memory = _skip("COMPOUNDING_MEMORY", blocking)
    else:
        summary = _stage(
            "COHORT_SUMMARY",
            lambda: intelligence_summary(
                db,
                min_samples=min_samples,
                max_observations=max_observations,
                persist_hypotheses=True,
            ),
            require_pass=False,
        )
        if summary["status"] == "PASS":
            memory = _stage(
                "COMPOUNDING_MEMORY",
                lambda: run_compounding_memory_cycle(
                    db,
                    min_samples=min_samples,
                    max_observations=max_observations,
                    base_summary=summary["data"],
                ),
            )
        else:
            memory = _skip("COMPOUNDING_MEMORY", "COHORT_SUMMARY")

    stages = (
        _summary(prop, ("candidate_n", "observation_n", "skipped_n")),
        _summary(event, ("candidate_n", "observation_n", "skipped_n")),
        _summary(summary, ("observation_n", "cohort_n", "hypothesis_n")),
        _summary(
            memory,
            ("observation_n", "market_scorecard_n", "signal_scorecard_n",
             "specialist_scorecard_n", "challenger_proposal_n"),
        ),
    )
    assert tuple(stage["stage"] for stage in stages) == STAGE_ORDER
    complete = all(stage["status"] == "PASS" for stage in stages)
    return {
        "schema_version": "WOW17_CONTINUOUS_INTELLIGENCE_V1",
        "status": "PASS" if complete else "PARTIAL_FAILURE",
        "stages": list(stages),
        "completed_stages": sum(stage["status"] == "PASS" for stage in stages),
        "total_stages": len(stages),
        "research_only": True,
        "automatic_promotion_allowed": False,
        "model_parameters_changed": False,
        "authority": AUTHORITY,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "AUTOMATIC_PROMOTION_ALLOWED", "STAGE_ORDER", "run_continuous_intelligence_cycle"]
