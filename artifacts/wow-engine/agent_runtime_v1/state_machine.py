from .contracts import RunStatus, JobStatus

RUN_TRANSITIONS={
 RunStatus.CREATED:{RunStatus.VALIDATING_REQUEST,RunStatus.CANCELED},
 RunStatus.VALIDATING_REQUEST:{RunStatus.DISCOVERY_QUEUED,RunStatus.ROUTING,RunStatus.FAILED,RunStatus.CANCELED},
 RunStatus.DISCOVERY_QUEUED:{RunStatus.DISCOVERY_RUNNING,RunStatus.FAILED,RunStatus.CANCELED},
 RunStatus.DISCOVERY_RUNNING:{RunStatus.ROUTING,RunStatus.FAILED,RunStatus.CANCELED},
 RunStatus.ROUTING:{RunStatus.EVIDENCE_QUEUED,RunStatus.RECONCILING,RunStatus.FAILED,RunStatus.CANCELED},
 RunStatus.EVIDENCE_QUEUED:{RunStatus.EVIDENCE_RUNNING,RunStatus.FAILED,RunStatus.CANCELED},
 RunStatus.EVIDENCE_RUNNING:{RunStatus.MODELING_QUEUED,RunStatus.RECONCILING,RunStatus.FAILED,RunStatus.CANCELED},
 RunStatus.MODELING_QUEUED:{RunStatus.MODELING_RUNNING,RunStatus.FAILED,RunStatus.CANCELED},
 RunStatus.MODELING_RUNNING:{RunStatus.AUDIT_QUEUED,RunStatus.RECONCILING,RunStatus.FAILED,RunStatus.CANCELED},
 RunStatus.AUDIT_QUEUED:{RunStatus.AUDIT_RUNNING,RunStatus.FAILED,RunStatus.CANCELED},
 RunStatus.AUDIT_RUNNING:{RunStatus.FINAL_REFRESH,RunStatus.RECONCILING,RunStatus.FAILED,RunStatus.CANCELED},
 RunStatus.FINAL_REFRESH:{RunStatus.RECONCILING,RunStatus.FAILED,RunStatus.CANCELED},
 RunStatus.RECONCILING:{RunStatus.COMPLETED,RunStatus.COMPLETED_WITH_BLOCKERS,RunStatus.FAILED},
}
JOB_TRANSITIONS={
 JobStatus.QUEUED:{JobStatus.RUNNING,JobStatus.CANCELED},
 JobStatus.RUNNING:{JobStatus.SUCCEEDED,JobStatus.BLOCKED,JobStatus.REJECTED,JobStatus.TIMED_OUT,JobStatus.RETRY_PENDING,JobStatus.CANCELED},
 JobStatus.RETRY_PENDING:{JobStatus.QUEUED,JobStatus.DEAD_LETTERED,JobStatus.CANCELED},
}

def assert_run_transition(current: RunStatus, nxt: RunStatus)->None:
    if nxt not in RUN_TRANSITIONS.get(current,set()):
        raise ValueError(f"ILLEGAL_RUN_TRANSITION:{current}->{nxt}")

def assert_job_transition(current: JobStatus, nxt: JobStatus)->None:
    if nxt not in JOB_TRANSITIONS.get(current,set()):
        raise ValueError(f"ILLEGAL_JOB_TRANSITION:{current}->{nxt}")
