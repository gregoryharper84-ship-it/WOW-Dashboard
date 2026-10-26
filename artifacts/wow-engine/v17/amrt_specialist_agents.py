"""Five AMRT model-restoration specialists, source-only and fail closed.

Callable agent contracts for the *existing* Conductor/Engineering worker. These
functions never poll, write, dispatch, merge, deploy, certify or score games.
Only an independently authorized production integration may invoke them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from v17.amrt_closure_gate import (
    TEAM, TERMINAL_AUTHORITY, lease_reentry, provider_attempt,
)
from v17.amrt_operational_lanes import RoleReceipt

SOURCE_STATUS = "SOURCE_ONLY_NOT_ACTIVATED"
AGENT_TO_EXISTING_ROLE = {
    "MODEL_RECOVERY_COMMANDER": "BLOCKER_SENTINEL",
    "MODEL_DEPENDENCY_INVESTIGATOR": "ROOT_CAUSE_INVESTIGATOR",
    "MODEL_RESTORATION_ENGINEER": "MODEL_RECOVERY_ENGINEER",
    "MODEL_RECOVERY_REGRESSION_SPECIALIST": "CODE_CI_ENGINEER",
    "RECOVERY_ESCALATION_DEADLOCK_BREAKER": "CODE_CI_ENGINEER",
}
STAGES = (
    "discovery", "canonical_identity", "data_hydration", "feature_freshness",
    "fitted_artifact", "model_registration", "scorer_invocation",
    "probability_package", "dynamic_calibration", "final_refresh",
    "publication_audit", "action_readback", "persistence_reconciliation",
)
PASS = "PASS"
KNOWN_STATES = frozenset({
    PASS, "FAIL", "MODEL_UNAVAILABLE", "EVIDENCE_NOT_EMITTED",
    "STALE", "NOT_RUN", "INPUT_INCOMPLETE", "HELD",
})
PROTECTED_MODEL_CHANGES = frozenset({
    "fitted_probabilities", "fitted_artifacts", "coefficients",
    "calibration_math", "calibration_policy", "model_qualification",
    "publication_thresholds", "probability_semantics",
    "terminal_precedence", "failure_path_weighting",
})
REPLAY_CASES = (
    "ORIGINAL_PRE_FIX_REPRODUCES",
    "ORIGINAL_POST_FIX_PASSES",
    "ADJACENT_SPORT_MARKET",
    "MISSING_STAGE_PROOF_REMAINS_HELD",
    "FAILED_OR_STALE_STAGE_PROOF_REMAINS_HELD",
    "DUPLICATE_CANONICAL_SOURCE_EXACT_ONCE",
    "FULL_COMPACT_ACTION_PARITY",
    "SAME_RUN_PERSISTENCE_RECONCILIATION",
    "TERMINAL_PROBABILITY_INVARIANTS",
)


@dataclass(frozen=True)
class SpecialistDecision:
    agent: str
    state: str
    incident_id: str
    accountable_owner: str
    next_owner: str
    reasons: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    required_checks: tuple[str, ...] = ()
    can_execute: bool = False
    terminal_authority: str = TERMINAL_AUTHORITY
    activation_status: str = SOURCE_STATUS


def _valid_ref(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= 1024


def _dec(agent: str, state: str, incident: Mapping[str, Any], next_owner: str,
         reasons: Sequence[str] = (), refs: Sequence[str] = (),
         checks: Sequence[str] = ()) -> SpecialistDecision:
    if agent not in AGENT_TO_EXISTING_ROLE:
        raise ValueError("UNREGISTERED_AMRT_SPECIALIST")
    if state not in ("READY", "ROUTED", "HELD", "PR_REVIEW", "REENTRY_REQUIRED"):
        raise ValueError("INVALID_SPECIALIST_DISPOSITION")
    return SpecialistDecision(
        agent=agent, state=state,
        incident_id=str(incident.get("incident_id") or "").strip(),
        accountable_owner=str(incident.get("owner_id") or "").strip(),
        next_owner=next_owner, reasons=tuple(reasons),
        evidence_refs=tuple(refs), required_checks=tuple(checks),
    )


def to_existing_role_receipt(decision: SpecialistDecision) -> RoleReceipt:
    """Preserve seven-role receipt ledger; never spoof independent QA/release."""
    if (decision.agent not in AGENT_TO_EXISTING_ROLE
            or decision.can_execute is not False
            or decision.terminal_authority != TERMINAL_AUTHORITY
            or decision.state == "FIXED_AND_VERIFIED"
            or not decision.incident_id):
        raise ValueError("INVALID_AMRT_SPECIALIST_HANDOFF")
    canonical = AGENT_TO_EXISTING_ROLE[decision.agent]
    if canonical not in TEAM:
        raise ValueError("CANONICAL_ROLE_MISSING")
    return RoleReceipt(
        role=canonical, state=decision.state, incident_id=decision.incident_id,
        next_owner=decision.next_owner, reasons=decision.reasons,
        evidence_refs=("specialist:" + decision.agent,) + decision.evidence_refs,
    )


class ModelRecoveryCommander:
    """Hold one canonical owner through QA, release and external closure."""

    NAME = "MODEL_RECOVERY_COMMANDER"

    def run(self, incident: Mapping[str, Any]) -> SpecialistDecision:
        if not _valid_ref(incident.get("incident_id")) or not _valid_ref(incident.get("issue_ref")):
            return _dec(self.NAME, "HELD", incident, "ECOSYSTEM_CONDUCTOR",
                        ("CANONICAL_INCIDENT_EVIDENCE_MISSING",))
        if not _valid_ref(incident.get("owner_id")):
            return _dec(self.NAME, "HELD", incident, "ECOSYSTEM_CONDUCTOR",
                        ("ACCOUNTABLE_OWNER_MISSING",), (str(incident["issue_ref"]),))
        if (incident.get("lease_owner") is not None
                and incident["lease_owner"] != incident["owner_id"]):
            return _dec(self.NAME, "HELD", incident, "ECOSYSTEM_CONDUCTOR",
                        ("OWNER_LEASE_CONFLICT",), (str(incident["issue_ref"]),))
        if incident.get("closed") is True and not _valid_ref(incident.get("independent_closure_ref")):
            return _dec(self.NAME, "HELD", incident, "ECOSYSTEM_CONDUCTOR",
                        ("CLOSED_WITHOUT_INDEPENDENT_PRODUCTION_PROOF",),
                        (str(incident["issue_ref"]),))
        stage = str(incident.get("stage") or "INVESTIGATION").upper()
        owner = {
            "INVESTIGATION": "ROOT_CAUSE_INVESTIGATOR",
            "REPAIR": "MODEL_RECOVERY_ENGINEER",
            "REGRESSION": "CODE_CI_ENGINEER",
            "REVIEW": "INDEPENDENT_QA",
            "QA": "INDEPENDENT_QA",
            "RELEASE": "RELEASE_VERIFICATION",
            "PRODUCTION_VERIFY": "RELEASE_VERIFICATION",
            "CLOSURE": "RELEASE_VERIFICATION",
        }.get(stage)
        if owner is None:
            return _dec(self.NAME, "HELD", incident, "ECOSYSTEM_CONDUCTOR",
                        ("UNKNOWN_RECOVERY_STAGE",), (str(incident["issue_ref"]),))
        if stage in ("RELEASE", "PRODUCTION_VERIFY", "CLOSURE"):
            if not (_valid_ref(incident.get("independent_qa_ref"))
                    and _valid_ref(incident.get("release_authority_ref"))):
                return _dec(self.NAME, "HELD", incident, "RELEASE_VERIFICATION",
                            ("INDEPENDENT_RELEASE_EVIDENCE_PENDING",),
                            (str(incident["issue_ref"]),))
            # Never announce closure from self-supplied flags. Existing external
            # independent verifier performs closure_gate + durable receipt.
            return _dec(self.NAME, "ROUTED", incident, "RELEASE_VERIFICATION",
                        ("INDEPENDENT_PRODUCTION_CLOSURE_REQUIRED",),
                        (str(incident["independent_qa_ref"]),
                         str(incident["release_authority_ref"])))
        return _dec(self.NAME, "ROUTED", incident, owner,
                    refs=(str(incident["issue_ref"]),))


class ModelDependencyInvestigator:
    """Walk ordered producer evidence without promoting absent proof to PASS."""

    NAME = "MODEL_DEPENDENCY_INVESTIGATOR"

    def run(self, incident: Mapping[str, Any],
            stages: Mapping[str, Mapping[str, Any]]) -> SpecialistDecision:
        if not _valid_ref(incident.get("incident_id")) or not _valid_ref(incident.get("reproduction_ref")):
            return _dec(self.NAME, "HELD", incident, "ROOT_CAUSE_INVESTIGATOR",
                        ("REPRODUCTION_EVIDENCE_MISSING",))
        if not all(_valid_ref(incident.get(k)) for k in ("sport", "market", "event_id", "model_id")):
            return _dec(self.NAME, "HELD", incident, "ROOT_CAUSE_INVESTIGATOR",
                        ("MODEL_EVENT_MARKET_IDENTITY_MISSING",))
        for stage in STAGES:
            record = stages.get(stage)
            if not isinstance(record, Mapping):
                return _dec(self.NAME, "HELD", incident, "ROOT_CAUSE_INVESTIGATOR",
                            ("DEPENDENCY_STAGE_UNOBSERVED:" + stage,),
                            (str(incident["reproduction_ref"]),))
            status = str(record.get("status") or "").upper()
            if status not in KNOWN_STATES or not _valid_ref(record.get("evidence_ref")):
                return _dec(self.NAME, "HELD", incident, "ROOT_CAUSE_INVESTIGATOR",
                            ("DEPENDENCY_EVIDENCE_INVALID:" + stage,),
                            (str(incident["reproduction_ref"]),))
            if status != PASS:
                owner = ("DATA_INTEGRATION_ENGINEER" if stage in STAGES[:4] or
                         stage == "persistence_reconciliation" else
                         "RELEASE_VERIFICATION" if stage == "action_readback" else
                         "MODEL_RECOVERY_ENGINEER")
                return _dec(self.NAME, "ROUTED", incident, owner,
                            ("FIRST_UNPROVEN_DEPENDENCY:" + stage + ":" + status,),
                            (str(incident["reproduction_ref"]), str(record["evidence_ref"])),
                            ("TRACE_PRODUCER_ROOT_CAUSE", "PRESERVE_TYPED_HELD_STATE"))
        return _dec(self.NAME, "HELD", incident, "ROOT_CAUSE_INVESTIGATOR",
                    ("REPRODUCED_FAILURE_NOT_EXPLAINED_BY_PROVIDED_STAGES",),
                    (str(incident["reproduction_ref"]),))


class ModelRestorationEngineer:
    """Generate a bounded implementation request, not a model or source mutation."""

    NAME = "MODEL_RESTORATION_ENGINEER"

    def run(self, incident: Mapping[str, Any]) -> SpecialistDecision:
        if not all(_valid_ref(incident.get(k)) for k in
                   ("incident_id", "reproduction_ref", "root_cause_ref", "acceptance_ref")):
            return _dec(self.NAME, "HELD", incident, "ROOT_CAUSE_INVESTIGATOR",
                        ("ROOT_CAUSE_OR_ACCEPTANCE_EVIDENCE_MISSING",))
        change_class = str(incident.get("change_class") or "").upper()
        changes = incident.get("changed_contracts")
        if (not isinstance(changes, (tuple, list))
                or not all(isinstance(c, str) for c in changes)):
            return _dec(self.NAME, "HELD", incident, "ROOT_CAUSE_INVESTIGATOR",
                        ("CHANGE_SCOPE_UNDECLARED",))
        if change_class == "C" or PROTECTED_MODEL_CHANGES.intersection(changes):
            return _dec(self.NAME, "HELD", incident, "MODEL_EXCELLENCE",
                        ("CLASS_C_MODEL_CERTIFICATION_REQUIRED",),
                        (str(incident["root_cause_ref"]),))
        if change_class not in ("A", "B"):
            return _dec(self.NAME, "HELD", incident, "ROOT_CAUSE_INVESTIGATOR",
                        ("GOVERNED_CHANGE_CLASS_MISSING",))
        if incident.get("source_pr_open") is True:
            return _dec(self.NAME, "PR_REVIEW", incident, "CODE_CI_ENGINEER",
                        ("EXISTING_SOURCE_PR_REUSE_NO_DUPLICATE_WRITER",),
                        (str(incident["acceptance_ref"]),))
        return _dec(self.NAME, "ROUTED", incident, "CODE_CI_ENGINEER",
                    ("AUTHORIZED_WORKER_AND_ATOMIC_LEASE_REQUIRED",),
                    (str(incident["root_cause_ref"]), str(incident["acceptance_ref"])),
                    ("FOCUSED_REGRESSION", "ADJACENT_CROSS_SPORT_REGRESSION",
                     "CLASS_B_INDEPENDENT_REVIEW" if change_class == "B"
                     else "INDEPENDENT_REVIEW_AS_APPLICABLE"))


class ModelRecoveryRegressionSpecialist:
    """Specify replay matrix and independently verifiable exact-head evidence."""

    NAME = "MODEL_RECOVERY_REGRESSION_SPECIALIST"

    def run(self, incident: Mapping[str, Any],
            observed: Mapping[str, Mapping[str, Any]] | None = None) -> SpecialistDecision:
        if not all(_valid_ref(incident.get(k)) for k in
                   ("incident_id", "reproduction_ref", "acceptance_ref")):
            return _dec(self.NAME, "HELD", incident, "ROOT_CAUSE_INVESTIGATOR",
                        ("REPLAY_CONTRACT_MISSING",))
        if observed is None:
            return _dec(self.NAME, "ROUTED", incident, "CODE_CI_ENGINEER",
                        ("TEST_MATRIX_NOT_YET_EXECUTED",),
                        (str(incident["reproduction_ref"]),), REPLAY_CASES)
        sha = incident.get("head_sha")
        if not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha.lower()):
            return _dec(self.NAME, "HELD", incident, "CODE_CI_ENGINEER",
                        ("EXACT_HEAD_SHA_REQUIRED",))
        for case in REPLAY_CASES:
            record = observed.get(case)
            if not isinstance(record, Mapping) or record.get("pass") is not True:
                return _dec(self.NAME, "HELD", incident, "CODE_CI_ENGINEER",
                            ("REPLAY_NOT_PROVEN:" + case,), checks=REPLAY_CASES)
            if record.get("sha") != sha or not _valid_ref(record.get("evidence_ref")):
                return _dec(self.NAME, "HELD", incident, "CODE_CI_ENGINEER",
                            ("REPLAY_SHA_OR_RECEIPT_MISMATCH:" + case,),
                            checks=REPLAY_CASES)
        # Author-generated tests can advance to independent QA, never act as QA.
        return _dec(self.NAME, "PR_REVIEW", incident, "INDEPENDENT_QA",
                    ("INDEPENDENT_QA_MUST_REPLAY_AND_SIGN_EXACT_HEAD",),
                    (str(incident["acceptance_ref"]),), REPLAY_CASES)


class RecoveryEscalationDeadlockBreaker:
    """Detect repair stalls; propose only bounded, lease-safe recovery routes."""

    NAME = "RECOVERY_ESCALATION_DEADLOCK_BREAKER"

    def run(self, incident: Mapping[str, Any], *, now: datetime) -> SpecialistDecision:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("NOW_MUST_BE_OFFSET_AWARE")
        if not _valid_ref(incident.get("incident_id")) or not _valid_ref(incident.get("owner_id")):
            return _dec(self.NAME, "HELD", incident, "ECOSYSTEM_CONDUCTOR",
                        ("CANONICAL_OWNER_UNRESOLVED",))
        # Never recommend code repair/fallback while a second, or unknown,
        # implementation writer may own the fenced incident lease.
        if (not _valid_ref(incident.get("lease_owner"))
                or incident["lease_owner"] != incident["owner_id"]):
            return _dec(self.NAME, "HELD", incident, "ECOSYSTEM_CONDUCTOR",
                        ("LEASE_OWNER_IDENTITY_UNVERIFIED",))
        if incident.get("source_pr_open") is True:
            if incident.get("review_rejected") is True or incident.get("ci_failed") is True:
                return _dec(self.NAME, "PR_REVIEW", incident, "CODE_CI_ENGINEER",
                            ("EXISTING_PR_REQUIRES_EXACT_HEAD_REPAIR",))
            return _dec(self.NAME, "PR_REVIEW", incident, "INDEPENDENT_QA",
                        ("EXISTING_PR_REVIEW_OR_RELEASE_PENDING",))
        lease = lease_reentry(incident, now=now)
        if lease.status != "OWNED":
            return _dec(self.NAME,
                        "REENTRY_REQUIRED" if lease.status == "REENTRY_REQUIRED" else "HELD",
                        incident, "ECOSYSTEM_CONDUCTOR", lease.blockers)
        if incident.get("lease_authority") != "EXISTING_AUTHENTICATED_ENGINEERING_WORKER":
            return _dec(self.NAME, "HELD", incident, "ECOSYSTEM_CONDUCTOR",
                        ("WRITER_AUTHORITY_NOT_VERIFIED",))
        raw = incident.get("last_progress_at")
        try:
            progress = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return _dec(self.NAME, "HELD", incident, "CODE_CI_ENGINEER",
                        ("PROGRESS_EVIDENCE_MISSING",))
        if progress.tzinfo is None or progress.utcoffset() is None or progress > now:
            return _dec(self.NAME, "HELD", incident, "CODE_CI_ENGINEER",
                        ("PROGRESS_TIMESTAMP_INVALID",))
        try:
            attempts = incident["provider_attempts"]
        except KeyError:
            return _dec(self.NAME, "HELD", incident, "CODE_CI_ENGINEER",
                        ("PROVIDER_ATTEMPT_EVIDENCE_MISSING",))
        if isinstance(attempts, bool) or not isinstance(attempts, int):
            return _dec(self.NAME, "HELD", incident, "CODE_CI_ENGINEER",
                        ("PROVIDER_ATTEMPT_EVIDENCE_INVALID",))
        stalled = (now - progress).total_seconds() >= 1800
        provider = provider_attempt(attempts, stalled,
                                    incident.get("authorized_fallback") is True)
        if not stalled:
            return _dec(self.NAME, "READY", incident, "CODE_CI_ENGINEER",
                        ("ACTIVE_LEASE_CONTINUE_EXISTING_WORK",))
        if provider.status == "FALLBACK_CANDIDATE":
            return _dec(self.NAME, "ROUTED", incident, "CODE_CI_ENGINEER",
                        ("AUTHORIZED_PROVIDER_FALLBACK_SAME_LEASE_ONLY",),
                        checks=("VERIFY_BOUND_WRITER_IDENTITY", "HONOR_ATTEMPT_CAP"))
        return _dec(self.NAME, "HELD", incident, provider.next_owner, provider.blockers)
