# P0 #2054 — Authenticated QA/Release workflow_run target routing reproducer

Status: **UNFIXED. Cursor Cloud Agent worktree handoff.** This document holds immutable evidence and acceptance criteria; it is not implementation or release approval.

## Exact observed regression
- Protected `main` at assignment: `3f9a7ab18098e6a00c742249b4ab81efe06e0bd6`.
- Governed web and worker both deployed/live at this SHA, **not proof QA/release CI is green**.
- GitHub Actions `wow-v17-independent-qa-check` run `40253927556`, job `121351592403`, triggered by `workflow_run` on a push-era source with no unique open exact-head PR, failed `QA_TARGET_COUNT_INVALID` at `2026-10-26T01:23:01Z`.
- Consequent `wow-v17-release-authority-check` run `40253949430`, job `121351659002`, failed `QA_POLICY_ARTIFACT_DUPLICATE_OR_MISSING`: upstream QA had no policy artifact because it never resolved an applicable PR.
- Independent QA failures continued at runs `40255235632` and `40255222199` on the merged head; many AUTO QA workflows queued/pending at approximately 01:42Z. A large AUTO queue is evidence of misrouting/backpressure, **not authority to disable checks**.
- Source workflow `.github/workflows/wow-v17-independent-qa-check.yml` job-level `if` only checks that `workflow_run.pull_requests[0]` exists. Target resolution later requires exactly one open PR at the expected head. This disagreement is the first concrete trust-boundary failure.
- Release policy `.github/workflows/wow-v17-release-authority-check.yml` requires a verified QA policy artifact even when the upstream QA event had no applicable open PR target.

## Minimal permitted repair

1. Capture original producer run ID, event, workflow path/ID, actor/app, head repository/SHA, and full attached PR inventory. Reproduce genuinely inapplicable merged/push cases without treating them as active PRs.
2. A trusted completed upstream run with *provably no unique current open exact-head PR* should result in a typed **NEUTRAL, no QA App check and no release artifact requirement**. No forged success.
3. A genuine current open PR must still receive proper exact-head QA/Release. Fail closed for unknown/foreign repo or SHA, malformed/missing identity where applicability cannot be proven, multiple matches, and incomplete evidence.
4. The downstream release evaluator must neutralize only an authenticated nonapplicable/neutral upstream QA; any applicable QA without authentic unique policy artifact remains RED. Retain independent principal, receipt and SHA validation.
5. Preserve governed concurrency/backpressure and avoid storms for no-target push events. No cancellation of legitimate review events or broad bypass.
6. Add focused positive/negative tests and adjacent regression for the exact reproducer, merged PR, closed/stale ref, 0/2 PRs, foreign app/head/repo, valid current PR, applicable missing/duplicate QA artifact, and queuing/idempotence.
7. Same-PR branch changes, 3 native regression checks, independent CODEOWNER/QA/Release review, exact-head merge, and **post-deploy independent production verification** required.

## Ownership and restrictions
Cursor is requested only as bounded engineering implementer. The original owner-created bootstrap commit is **not independent trusted-source authorization**; CODEOWNER, independent QA/Release, and if needed the governed Engineering App source-adoption path remain obligatory. Preserve `custom_gpt_identity=WOW_BETTING_ENGINE`, `runtime_generation=V17_ACTIVE`, `terminal_authority=V17_TERMINAL_REDUCER`, `can_execute=false`, `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`. No credential, workflow ruleset, quota, wager or probability changes.
