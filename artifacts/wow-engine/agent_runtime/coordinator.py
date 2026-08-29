"""Machine-enforced continuation for the WOW Agent Runtime stage graph
(packet section 14's dependency graph, section 5's run state machine).
Ported from PR #33 (feature/wow-agent-runtime-v1) during the convergence
pass, adapted to this module's repository (explicit client, public schema,
the atomic complete_job RPC) instead of PR #33's raw-psycopg JobRepository.

This coordinator never creates probabilities. It persists stage outputs,
advances only legal predecessor-complete stages, and routes blockers toward
terminal reduction/reconciliation. Evidence is folded into candidate_payload
rather than written to a separate evidence_snapshots table — Phase 0's
overlap audit found wow_prop_evidence_snapshots/wow_event_evidence already
own that concept for a real lane; this runtime doesn't have one wired yet
(that's Phase 4).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_runtime import repository
from agent_runtime.idempotency import compute_request_hash as canonical_hash
from agent_runtime.evidence import validate_evidence_payload
from agent_runtime.state_machine import JOB_TERMINAL_STATES

_TERMINAL = set(JOB_TERMINAL_STATES)


class Coordinator:
    def __init__(self, client: Any):
        self.client = client

    def _queue(self, **kwargs):
        from agent_runtime.orchestrator import Orchestrator
        return Orchestrator(self.client).queue_worker(**kwargs)

    def _run_as_of(self, run_id: str) -> datetime:
        run = repository.get_run(self.client, run_id)
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
            repository.transition_run(self.client, run_id, expected_status=pair[0], next_status=pair[1], stage=pair[1])

    def on_job_terminal(self, env, output: dict[str, Any]) -> None:
        worker = env.worker_id
        handlers = {
            "wow.parallel-discovery-router": self._after_discovery,
            "wow.slate-integrity-expert": self._after_identity,
            "wow.evidence-hydration": self._after_evidence,
            "wow.controlling-model": self._after_model,
            "wow.failure-path-framework": self._after_failure_paths,
            "wow.dynamic-calibration-expert": self._after_calibration,
            "wow.exact-line-market-auditor": self._after_market,
            "wow.structure-exposure-governor": self._after_structure,
            "wow.final-refresh-governor": self._after_final_refresh,
            "wow.terminal-ceiling-reducer": self._after_reducer,
        }
        handler = handlers.get(worker)
        if handler:
            handler(env, output)

    def _all_worker_terminal(self, run_id: str, worker_id: str) -> bool:
        jobs = repository.list_jobs(self.client, run_id, worker_id=worker_id)
        return bool(jobs) and all(str(job["status"]) in _TERMINAL for job in jobs)

    def _output_for_job(self, job: dict[str, Any]) -> dict[str, Any]:
        output = repository.get_output(self.client, str(job["job_id"]))
        return (output or {}).get("output") or {}

    def _candidate_payload(self, candidate_id: str) -> dict[str, Any]:
        row = repository.get_candidate(self.client, candidate_id) or {}
        payload = row.get("candidate_payload")
        return dict(payload) if isinstance(payload, dict) else {}

    def _terminal(self, candidate_id: str, label: str, blockers: list[str], ceiling: str = "RESEARCH_INTEREST") -> None:
        """Every terminalized candidate gets a row in wow_agent_terminal_decisions,
        not only ones that reached the reducer — matching PR #33's
        job_store.py::set_candidate_terminal(), which wrote both the candidate
        row and the terminal_decisions row in one call. Early-exit paths
        (SLATE_PURGE, REJECT_DATA_QUALITY, MODEL_UNAVAILABLE, a HOLD with
        blockers) are still real terminal decisions and belong in the
        immutable ledger, same as one that reached wow.terminal-ceiling-reducer."""
        candidate = repository.get_candidate(self.client, candidate_id) or {}
        applied = repository.set_candidate_terminal(
            self.client, candidate_id, terminal_label=label, terminal_ceiling=ceiling,
            blockers=blockers, controlling_worker_id=None,
        )
        if applied:
            decision_hash = canonical_hash({
                "candidate_id": candidate_id, "label": label, "ceiling": ceiling,
                "blockers": sorted(set(blockers)), "probability_publishable": False,
            })
            repository.record_terminal_decision(
                self.client, run_id=str(candidate.get("run_id") or ""), candidate_id=candidate_id,
                final_terminal_ceiling=ceiling, terminal_label=label,
                controlling_worker_id=candidate.get("controlling_worker_id"),
                probability_publishable=False, blockers=blockers,
                reducer_version="wow.terminal-ceiling-reducer/1.0.0", decision_hash=decision_hash,
            )

    def _after_discovery(self, env, output: dict[str, Any]) -> None:
        if output.get("status") != "SUCCEEDED":
            repository.transition_run(self.client, env.run_id, expected_status="DISCOVERY_RUNNING", next_status="FAILED", stage="DISCOVERY_FAILED")
            return
        candidates = list((output.get("output") or {}).get("candidates") or [])
        rows = repository.upsert_candidates(self.client, env.run_id, candidates)
        repository.transition_run(self.client, env.run_id, expected_status="DISCOVERY_RUNNING", next_status="ROUTING", stage="ROUTING")
        if not rows:
            self._finish_if_terminal(env.run_id)
            return
        as_of = self._run_as_of(env.run_id)
        for row in rows:
            payload = dict(row.get("candidate_payload") or {})
            self._queue(
                run_id=env.run_id, candidate_id=str(row["candidate_id"]), worker_id="wow.slate-integrity-expert",
                evidence_snapshot_id=None, as_of=as_of, payload={"candidate": payload}, required=True,
            )

    def _after_identity(self, env, output: dict[str, Any]) -> None:
        if not self._all_worker_terminal(env.run_id, "wow.slate-integrity-expert"):
            return
        candidates = repository.list_run_candidates(self.client, env.run_id)
        as_of = self._run_as_of(env.run_id)
        evidence_work: list[tuple[dict[str, Any], dict[str, Any]]] = []
        identity_jobs = {str(j["candidate_id"]): j for j in repository.list_jobs(self.client, env.run_id, worker_id="wow.slate-integrity-expert")}
        for candidate in candidates:
            if candidate.get("terminal_label") is not None:
                continue
            cid = str(candidate["candidate_id"])
            job = identity_jobs.get(cid)
            if not job or job["status"] != "SUCCEEDED":
                blockers = list((job or {}).get("blockers") or ["SLATE_IDENTITY_INCOMPLETE"])
                self._terminal(cid, "SLATE_PURGE", blockers)
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
        if not repository.transition_run(self.client, env.run_id, expected_status="ROUTING", next_status="EVIDENCE_QUEUED", stage="EVIDENCE_QUEUED").applied:
            return
        for candidate, evidence in evidence_work:
            self._queue(
                run_id=env.run_id, candidate_id=str(candidate["candidate_id"]), worker_id="wow.evidence-hydration",
                evidence_snapshot_id=None, as_of=as_of, payload={"evidence": evidence}, required=True,
            )

    def _after_evidence(self, env, output: dict[str, Any]) -> None:
        if not self._all_worker_terminal(env.run_id, "wow.evidence-hydration"):
            return
        as_of = self._run_as_of(env.run_id)
        model_work = []
        for job in repository.list_jobs(self.client, env.run_id, worker_id="wow.evidence-hydration"):
            cid = str(job["candidate_id"])
            candidate = repository.get_candidate(self.client, cid) or {}
            if candidate.get("terminal_label") is not None:
                continue
            if job["status"] != "SUCCEEDED":
                self._terminal(cid, "REJECT_DATA_QUALITY", list(job.get("blockers") or ["EVIDENCE_NOT_VERIFIED"]))
                continue
            # Fold the sealed evidence output into candidate_payload — no
            # separate evidence_snapshots table (see module docstring).
            job_output = self._output_for_job(job)
            payload = dict(candidate.get("candidate_payload") or {})
            payload["sealed_evidence"] = job_output.get("sealed_evidence")
            payload["evidence_payload_hash"] = job_output.get("payload_hash")
            repository.upsert_candidates(self.client, env.run_id, [{**payload, "canonical_key": candidate.get("canonical_key")}])
            model_payload = {
                "sport": candidate.get("sport"), "market_family": candidate.get("market_family"), "period": candidate.get("period"),
                "stat_family": candidate.get("stat_family"), "event_request": payload.get("event_request"), "capability": payload.get("capability"),
            }
            model_work.append((candidate, model_payload))
        if not model_work:
            self._finish_if_terminal(env.run_id)
            return
        if not repository.transition_run(self.client, env.run_id, expected_status="EVIDENCE_RUNNING", next_status="MODELING_QUEUED", stage="MODELING_QUEUED").applied:
            return
        for candidate, payload in model_work:
            self._queue(
                run_id=env.run_id, candidate_id=str(candidate["candidate_id"]), worker_id="wow.controlling-model",
                evidence_snapshot_id=None, as_of=as_of, payload=payload, required=True,
            )

    def _after_model(self, env, output: dict[str, Any]) -> None:
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
                self._terminal(cid, "MODEL_UNAVAILABLE", list(job.get("blockers") or ["MODEL_UNAVAILABLE"]), "MODEL_UNAVAILABLE")
                continue
            if body.get("probability_publishable") is not True and "PROBABILITY_PUBLICATION_HELD" not in (job.get("blockers") or []):
                self._terminal(cid, "MODEL_QUALIFIED_HOLD", list(job.get("blockers") or ["PROBABILITY_PUBLICATION_HELD"]), "MODEL_QUALIFIED_HOLD")
                continue
            payload = self._candidate_payload(cid)
            failure_work.append((cid, payload.get("failure_components"), candidate))
        if not failure_work:
            self._finish_if_terminal(env.run_id)
            return
        for cid, components, candidate in failure_work:
            self._queue(
                run_id=env.run_id, candidate_id=cid, worker_id="wow.failure-path-framework",
                evidence_snapshot_id=None, as_of=as_of, payload={"components": components}, required=True,
            )

    def _after_failure_paths(self, env, output: dict[str, Any]) -> None:
        cid = str(env.candidate_id)
        if output.get("status") != "SUCCEEDED":
            self._terminal(cid, "MODEL_QUALIFIED_HOLD", list(output.get("blockers") or ["FAILURE_PATH_GATE_BLOCKED"]), "MODEL_QUALIFIED_HOLD")
            self._maybe_enter_audit(env.run_id)
            return
        payload = self._candidate_payload(cid)
        calibration = payload.get("dynamic_calibration") or {}
        self._queue(
            run_id=env.run_id, candidate_id=cid, worker_id="wow.dynamic-calibration-expert",
            evidence_snapshot_id=None, as_of=self._run_as_of(env.run_id), payload=calibration, required=True,
        )

    def _after_calibration(self, env, output: dict[str, Any]) -> None:
        cid = str(env.candidate_id)
        if output.get("status") != "SUCCEEDED":
            self._terminal(cid, "MODEL_QUALIFIED_HOLD", list(output.get("blockers") or ["DYNAMIC_CALIBRATION_BLOCKED"]), "MODEL_QUALIFIED_HOLD")
        self._maybe_enter_audit(env.run_id)

    def _maybe_enter_audit(self, run_id: str) -> None:
        active = [c for c in repository.list_run_candidates(self.client, run_id) if c.get("terminal_label") is None]
        if not active:
            self._finish_if_terminal(run_id)
            return
        cal_jobs = {str(j["candidate_id"]): j for j in repository.list_jobs(self.client, run_id, worker_id="wow.dynamic-calibration-expert")}
        active_ids = [str(c["candidate_id"]) for c in active]
        if not all(cid in cal_jobs and cal_jobs[cid]["status"] in _TERMINAL for cid in active_ids):
            return
        ready = [c for c in active if cal_jobs[str(c["candidate_id"])]["status"] == "SUCCEEDED"]
        for c in active:
            if c not in ready:
                cid = str(c["candidate_id"])
                self._terminal(cid, "MODEL_QUALIFIED_HOLD", list(cal_jobs[cid].get("blockers") or ["DYNAMIC_CALIBRATION_BLOCKED"]), "MODEL_QUALIFIED_HOLD")
        if not ready:
            self._finish_if_terminal(run_id)
            return
        if not repository.transition_run(self.client, run_id, expected_status="MODELING_RUNNING", next_status="AUDIT_QUEUED", stage="AUDIT_QUEUED").applied:
            return
        for c in ready:
            payload = self._candidate_payload(str(c["candidate_id"]))
            market = payload.get("market_audit") or {}
            self._queue(
                run_id=run_id, candidate_id=str(c["candidate_id"]), worker_id="wow.exact-line-market-auditor",
                evidence_snapshot_id=None, as_of=self._run_as_of(run_id), payload=market, required=True,
            )

    def _after_market(self, env, output: dict[str, Any]) -> None:
        cid = str(env.candidate_id)
        if output.get("status") != "SUCCEEDED":
            self._terminal(cid, "MARKET_VERIFIED_HOLD", list(output.get("blockers") or ["MARKET_GATE_BLOCKED"]), "MARKET_VERIFIED_HOLD")
            self._finish_if_terminal(env.run_id)
            return
        payload = self._candidate_payload(cid)
        structure = payload.get("structure_audit") or {}
        self._queue(
            run_id=env.run_id, candidate_id=cid, worker_id="wow.structure-exposure-governor",
            evidence_snapshot_id=None, as_of=self._run_as_of(env.run_id), payload=structure, required=True,
        )

    def _after_structure(self, env, output: dict[str, Any]) -> None:
        cid = str(env.candidate_id)
        if output.get("status") != "SUCCEEDED":
            self._terminal(cid, "STRUCTURE_VERIFIED_HOLD", list(output.get("blockers") or ["STRUCTURE_GATE_BLOCKED"]), "STRUCTURE_VERIFIED_HOLD")
            self._finish_if_terminal(env.run_id)
            return
        payload = self._candidate_payload(cid)
        refresh = dict(payload.get("final_refresh") or {})
        if "event_start" not in refresh:
            refresh["event_start"] = payload.get("event_start_utc") or (payload.get("event_request") or {}).get("event_start_time_utc")
        self._queue(
            run_id=env.run_id, candidate_id=cid, worker_id="wow.final-refresh-governor",
            evidence_snapshot_id=None, as_of=self._run_as_of(env.run_id), payload=refresh, required=True,
        )

    def _after_final_refresh(self, env, output: dict[str, Any]) -> None:
        cid = str(env.candidate_id)
        if output.get("status") != "SUCCEEDED":
            self._terminal(cid, "FINAL_REFRESH_HOLD", list(output.get("blockers") or ["FINAL_REFRESH_BLOCKED"]), "FINAL_REFRESH_HOLD")
            self._finish_if_terminal(env.run_id)
            return
        jobs = []
        for job in repository.list_jobs(self.client, env.run_id, candidate_id=cid):
            if job["worker_id"] == "wow.terminal-ceiling-reducer":
                continue
            jobs.append({"worker_id": job["worker_id"], "status": job["status"], "ceiling": job.get("ceiling") or "RESEARCH_INTEREST", "blockers": job.get("blockers") or []})
        candidate = repository.get_candidate(self.client, cid) or {}
        self._queue(
            run_id=env.run_id, candidate_id=cid, worker_id="wow.terminal-ceiling-reducer",
            evidence_snapshot_id=None, as_of=self._run_as_of(env.run_id),
            payload={"controlling_worker_id": candidate.get("controlling_worker_id") or "wow.controlling-model", "required_jobs": jobs},
            required=True,
        )

    def _after_reducer(self, env, output: dict[str, Any]) -> None:
        cid = str(env.candidate_id)
        body = output.get("output") or {}
        if output.get("status") != "SUCCEEDED":
            self._terminal(cid, "RESEARCH_INTEREST", list(output.get("blockers") or ["TERMINAL_REDUCER_BLOCKED"]))
            self._finish_if_terminal(env.run_id)
            return
        candidate = repository.get_candidate(self.client, cid) or {}
        applied = repository.set_candidate_terminal(
            self.client, cid,
            terminal_label=str(body.get("terminal_label") or "RESEARCH_INTEREST"),
            terminal_ceiling=str(body.get("final_terminal_ceiling") or "RESEARCH_INTEREST"),
            blockers=list(body.get("blockers") or []),
            controlling_worker_id=candidate.get("controlling_worker_id"),
        )
        if applied:
            decision_hash = canonical_hash({
                "candidate_id": cid, "label": body.get("terminal_label"), "ceiling": body.get("final_terminal_ceiling"),
                "blockers": sorted(set(body.get("blockers") or [])), "probability_publishable": body.get("probability_publishable") is True,
            })
            repository.record_terminal_decision(
                self.client, run_id=env.run_id, candidate_id=cid,
                final_terminal_ceiling=str(body.get("final_terminal_ceiling") or "RESEARCH_INTEREST"),
                terminal_label=str(body.get("terminal_label") or "RESEARCH_INTEREST"),
                controlling_worker_id=candidate.get("controlling_worker_id"),
                probability_publishable=body.get("probability_publishable") is True,
                blockers=list(body.get("blockers") or []),
                reducer_version="wow.terminal-ceiling-reducer/1.0.0", decision_hash=decision_hash,
            )
        self._finish_if_terminal(env.run_id)

    def _finish_if_terminal(self, run_id: str) -> None:
        reconciliation = repository.reconcile_run(self.client, run_id)
        if reconciliation["rows_pending"]:
            return
        run = repository.get_run(self.client, run_id) or {}
        current = str(run.get("status"))
        if current in {"RECONCILING", "COMPLETED", "COMPLETED_WITH_BLOCKERS", "FAILED", "CANCELED"}:
            if current != "RECONCILING":
                return
        else:
            if not repository.transition_run(self.client, run_id, expected_status=current, next_status="RECONCILING", stage="RECONCILING").applied:
                return
            current = "RECONCILING"
        if current == "RECONCILING":
            target = "COMPLETED" if reconciliation["rows_in"] == reconciliation["rows_completed"] else "COMPLETED_WITH_BLOCKERS"
            repository.transition_run(self.client, run_id, expected_status="RECONCILING", next_status=target, stage=target)
