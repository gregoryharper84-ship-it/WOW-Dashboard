---
name: wow-debug-dissolve-deploy
description: Use for scoped WOW V17 engineering incident repair; reproduce the failure, remove the root cause, test the smallest diff, and prepare independent production verification.
---

# WOW Debug. Dissolve. Deploy.

This is a reusable Claude Code execution procedure, not a new engineering dispatcher, permission grant, model specialist, or release authority.

## Authority

- Read the repository root CLAUDE.md and the applicable .agents/skills/wow-engineering-* contracts before modifying a file.
- Inherit the active WOW V17 governance; do not reinterpret or override higher-priority requirements.
- can_execute=false; no live wager, order, or trading action.
- V17_TERMINAL_REDUCER retains sole global probability publication authority.
- Never manufacture model probabilities, calibration, lower bounds, market evidence, or source receipts.
- Do not change model coefficients, routing, calibration, or publication behavior as a routine engineering repair. Such proposals follow the governed challenger and validation path.

## DEBUG

1. Resolve the exact GitHub incident, existing PR, expected head SHA, current owner and implementation lease.
2. If another writer owns the incident, stop and return OWNERSHIP_CONFLICT without making changes.
3. Inspect failing CI logs, affected source, and reproducible tests; distinguish underlying defect from credential, quota, data, and environment failures.
4. Write down the first failing operation, actual typed error, root-cause evidence, and minimal acceptance criteria.

## DISSOLVE

5. Work only on the authorized existing PR branch when one exists; otherwise request the existing Conductor's branch assignment.
6. Make the smallest targeted source/test diff. Preserve successful behavior and avoid broad refactoring.
7. Add a regression test that fails before the fix where practical, plus a positive counterexample.
8. Keep secrets out of code and logs. Never bypass protected GitHub rules, independent principal requirements, or exact-SHA fences.
9. If an external model provider is out of credit/quota, preserve the typed failure and deterministic fallback; do not retry it repeatedly or disguise failure as completion.

## DEPLOY

10. Run only locally authorized deterministic tests and record their commands and results. Never claim live verification from local tests.
11. Produce an exact-head PR/patch handoff with changed paths, test results, known blockers, and rollback plan.
12. Do not merge or deploy merely because the patch is ready. Existing QA/Release and SIRT must independently verify real production behavior.
13. Final incident status must reflect proof: PR_CREATED, EXPERIMENT_CREATED, FIXED_AND_VERIFIED, BLOCKED_WITH_EXACT_REASON, or other existing canonical status.

## Completion contract

Report incident and branch; parent and head SHA; root cause; changed files; reproducible acceptance; tests and environment; unresolved blockers; independent verification needed; can_execute=false.

Do not create a competing scheduler, release controller, monitoring team, or parallel writer.
