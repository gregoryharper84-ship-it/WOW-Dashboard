"""V17 continuation semantics for controlling-model failures.

This module deliberately subclasses the existing Scout/Research coordinator and
changes only the non-successful controlling-model terminalization boundary.
V17 reserves MODEL_UNAVAILABLE for a genuinely unavailable exact controlling
capability/artifact.  Once the selected model path fails for another reason,
the typed failure must survive into the immutable terminal decision rather than
being rewritten as MODEL_UNAVAILABLE.

No probability, calibration, qualification, market, portfolio, refresh, or
execution semantics are changed here.  can_execute remains false.
"""
from __future__ import annotations

from typing import Any

from agent_runtime import repository
from agent_runtime.coordinator_scout_research import Coordinator as ScoutResearchCoordinator


_SCORER_FAILURE_BLOCKERS = frozenset({
    "MODEL_SCORER_FAILED",
    "WORKER_TIMED_OUT",
    "WORKER_DEAD_LETTERED",
    "EVENT_MODEL_BRIDGE_UNAVAILABLE",
    "EVENT_MODEL_BRIDGE_FAILED",
    "CONTROLLING_MODEL_PROVIDER_FAILED",
})
_OUTPUT_FAILURE_BLOCKERS = frozenset({
    "MODEL_OUTPUT_INVALID",
    "EVENT_MODEL_BRIDGE_INVALID_RESPONSE",
    "PROP_PMF_EMPTY",
    "PROP_PMF_SUPPORT_INVALID",
    "PROP_PMF_PROBABILITY_INVALID",
    "PROP_PMF_NOT_NORMALIZED",
})


def classify_model_terminal(job_status: str, blockers: list[str] | tuple[str, ...] | None) -> tuple[str, str]:
    """Return (terminal_label, terminal_ceiling) for a failed model job.

    MODEL_UNAVAILABLE is the only model-failure label that also receives the
    MODEL_UNAVAILABLE sentinel ceiling.  All other typed failures remain held
    at RESEARCH_INTEREST so reconciliation cannot misclassify an invoked-model
    failure as missing capability.
    """
    normalized = [str(code).strip() for code in (blockers or []) if str(code).strip()]
    blocker_set = set(normalized)
    status = str(job_status or "").strip().upper()

    if "MODEL_UNAVAILABLE" in blocker_set:
        return "MODEL_UNAVAILABLE", "MODEL_UNAVAILABLE"
    if "MODEL_INPUTS_INSUFFICIENT" in blocker_set:
        return "MODEL_INPUTS_INSUFFICIENT", "RESEARCH_INTEREST"
    if blocker_set.intersection(_OUTPUT_FAILURE_BLOCKERS):
        return "MODEL_OUTPUT_INVALID", "RESEARCH_INTEREST"
    if status in {"TIMED_OUT", "DEAD_LETTERED"} or blocker_set.intersection(_SCORER_FAILURE_BLOCKERS):
        return "MODEL_SCORER_FAILED", "RESEARCH_INTEREST"
    if normalized:
        # Preserve an already-typed backend/model blocker rather than replacing
        # it with a broader capability label.  The ceiling remains a hold.
        return normalized[0], "RESEARCH_INTEREST"
    return "MODEL_SCORER_FAILED", "RESEARCH_INTEREST"


class Coordinator(ScoutResearchCoordinator):
    """Scout/Research coordinator with V17 typed model-failure preservation."""

    def _after_model(self, env: Any, output: dict[str, Any]) -> None:
        if not self._all_worker_terminal(env.run_id, "wow.controlling-model"):
            return
        as_of = self._run_as_of(env.run_id)
        failure_work = []
        for job in repository.list_jobs(self.client, env.run_id, worker_id="wow.controlling-model"):
            cid = str(job["candidate_id"])
            candidate = repository.get_candidate(self.client, cid) or {}
            if candidate.get("terminal_label") is not None:
                continue
            body = self._output_for_job(job)
            if job["status"] != "SUCCEEDED":
                blockers = list(job.get("blockers") or [])
                label, ceiling = classify_model_terminal(str(job.get("status") or ""), blockers)
                effective_blockers = blockers or [label]
                self._terminal(cid, label, effective_blockers, ceiling)
                continue
            if body.get("probability_publishable") is not True and "PROBABILITY_PUBLICATION_HELD" not in (job.get("blockers") or []):
                self._terminal(
                    cid,
                    "MODEL_QUALIFIED_HOLD",
                    list(job.get("blockers") or ["PROBABILITY_PUBLICATION_HELD"]),
                    "MODEL_QUALIFIED_HOLD",
                )
                continue
            payload = self._candidate_payload(cid)
            failure_work.append((cid, payload.get("failure_components"), candidate))
        if not failure_work:
            self._finish_if_terminal(env.run_id)
            return
        for cid, components, candidate in failure_work:
            self._queue(
                run_id=env.run_id,
                candidate_id=cid,
                worker_id="wow.failure-path-framework",
                evidence_snapshot_id=None,
                as_of=as_of,
                payload={"components": components},
                required=True,
            )


__all__ = ["Coordinator", "classify_model_terminal"]
