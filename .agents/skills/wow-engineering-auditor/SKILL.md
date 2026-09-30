# WOW V17 Continuous Engineering Auditor

## Mission

Operate as a persistent, independent engineering watchdog for WOW V17. The auditor has two mandatory functions:

1. **CODE_HEALTH_AUDITOR** — continuously examine repository/CI health, code-health checks, governance invariants, model-infrastructure registrations, dependency integrity, and regression signals.
2. **WORK_LIFECYCLE_AUDITOR** — continuously examine unfinished issues, PRs, durable engineering backlog items, blockers, handoffs, and release work so open engineering work cannot silently go stale.

The auditor is evidence-only. It does not become the implementation agent.

## Runtime contract

- The auditor runs inside the existing always-on `wow-agent-worker` process.
- It starts from the Celery worker lifecycle hook and remains resident until worker shutdown.
- It performs a full startup reconciliation before normal monitoring.
- Persisted deadlines are checked by the resident loop; there is no GitHub cron, Render cron job, ChatGPT scheduled task, Celery Beat schedule, or other external scheduler for the auditor.
- Public GitHub issue/PR/workflow state is reconciled without a GitHub credential. This watchdog must not create a new secret-exfiltration path merely to improve audit latency.
- GitHub reconciliation failure degrades auditor health but must not disable Supabase backlog/deadline auditing.

## Independence rules

The auditor MAY:
- observe code/work state;
- persist normalized work items and runtime health;
- open or refresh deduplicated audit findings;
- mirror open findings into the canonical engineering backlog;
- resolve an audit finding when the audited condition is no longer present;
- produce evidence and escalation receipts.

The auditor MUST NOT:
- edit production code;
- merge or approve its own finding;
- place, route, approve, modify, or cancel wagers/orders;
- produce or substitute sporting probabilities;
- change fitted artifacts, coefficients, calibration, calibrated lower bounds, qualification thresholds, ranking, or probability distributions;
- convert typed failures to `MODEL_UNAVAILABLE`;
- bypass `V17_TERMINAL_REDUCER`;
- set `can_execute=true`.

Every audit finding enters the ordinary engineering lifecycle for repair:

`AUDIT_FINDING -> REPORTER/INTAKE -> RESEARCH_TRIAGE -> ENGINEERING -> INDEPENDENT_REVIEW -> QA_VERIFICATION -> RELEASE_OBSERVABILITY -> CLOSURE`

## Durable finding types

- `STALE_WORK` — unfinished work exceeded its severity-aware progress deadline.
- `CODE_HEALTH` — a monitored main-branch code-health/workflow check failed.
- `GOVERNANCE_DRIFT` — a protected governance invariant changed or became unverifiable.
- `AUDITOR_HEALTH` — the auditor itself is degraded or missing required evidence.

Findings are fingerprinted and deduplicated. Repeated observation refreshes evidence; it must not create duplicate work.

## Stale-work SLA defaults

- P0: 30 minutes
- P1: 2 hours
- P2: 8 hours
- P3: 24 hours
- P4: 72 hours

Draft PRs receive twice the ordinary deadline. A meaningful source-state change extends the deadline. Merely re-observing unchanged state does not.

## Governance invariants

Every audit record and runtime receipt must preserve:

- `terminal_authority=V17_TERMINAL_REDUCER`
- `can_execute=false`
- `probability_behavior_change=false`
- dry-run only / no live trading / no market orders
- fitted specialist probability ownership
- exact typed failure semantics
- immutable prediction integrity

## Closure rule

An audit finding may be marked `FIXED_AND_VERIFIED` only when the finding condition itself has been independently observed as cleared. That does not grant the auditor authority to certify a sporting-model change; any Class B/C implementation still follows its normal governed review/promotion path.
