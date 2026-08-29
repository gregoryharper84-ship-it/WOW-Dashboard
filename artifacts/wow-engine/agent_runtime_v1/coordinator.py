from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from .contracts import JobStatus, TERMINAL_JOB_STATES, canonical_hash
from .evidence import validate_evidence_payload
from .job_store import JobRepository

_TERMINAL = {state.value for state in TERMINAL_JOB_STATES}


class Coordinator:
    """Machine-enforced continuation for the WOW Agent Runtime stage graph.

    This coordinator never creates probabilities. It persists stage outputs,
    advances only legal predecessor-complete stages, and routes blockers toward
    terminal reduction/reconciliation.
    """
    def __init__(self, repo: JobRepository | None = None):
        self.repo = repo or JobRepository()

    def _queue(self, **kwargs):
        from .orchestrator import Orchestrator
        return Orchestrator(self.repo).queue_worker(**kwargs)

    def _run_as_of(self, run_id: str) -> datetime:
        run = self.repo.get_run(run_id)
        value = run.get("requested_as_of") if run else None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else datetime.now(timezone.utc)

    def on_job_started(self, worker_id: str, run_id: str) -> None:
        mapping = {
            "wow.parallel-discovery-router": ("DISCOVERY_QUEUED", "DISCOVERY_RUNNING"),
            "wow.evidence-hydration": ("EVIDENCE_QUEUED", "EVIDENCE_RUNNING"),
            "wow.controlling-model": ("MODELING_QUEUED", "MODELING_RUNNING"),
            "wow.exact-line-market-auditor": ("AUDIT_QUEUED", "AUDIT_RUNNING"),
            "wow.final-refresh-governor": ("AUDIT_RUNNING", "FINAL_REFRESH"),
        }
        pair = mapping.get(worker_id)
        if pair:
            self.repo.transition_run(run_id, pair[0], pair[1], pair[1])

    def on_job_terminal(self, env, output: dict[str, Any]) -> None:
        worker = env.worker_id
        if worker == "wow.parallel-discovery-router":
            self._after_discovery(env, output)
        elif worker == "wow.slate-integrity-expert":
            self._after_identity(env, output)
        elif worker == "wow.evidence-hydration":
            self._after_evidence(env, output)
        elif worker == "wow.controlling-model":
            self._after_model(env, output)
        elif worker == "wow.failure-path-framework":
            self._after_failure_paths(env, output)
        elif worker == "wow.dynamic-calibration-expert":
            self._after_calibration(env, output)
        elif worker == "wow.exact-line-market-auditor":
            self._after_market(env, output)
        elif worker == "wow.structure-exposure-governor":
            self._after_structure(env, output)
        elif worker == "wow.final-refresh-governor":
            self._after_final_refresh(env, output)
        elif worker == "wow.terminal-ceiling-reducer":
            self._after_reducer(env, output)

    def _all_worker_terminal(self, run_id: str, worker_id: str) -> bool:
        jobs = self.repo.list_jobs(run_id, worker_id=worker_id)
        return bool(jobs) and all(str(job["status"]) in _TERMINAL for job in jobs)

    def _output_for_job(self, job: dict[str, Any]) -> dict[str, Any]:
        return self.repo.get_output(str(job["job_id"])) or {}

    def _candidate_payload(self, candidate_id: str) -> dict[str, Any]:
        row = self.repo.get_candidate(candidate_id) or {}
        payload = row.get("candidate_payload")
        return dict(payload) if isinstance(payload, dict) else {}

    def _terminal(self, candidate_id: str, label: str, blockers: list[str], ceiling: str = "RESEARCH_INTEREST") -> None:
        self.repo.set_candidate_terminal(candidate_id=candidate_id, label=label, ceiling=ceiling, blockers=blockers, probability_publishable=False)

    def _after_discovery(self, env, output: dict[str, Any]) -> None:
        if output.get("status") != JobStatus.SUCCEEDED.value:
            self.repo.transition_run(env.run_id, "DISCOVERY_RUNNING", "FAILED", "DISCOVERY_FAILED")
            return
        candidates = list((output.get("output") or {}).get("candidates") or [])
        rows = self.repo.upsert_candidates(env.run_id, candidates)
        self.repo.transition_run(env.run_id, "DISCOVERY_RUNNING", "ROUTING", "ROUTING")
        if not rows:
            self.repo.transition_run(env.run_id, "ROUTING", "RECONCILING", "RECONCILING")
            self.repo.reconcile_run(env.run_id)
            self.repo.transition_run(env.run_id, "RECONCILING", "COMPLETED", "COMPLETED")
            return
        as_of = self._run_as_of(env.run_id)
        for row in rows:
            payload = dict(row.get("candidate_payload") or {})
            self._queue(run_id=env.run_id, candidate_id=str(row["candidate_id"]), worker_id="wow.slate-integrity-expert",
                        evidence_snapshot_id=None, as_of=as_of, payload={"candidate": payload}, required=True)

    def _after_identity(self, env, output: dict[str, Any]) -> None:
        if not self._all_worker_terminal(env.run_id, "wow.slate-integrity-expert"):
            return
        candidates = self.repo.list_candidates(env.run_id)
        as_of = self._run_as_of(env.run_id)
        evidence_work: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
        identity_jobs = {str(j["candidate_id"]): j for j in self.repo.list_jobs(env.run_id, worker_id="wow.slate-integrity-expert")}
        for candidate in candidates:
            cid = str(candidate["candidate_id"]); job = identity_jobs.get(cid)
            if not job or job["status"] != JobStatus.SUCCEEDED.value:
                blockers = list((job or {}).get("blockers") or ["SLATE_IDENTITY_INCOMPLETE"])
                self._terminal(cid, "SLATE_PURGE", blockers)
                continue
            payload = dict(candidate.get("candidate_payload") or {})
            evidence = payload.get("evidence")
            event_start_raw = payload.get("event_start_utc") or (payload.get("event_request") or {}).get("event_start_time_utc")
            if not isinstance(evidence, dict) or not event_start_raw:
                self._terminal(cid, "REJECT_DATA_QUALITY", ["EVIDENCE_SNAPSHOT_MISSING"])
                continue
            event_start = event_start_raw if isinstance(event_start_raw, datetime) else datetime.fromisoformat(str(event_start_raw).replace("Z", "+00:00"))
            missing, conflicts = validate_evidence_payload(evidence)
            eid = self.repo.create_evidence_snapshot(
                run_id=env.run_id, candidate_id=cid, as_of=as_of, event_start=event_start, payload=evidence,
                provenance={"source_attempts": evidence.get("source_attempts") or []}, missing=missing, conflicts=conflicts,
                payload_hash=canonical_hash(evidence),
            )
            evidence_work.append((candidate, eid, evidence))
        if not evidence_work:
            self._finish_if_terminal(env.run_id)
            return
        if not self.repo.transition_run(env.run_id, "ROUTING", "EVIDENCE_QUEUED", "EVIDENCE_QUEUED"):
            return
        for candidate, eid, evidence in evidence_work:
            self._queue(run_id=env.run_id, candidate_id=str(candidate["candidate_id"]), worker_id="wow.evidence-hydration",
                        evidence_snapshot_id=eid, as_of=as_of, payload={"evidence": evidence, "evidence_snapshot_id": eid}, required=True)

    def _after_evidence(self, env, output: dict[str, Any]) -> None:
        if not self._all_worker_terminal(env.run_id, "wow.evidence-hydration"):
            return
        as_of = self._run_as_of(env.run_id)
        model_work = []
        for job in self.repo.list_jobs(env.run_id, worker_id="wow.evidence-hydration"):
            cid = str(job["candidate_id"])
            if job["status"] != JobStatus.SUCCEEDED.value:
                self._terminal(cid, "REJECT_DATA_QUALITY", list(job.get("blockers") or ["EVIDENCE_NOT_VERIFIED"]))
                continue
            candidate = self.repo.get_candidate(cid) or {}; payload = dict(candidate.get("candidate_payload") or {})
            model_payload = {
                "sport": candidate.get("sport"), "market_family": candidate.get("market_family"), "period": candidate.get("period"),
                "stat_family": candidate.get("stat_family"), "event_request": payload.get("event_request"), "capability": payload.get("capability"),
            }
            model_work.append((candidate, model_payload))
        if not model_work:
            self._finish_if_terminal(env.run_id); return
        if not self.repo.transition_run(env.run_id, "EVIDENCE_RUNNING", "MODELING_QUEUED", "MODELING_QUEUED"):
            return
        for candidate, payload in model_work:
            self._queue(run_id=env.run_id, candidate_id=str(candidate["candidate_id"]), worker_id="wow.controlling-model",
                        evidence_snapshot_id=str(candidate.get("evidence_snapshot_id")), as_of=as_of, payload=payload, required=True)

    def _after_model(self, env, output: dict[str, Any]) -> None:
        if not self._all_worker_terminal(env.run_id, "wow.controlling-model"):
            return
        as_of = self._run_as_of(env.run_id); failure_work=[]
        for job in self.repo.list_jobs(env.run_id, worker_id="wow.controlling-model"):
            cid=str(job["candidate_id"]); out=self._output_for_job(job); body=out.get("output") or {}
            if job["status"] != JobStatus.SUCCEEDED.value:
                self._terminal(cid,"MODEL_UNAVAILABLE",list(job.get("blockers") or ["MODEL_UNAVAILABLE"])); continue
            if body.get("probability_publishable") is not True:
                self._terminal(cid,"MODEL_QUALIFIED_HOLD",list(job.get("blockers") or ["PROBABILITY_PUBLICATION_HELD"]),"MODEL_QUALIFIED_HOLD"); continue
            payload=self._candidate_payload(cid); components=payload.get("failure_components")
            failure_work.append((cid,components,self.repo.get_candidate(cid)))
        if not failure_work:
            self._finish_if_terminal(env.run_id); return
        for cid,components,candidate in failure_work:
            self._queue(run_id=env.run_id,candidate_id=cid,worker_id="wow.failure-path-framework",
                        evidence_snapshot_id=str((candidate or {}).get("evidence_snapshot_id")),as_of=as_of,payload={"components":components},required=True)

    def _after_failure_paths(self, env, output: dict[str, Any]) -> None:
        cid=str(env.candidate_id)
        if output.get("status") != JobStatus.SUCCEEDED.value:
            self._terminal(cid,"MODEL_QUALIFIED_HOLD",list(output.get("blockers") or ["FAILURE_PATH_GATE_BLOCKED"]),"MODEL_QUALIFIED_HOLD")
            self._maybe_enter_audit(env.run_id); return
        payload=self._candidate_payload(cid); calibration=payload.get("dynamic_calibration") or {}
        candidate=self.repo.get_candidate(cid) or {}
        self._queue(run_id=env.run_id,candidate_id=cid,worker_id="wow.dynamic-calibration-expert",
                    evidence_snapshot_id=str(candidate.get("evidence_snapshot_id")),as_of=self._run_as_of(env.run_id),payload=calibration,required=True)

    def _after_calibration(self, env, output: dict[str, Any]) -> None:
        cid=str(env.candidate_id)
        if output.get("status") != JobStatus.SUCCEEDED.value:
            self._terminal(cid,"MODEL_QUALIFIED_HOLD",list(output.get("blockers") or ["DYNAMIC_CALIBRATION_BLOCKED"]),"MODEL_QUALIFIED_HOLD")
        self._maybe_enter_audit(env.run_id)

    def _maybe_enter_audit(self, run_id: str) -> None:
        active=[c for c in self.repo.list_candidates(run_id) if c.get("terminal_label") is None]
        if not active:
            self._finish_if_terminal(run_id); return
        cal_jobs={str(j["candidate_id"]):j for j in self.repo.list_jobs(run_id,worker_id="wow.dynamic-calibration-expert")}
        if not all(cid in cal_jobs and cal_jobs[cid]["status"] in _TERMINAL for cid in [str(c["candidate_id"]) for c in active]):
            return
        ready=[c for c in active if cal_jobs[str(c["candidate_id"])]["status"]==JobStatus.SUCCEEDED.value]
        for c in active:
            if c not in ready:
                self._terminal(str(c["candidate_id"]),"MODEL_QUALIFIED_HOLD",list(cal_jobs[str(c["candidate_id"])].get("blockers") or ["DYNAMIC_CALIBRATION_BLOCKED"]),"MODEL_QUALIFIED_HOLD")
        if not ready:
            self._finish_if_terminal(run_id); return
        if not self.repo.transition_run(run_id,"MODELING_RUNNING","AUDIT_QUEUED","AUDIT_QUEUED"):
            return
        for c in ready:
            payload=self._candidate_payload(str(c["candidate_id"])); market=payload.get("market_audit") or {}
            self._queue(run_id=run_id,candidate_id=str(c["candidate_id"]),worker_id="wow.exact-line-market-auditor",
                        evidence_snapshot_id=str(c.get("evidence_snapshot_id")),as_of=self._run_as_of(run_id),payload=market,required=True)

    def _after_market(self, env, output: dict[str, Any]) -> None:
        cid=str(env.candidate_id); candidate=self.repo.get_candidate(cid) or {}
        if output.get("status") != JobStatus.SUCCEEDED.value:
            self._terminal(cid,"MARKET_VERIFIED_HOLD",list(output.get("blockers") or ["MARKET_GATE_BLOCKED"]),"MARKET_VERIFIED_HOLD")
            self._finish_if_terminal(env.run_id); return
        payload=self._candidate_payload(cid); structure=payload.get("structure_audit") or {}
        self._queue(run_id=env.run_id,candidate_id=cid,worker_id="wow.structure-exposure-governor",
                    evidence_snapshot_id=str(candidate.get("evidence_snapshot_id")),as_of=self._run_as_of(env.run_id),payload=structure,required=True)

    def _after_structure(self, env, output: dict[str, Any]) -> None:
        cid=str(env.candidate_id); candidate=self.repo.get_candidate(cid) or {}
        if output.get("status") != JobStatus.SUCCEEDED.value:
            self._terminal(cid,"STRUCTURE_VERIFIED_HOLD",list(output.get("blockers") or ["STRUCTURE_GATE_BLOCKED"]),"STRUCTURE_VERIFIED_HOLD")
            self._finish_if_terminal(env.run_id); return
        payload=self._candidate_payload(cid); refresh=dict(payload.get("final_refresh") or {})
        if "event_start" not in refresh:
            refresh["event_start"] = payload.get("event_start_utc") or (payload.get("event_request") or {}).get("event_start_time_utc")
        self._queue(run_id=env.run_id,candidate_id=cid,worker_id="wow.final-refresh-governor",
                    evidence_snapshot_id=str(candidate.get("evidence_snapshot_id")),as_of=self._run_as_of(env.run_id),payload=refresh,required=True)

    def _after_final_refresh(self, env, output: dict[str, Any]) -> None:
        cid=str(env.candidate_id); candidate=self.repo.get_candidate(cid) or {}
        if output.get("status") != JobStatus.SUCCEEDED.value:
            self._terminal(cid,"FINAL_REFRESH_HOLD",list(output.get("blockers") or ["FINAL_REFRESH_BLOCKED"]),"FINAL_REFRESH_HOLD")
            self._finish_if_terminal(env.run_id); return
        jobs=[]
        for job in self.repo.list_jobs(env.run_id,candidate_id=cid):
            if job["worker_id"]=="wow.terminal-ceiling-reducer": continue
            jobs.append({"worker_id":job["worker_id"],"status":job["status"],"ceiling":job.get("ceiling") or "RESEARCH_INTEREST","blockers":job.get("blockers") or []})
        self._queue(run_id=env.run_id,candidate_id=cid,worker_id="wow.terminal-ceiling-reducer",
                    evidence_snapshot_id=str(candidate.get("evidence_snapshot_id")),as_of=self._run_as_of(env.run_id),
                    payload={"controlling_worker_id":candidate.get("controlling_worker_id") or "wow.controlling-model","required_jobs":jobs},required=True)

    def _after_reducer(self, env, output: dict[str, Any]) -> None:
        cid=str(env.candidate_id); body=output.get("output") or {}
        if output.get("status") != JobStatus.SUCCEEDED.value:
            self._terminal(cid,"RESEARCH_INTEREST",list(output.get("blockers") or ["TERMINAL_REDUCER_BLOCKED"])); self._finish_if_terminal(env.run_id); return
        self.repo.set_candidate_terminal(
            candidate_id=cid,
            label=str(body.get("terminal_label") or "RESEARCH_INTEREST"),
            ceiling=str(body.get("final_terminal_ceiling") or "RESEARCH_INTEREST"),
            blockers=list(body.get("blockers") or []),
            probability_publishable=body.get("probability_publishable") is True,
        )
        self._finish_if_terminal(env.run_id)

    def _finish_if_terminal(self, run_id: str) -> None:
        reconciliation=self.repo.reconcile_run(run_id)
        if reconciliation["rows_pending"]:
            return
        run=self.repo.get_run(run_id) or {}; current=str(run.get("status"))
        if current not in {"RECONCILING","COMPLETED","COMPLETED_WITH_BLOCKERS","FAILED","CANCELED"}:
            if not self.repo.transition_run(run_id,current,"RECONCILING","RECONCILING"):
                return
            current="RECONCILING"
        if current=="RECONCILING":
            target="COMPLETED" if reconciliation["rows_in"]==reconciliation["rows_completed"] else "COMPLETED_WITH_BLOCKERS"
            self.repo.transition_run(run_id,"RECONCILING",target,target)
