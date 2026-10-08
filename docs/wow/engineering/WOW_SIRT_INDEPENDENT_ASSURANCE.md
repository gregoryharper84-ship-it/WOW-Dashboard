# WOW SIRT — Independent Assurance Implementation

## Ownership
The Conductor coordinates ecosystem handoffs; Engineering implements fixes; Independent Verification accepts closures; SIRT diagnoses, prevents, and audits. No alternate terminal publication authority is created.

## A. Independent reliability sentinel
`.github/workflows/wow-sirt-independent-reliability-sentinel.yml` runs independently of the resident `wow-agent-worker` every 15 minutes and on manual dispatch. It requests the **existing** service-role-protected `wow_engineering_auditor_runtime` record; a missing credential, missing record, unreachable database, stale heartbeat, stopped worker-auditor, or governance mismatch fails closed.

**Deployment requirement:** GitHub Actions secrets `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (or `SUPABASE_SERVICE_KEY`) must be present in the repository environment for live polling. Never copy credential values into source or logs. A failed scheduled run is not a verified production success. Review Actions status and its retained JSON receipt.

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

The scheduled workflow retains a 30-day report artifact. Canonical postmortems and existing Supabase audit findings remain the durable sources of truth; no competing incident ledger or Conductor database is introduced.

To make a recurring family actionable, engineering triage should record a stable `confirmed_root_cause_id` or `root_cause_code` and link corrective PRs, prevention regression tests, and production acceptance evidence through the existing lifecycle.

## Verification and release checklist
1. `python -m pytest -q artifacts/wow-engine/test_v17_sirt_assurance.py`
2. Verify the existing engineering code-health workflow passes on the exact PR head.
3. Review independent SIRT authority/no probability or execution change.
4. Merge only after required reviews and governance.
5. Confirm the scheduled watchdog runs successfully with configured secrets **and** fails closed on a missing/stale heartbeat or critical-backlog worker inactivity.
6. Confirm artifact retention and incident-family output.
7. Verify any claimed product recovery separately using actual end-to-end LLP/prop user journeys.

**Release state:** Code and a PR are not deployment. A worker with `autoDeploy=no` requires an explicit authorized deployment; this new GitHub Actions watchdog becomes scheduled only after merge. Do not claim operation until a real run receipt is observed.

**Global invariants:** `can_execute=false`, `terminal_authority=V17_TERMINAL_REDUCER`, independent specialist probability ownership.
