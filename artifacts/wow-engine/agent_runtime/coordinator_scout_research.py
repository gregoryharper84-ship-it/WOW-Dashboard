"""Mandatory Scout -> Research barrier for every governed WOW v16 candidate.

This coordinator subclasses the proven Agent Runtime coordinator and replaces
only the pre-model continuation. Once existing evidence-hydration succeeds, the
legacy controlling-model/failure/calibration/market/refresh/reducer chain is
used unchanged.
"""
from __future__ import annotations

from typing import Any

from agent_runtime import repository
from agent_runtime.coordinator import Coordinator as BaseCoordinator
from agent_runtime.scout_research import RESEARCH_RECONCILER, RESEARCH_WORKERS, scout_lane

_TERMINAL = {"SUCCEEDED", "BLOCKED", "REJECTED", "TIMED_OUT", "DEAD_LETTERED", "CANCELED"}
_SPECIALIZED_SCOUTS = ("wow.prop-scout-router", "wow.ml-event-scout-router")


class Coordinator(BaseCoordinator):
    """Production coordinator with machine-enforced Scout + Research stages."""

    def on_job_started(self, worker_id: str, run_id: str) -> None:
        if worker_id in RESEARCH_WORKERS:
            try:
                repository.transition_run(
                    self.client, run_id,
                    expected_status="RESEARCH_QUEUED", next_status="RESEARCH_RUNNING", stage="RESEARCH_RUNNING",
                )
            except Exception:
                pass
            return
        super().on_job_started(worker_id, run_id)

    def on_job_terminal(self, env, output: dict[str, Any]) -> None:
        worker = env.worker_id
        if worker == "wow.parallel-discovery-router":
            self._after_discovery(env, output)
            return
        if worker == "wow.global-scout-coordinator":
            self._after_global_scout(env, output)
            return
        if worker in _SPECIALIZED_SCOUTS:
            self._after_specialized_scout(env, output)
            return
        if worker == "wow.slate-integrity-expert":
            self._after_identity(env, output)
            return
        if worker in RESEARCH_WORKERS:
            self._after_researcher(env, output)
            return
        if worker == RESEARCH_RECONCILER:
            self._after_reconciler(env, output)
            return
        super().on_job_terminal(env, output)

    def _after_discovery(self, env, output: dict[str, Any]) -> None:
        if output.get("status") != "SUCCEEDED":
            repository.transition_run(
                self.client, env.run_id,
                expected_status="DISCOVERY_RUNNING", next_status="FAILED", stage="DISCOVERY_FAILED",
            )
            return
        candidates = list((output.get("output") or {}).get("candidates") or [])
        rows = repository.upsert_candidates(self.client, env.run_id, candidates)
        repository.transition_run(
            self.client, env.run_id,
            expected_status="DISCOVERY_RUNNING", next_status="ROUTING", stage="SCOUT_ROUTING",
        )
        if not rows:
            self._finish_if_terminal(env.run_id)
            return
        raw_rows = env.payload.get("rows")
        scout_mode = "FOCUSED" if isinstance(raw_rows, list) and raw_rows else (
            "FULL_SLATE" if env.payload.get("discovery_enabled") is True else "FOCUSED"
        )
        as_of = self._run_as_of(env.run_id)
        for row in rows:
            payload = dict(row.get("candidate_payload") or {})
            self._queue(
                run_id=env.run_id, candidate_id=str(row["candidate_id"]), worker_id="wow.global-scout-coordinator",
                evidence_snapshot_id=None, as_of=as_of,
                payload={"candidate": payload, "scout_mode": scout_mode}, required=True,
            )

    def _after_global_scout(self, env, output: dict[str, Any]) -> None:
        if not self._all_worker_terminal(env.run_id, "wow.global-scout-coordinator"):
            return
        as_of = self._run_as_of(env.run_id)
        jobs = {
            str(job["candidate_id"]): job
            for job in repository.list_jobs(self.client, env.run_id, worker_id="wow.global-scout-coordinator")
        }
        for candidate in repository.list_run_candidates(self.client, env.run_id):
            if candidate.get("terminal_label") is not None:
                continue
            cid = str(candidate["candidate_id"])
            job = jobs.get(cid)
            if not job or job.get("status") != "SUCCEEDED":
                self._terminal(cid, "RESEARCH_INTEREST", list((job or {}).get("blockers") or ["SCOUT_GATE_BLOCKED"]))
                continue
            payload = dict(candidate.get("candidate_payload") or {})
            worker_id = "wow.prop-scout-router" if scout_lane(payload) == "PROP" else "wow.ml-event-scout-router"
            self._queue(
                run_id=env.run_id, candidate_id=cid, worker_id=worker_id,
                evidence_snapshot_id=None, as_of=as_of,
                payload={"candidate": payload}, required=True,
            )
        self._maybe_queue_identity(env.run_id)

    def _after_specialized_scout(self, env, output: dict[str, Any]) -> None:
        self._maybe_queue_identity(env.run_id)

    def _maybe_queue_identity(self, run_id: str) -> None:
        candidates = [c for c in repository.list_run_candidates(self.client, run_id) if c.get("terminal_label") is None]
        if not candidates:
            self._finish_if_terminal(run_id)
            return
        all_jobs = []
        for worker_id in _SPECIALIZED_SCOUTS:
            all_jobs.extend(repository.list_jobs(self.client, run_id, worker_id=worker_id))
        by_candidate = {str(job["candidate_id"]): job for job in all_jobs}
        if not all(str(c["candidate_id"]) in by_candidate and by_candidate[str(c["candidate_id"])]["status"] in _TERMINAL for c in candidates):
            return
        as_of = self._run_as_of(run_id)
        for candidate in candidates:
            cid = str(candidate["candidate_id"])
            job = by_candidate[cid]
            if job.get("status") != "SUCCEEDED":
                self._terminal(cid, "RESEARCH_INTEREST", list(job.get("blockers") or ["SCOUT_GATE_BLOCKED"]))
                continue
            payload = dict(candidate.get("candidate_payload") or {})
            self._queue(
                run_id=run_id, candidate_id=cid, worker_id="wow.slate-integrity-expert",
                evidence_snapshot_id=None, as_of=as_of, payload={"candidate": payload}, required=True,
            )

    def _after_identity(self, env, output: dict[str, Any]) -> None:
        if not self._all_worker_terminal(env.run_id, "wow.slate-integrity-expert"):
            return
        candidates = repository.list_run_candidates(self.client, env.run_id)
        identity_jobs = {
            str(job["candidate_id"]): job
            for job in repository.list_jobs(self.client, env.run_id, worker_id="wow.slate-integrity-expert")
        }
        research_work: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for candidate in candidates:
            if candidate.get("terminal_label") is not None:
                continue
            cid = str(candidate["candidate_id"])
            job = identity_jobs.get(cid)
            if not job or job.get("status") != "SUCCEEDED":
                self._terminal(cid, "SLATE_PURGE", list((job or {}).get("blockers") or ["SLATE_IDENTITY_INCOMPLETE"]))
                continue
            research_work.append((candidate, dict(candidate.get("candidate_payload") or {})))
        if not research_work:
            self._finish_if_terminal(env.run_id)
            return
        if not repository.transition_run(
            self.client, env.run_id,
            expected_status="ROUTING", next_status="RESEARCH_QUEUED", stage="RESEARCH_QUEUED",
        ).applied:
            return
        as_of = self._run_as_of(env.run_id)
        for candidate, payload in research_work:
            for worker_id in RESEARCH_WORKERS:
                self._queue(
                    run_id=env.run_id, candidate_id=str(candidate["candidate_id"]), worker_id=worker_id,
                    evidence_snapshot_id=None, as_of=as_of,
                    payload={"candidate": payload, "evidence": payload.get("evidence")}, required=True,
                )

    def _after_researcher(self, env, output: dict[str, Any]) -> None:
        cid = str(env.candidate_id)
        jobs = []
        for worker_id in RESEARCH_WORKERS:
            jobs.extend(
                job for job in repository.list_jobs(self.client, env.run_id, worker_id=worker_id)
                if str(job.get("candidate_id")) == cid
            )
        if len(jobs) < len(RESEARCH_WORKERS) or not all(job.get("status") in _TERMINAL for job in jobs):
            return
        existing = [
            job for job in repository.list_jobs(self.client, env.run_id, worker_id=RESEARCH_RECONCILER)
            if str(job.get("candidate_id")) == cid
        ]
        if existing:
            return
        candidate = repository.get_candidate(self.client, cid) or {}
        if candidate.get("terminal_label") is not None:
            return
        payload = dict(candidate.get("candidate_payload") or {})
        reports = []
        team_jobs_ok = True
        for worker_id in RESEARCH_WORKERS:
            job = next(job for job in jobs if job.get("worker_id") == worker_id)
            team_jobs_ok = team_jobs_ok and job.get("status") == "SUCCEEDED"
            report = self._output_for_job(job) if job.get("status") == "SUCCEEDED" else {
                "research_status": "DATA_UNOBTAINABLE", "worker_id": worker_id,
            }
            reports.append(report)
        event_start_raw = payload.get("event_start_utc") or (payload.get("event_request") or {}).get("event_start_time_utc")
        self._queue(
            run_id=env.run_id, candidate_id=cid, worker_id=RESEARCH_RECONCILER,
            evidence_snapshot_id=None, as_of=self._run_as_of(env.run_id),
            payload={
                "research_reports": reports,
                "team_jobs_ok": team_jobs_ok,
                "evidence_present": isinstance(payload.get("evidence"), dict),
                "event_start_present": bool(event_start_raw),
            },
            required=True,
        )

    def _after_reconciler(self, env, output: dict[str, Any]) -> None:
        if not self._all_worker_terminal(env.run_id, RESEARCH_RECONCILER):
            return
        candidates = repository.list_run_candidates(self.client, env.run_id)
        jobs = {
            str(job["candidate_id"]): job
            for job in repository.list_jobs(self.client, env.run_id, worker_id=RESEARCH_RECONCILER)
        }
        evidence_work: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for candidate in candidates:
            if candidate.get("terminal_label") is not None:
                continue
            cid = str(candidate["candidate_id"])
            job = jobs.get(cid)
            if not job or job.get("status") != "SUCCEEDED":
                self._terminal(cid, "REJECT_DATA_QUALITY", list((job or {}).get("blockers") or ["RESEARCH_TEAM_INCOMPLETE"]))
                continue
            payload = dict(candidate.get("candidate_payload") or {})
            evidence = payload.get("evidence")
            event_start_raw = payload.get("event_start_utc") or (payload.get("event_request") or {}).get("event_start_time_utc")
            if not isinstance(evidence, dict) or not event_start_raw:
                self._terminal(cid, "REJECT_DATA_QUALITY", ["EVIDENCE_SNAPSHOT_MISSING"])
                continue
            evidence_work.append((candidate, evidence))
        if not evidence_work:
            self._finish_if_terminal(env.run_id)
            return
        if not repository.transition_run(
            self.client, env.run_id,
            expected_status="RESEARCH_RUNNING", next_status="EVIDENCE_QUEUED", stage="EVIDENCE_QUEUED",
        ).applied:
            return
        as_of = self._run_as_of(env.run_id)
        for candidate, evidence in evidence_work:
            self._queue(
                run_id=env.run_id, candidate_id=str(candidate["candidate_id"]), worker_id="wow.evidence-hydration",
                evidence_snapshot_id=None, as_of=as_of, payload={"evidence": evidence}, required=True,
            )
