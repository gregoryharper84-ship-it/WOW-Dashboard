# WOW SIRT — Independent Assurance Implementation

## Ownership
The Conductor coordinates ecosystem handoffs; Engineering implements fixes; Independent Verification accepts closures; SIRT diagnoses, prevents, and audits. No alternate terminal publication authority is created.

## A. Independent reliability sentinel
The sole 15-minute scheduler is the Render cron `wow-sirt-external-sentinel` declared in `render.yaml` (`schedule: "*/15 * * * *"`, `startCommand: python -m v17.sirt_external_sentinel`, `autoDeployTrigger: "off"`), which runs outside GitHub and persists a liveness heartbeat. `.github/workflows/wow-sirt-independent-reliability-sentinel.yml` is **manual dispatch only**: it declares no `schedule` trigger and never substitutes for the cron, because GitHub's scheduler cannot be relied on to fire a 15-minute sentinel. A manual run requests the **existing** service-role-protected `wow_engineering_auditor_runtime` record; a missing credential, missing record, unreachable database, stale heartbeat, stopped worker-auditor, or governance mismatch fails closed.

**Deployment requirement:** the Render cron carries `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` as unsynced environment variables, and GitHub Actions needs `SUPABASE_URL` plus `SUPABASE_SERVICE_ROLE_KEY` (or `SUPABASE_SERVICE_KEY`) in the repository environment for manual live polling. Never copy credential values into source or logs. A failed cron cycle is not a verified production success, and a green manual run does not prove the cron is promoted or firing. Review the cron's persisted heartbeat and, for manual runs, Actions status and its retained JSON receipt.

The sentinel also independently checks GitHub Actions worker activity whenever
Supabase has unresolved P0/P1 engineering work. Missing, stale (>2 hours), or
latest failed worker activity yields a typed finding. It uses only the
read-only GitHub Actions permission and the job-scoped GITHUB_TOKEN. This
**does not establish that the Render resident dispatcher is enabled**:
resident-dispatch heartbeat, queue progress, and two-cycle failure/restart
acceptance remain separate Class B release requirements on #1525.

The resident engineering auditor continues checking lifecycle deadlines and GitHub code health. The independent sentinel provides **watchdog-of-watchdog** coverage, not an additional duplicate resident daemon. Neither a healthy heartbeat nor a passing code-health job proves that LLP, props, or Kalshi models are operational.

`assess_sentinel` also supports typed P0/P1 overdue-work findings, unacknowledged handoffs, and stale or false-green production probes. These optional inputs must be fed from authoritative persisted sources before claiming live coverage for them. Missing evidence cannot become PASS.

## B. Class A lifecycle closure auditor
`assess_class_a_closure` is an independent proof evaluator. It refuses closure eligibility without diagnosis, tests, review, exact-head and base certification, merge provenance, deployed revision or verified ancestry, production acceptance, and independent verification receipts.

An evidence-complete receipt yields `EVIDENCE_COMPLETE_PENDING_TERMINAL_AUTHORITY`; it never self-sets `FIXED_AND_VERIFIED`, certifies a specialist, or bypasses the existing terminal reducer. Product readiness is independently proven outside this module.

## C. Systemic failure intelligence
`failure_families` consumes canonical postmortem and incident records, emitting deterministic recurrence fingerprints and preventive review opportunities. **Only confirmed and explicitly shared root-cause identifiers are grouped.** Title similarity and speculation do not count.

Each workflow run retains a 30-day report artifact. Canonical postmortems and existing Supabase audit findings remain the durable sources of truth; no competing incident ledger or Conductor database is introduced.

To make a recurring family actionable, engineering triage should record a stable `confirmed_root_cause_id` or `root_cause_code` and link corrective PRs, prevention regression tests, and production acceptance evidence through the existing lifecycle.

## Verification and release checklist
1. `python -m pytest -q artifacts/wow-engine/test_v17_sirt_assurance.py`
2. Verify the existing engineering code-health workflow passes on the exact PR head.
3. Review independent SIRT authority/no probability or execution change.
4. Merge only after required reviews and governance.
5. Confirm the Render cron watchdog is promoted with its configured secrets, runs successfully on its 15-minute schedule, **and** fails closed on a missing/stale heartbeat or critical-backlog worker inactivity.
6. Confirm artifact retention and incident-family output.
7. Verify any claimed product recovery separately using actual end-to-end LLP/prop user journeys.

**Release state:** Code and a PR are not deployment. The Render cron is declared with `autoDeployTrigger: "off"`, so creating and promoting it is a separately authorized Release Authority action rather than a merge side effect; merging schedules no GitHub workflow, because the sentinel workflow is manual dispatch only. Do not claim operation until a real cron heartbeat is observed.

**Global invariants:** `can_execute=false`, `terminal_authority=V17_TERMINAL_REDUCER`, independent specialist probability ownership.
