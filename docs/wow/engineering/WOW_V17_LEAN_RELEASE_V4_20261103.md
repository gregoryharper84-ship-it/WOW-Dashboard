# WOW V17 — Lean Deployment V4 (two-gate retirement)

**Migration ID:** WOW-V17-DEPLOY-V4-20261103  
**Owner:** WOW Engineering / AMRT implementation; authorized repository owner for effective ruleset mutation and merge; SIRT independent production acceptance.  
**State:** PROPOSED / WORKING_NOT_COMPLETE until merged, rule change read back, and actual production acceptance.  
**Scope:** Repository/deployment governance only; no model-weight, betting execution, or terminal-reducer change.

## Problem verified November 3, 2026

Live GitHub branch ruleset \`24907422\`, read via its GitHub REST ruleset endpoint, was ACTIVE with seven required check contexts, strict base synchronization and no bypass actors. Existing V3 descriptive text claims a three-check baseline; this is **documented drift**, not authority to ignore live requirements.

Retire the two named, recurrently unavailable mechanisms:

- \`Trusted exact-head engineering governance\` (GitHub Actions App 15368)
- \`WOW Independent Security Review\` (QA App 5245832; paid Claude/Gemini semantic reviewer)

The rule change retains exactly these five pinned contexts:

- \`WOW required-three regression\` (GitHub Actions App 15368)
- \`WOW governed probability backend\` (GitHub Actions App 15368)
- \`WOW additional required regression\` (GitHub Actions App 15368)
- \`WOW Independent QA exact-head\` (QA App 5245832, deterministic)
- \`WOW Release Authority exact-head\` (Release App 5245853, deterministic)

Do not delete/disable the three regression checks, QA and Release App checks, PR requirement, fast-forward/deletion protection, strict base SHA recertification, or mutate bypass actors. Keep exact-head provenance and the current isolated Render writer/lease/journal.

## Desired release protocol

1. **Engineer:** AMRT/Engineering opens source PR; isolate scope and avoid mixed model/governance changes. Protect secrets. Never allow PR code into a privileged \`pull_request_target\` runner.
2. **Deterministic source CI:** Run all three GitHub-native regression contexts and security-negative/static checks relevant to the diff. A genuine failing test or unresolved security finding stops the change; no legacy review label is laundered into PASS.
3. **Authorized protected merge:** Verify the current exact head, real five pinned checks, no open blocking findings, source/merge lineage and required owner authorization. The merge actor is accountable; the QA App and Release App emit independent deterministic evidence but do **not** claim independent semantic AI/human review.
4. **Protected Render deployment:** Existing \`wow-v17-render-production-deploy.yml\` remains the ONLY production writer: source on protected main, \`wow-release\` environment, exact merge/main lineage, pinned QA/Release checks, durable service-wide journal/lease, single Render POST, no ambiguous retry.
5. **Read-only production observation:** A zero-reviewer-credit \`wow-v17-lean-production-readback\` job observes GitHub source-run identity, current main, Render LIVE deploy SHA and \`/health/live\`. It **cannot deploy** and emits \`PENDING_SIRT_ACCEPTANCE\` rather than self-certifying production.
6. **SIRT acceptance:** SIRT separately verifies real Render deployment ID/SHA for web and worker, latest critical logs, service behavior, \`/governance\`, customer-facing Action, persisted sport/market model rows, canaries and rollback readiness. Betting Intelligence separately accepts fitted scoring and calibration. \`COMPLETE\` only on independent, recorded evidence.

### Safe rollout and rollback

- **Build first:** Create the source PR and run deterministic/negative tests; the current seven-check ruleset remains effective while proposing the change. A protected trust-root migration needs independent challenge of the new safety design and an intentional owner decision, not the author's self-certification.
- **Prepare the owner-admin update:** Run \`python artifacts/wow-engine/v17/lean_release_ruleset_migration.py --output /tmp/wow-v17-lean-ruleset.json\`. The planner uses a read-only live ruleset GET by default and rejects changes from the observed seven-check source or untrusted App identities.
- **Coordinated cutover:** Once changed source and protections have been independently evaluated, the authorized repository administrator applies the exact generated payload (using authenticated GitHub administration tools). The planner has **no apply mode**. Confirm the effective five contexts with \`--verify-target\`, then merge only an otherwise valid exact-head PR and verify post-deploy. Do not treat the ruleset update itself as deployment acceptance.
- **Retirement:** Only AFTER both live five-check protection and a successful V4 deployment/readback are demonstrated, disable the legacy trusted-governance/existing-PR certifier and automatic semantic-review workflows. Removing named required checks while leaving their executors temporarily dormant is acceptable; disabling a currently required check before the coordinated cutover is not.
- **Failure/rollback:** On any missing check, foreign App, stale base/head, failed security test, provenance error, ambiguous Render POST, lost lease or failed post-deploy check: HOLD and preserve typed evidence. Restore the recorded seven-check configuration if the migration is rolled back by the authorized administrator. Never force-push or direct-deploy around the protected path.

## Implementation map — hidden legacy dependencies

This proposal must remove legacy dependencies from these locations **before claiming production migration COMPLETE**:

- \`artifacts/wow-engine/v17/independent_release_checks.py\`: legacy QA mode currently asks for the trusted-governance check in some non-zero-credit cases.
- \`artifacts/wow-engine/v17/governance_dispatcher.py\`: legacy non-zero-credit dispatch condition can require the same check.
- \`.github/workflows/wow-v17-release-production-verification-agent.yml\`: postmerge verification requires the trusted governance check, artifact, producer and checksum before recording acceptance.
- \`.github/workflows/wow-v17-simple-security-review.yml\`: automatically schedules the paid semantic reviewer; retire automatic trigger at cutover.
- \`.github/workflows/wow-v17-trusted-governance-gate.yml\`, \`.github/workflows/wow-v17-existing-pr-governance-review.yml\` and legacy tests: retire as non-controlling evidence after cutover, never rewrite historical receipts.
- \`.github/workflows/wow-v17-morning-green-continuation.yml\`: old autonomous merge handoff must not depend on a retired receipt.
- All references in release scripts, docs, CI and required-check schemas must either be migrated or explicitly labeled historical.

## Tests and independent acceptance

Migration negative cases: altered/missing/foreign required App check; unknown required check; disabled strict sync; missing PR/deletion/non-fast-forward protections; bypass actors; stale/moved SHA; missing exact-head QA or Release; unresolved real security finding; nonowner protected PR without owner's authorized review; failed Render dispatch; duplicate/ambiguous Render POST; wrong Render LIVE SHA; failed health/readback; no new pregame model rows; missing V17 Action receipts.

Record every release with ID, PR, exact source SHA, merged main SHA, three CI receipts, QA/Release App identities, Render deployment ID/revision, observed web/worker commit, canary and SIRT acceptance. No release is complete from CI or a green health endpoint alone.

**Hard locks:** \`can_execute=false\`, \`V17_TERMINAL_REDUCER\` sole model-publication authority, no wagering/trading/order actions, no fabricated fitted probabilities, no hold override, no SIRT deployment authority.
