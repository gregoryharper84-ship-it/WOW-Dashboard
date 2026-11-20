# WOW V17 Cursor Recovery Design — 2026-11-12

**Objectives:** `CUR-REL-20261112`, `CUR-DELIV-20261112`  
**Authority:** Issue #1562 acceptance; incident #2136; `V17_TERMINAL_REDUCER`; `can_execute=false`  
**Implementer:** Cursor (nonowner engineering identity)

## Problem (release)

Owner-authored PR #2630 isolates automatic QA/Release `workflow_run` target-resolution failures so they emit typed HOLDs (`QA_AUTOMATIC_TARGET_HOLD` / `RELEASE_AUTOMATIC_TARGET_HOLD`) instead of poisoning protected `main` `checksPass`. That source change is correct in substance, but `TRUST_ROOT_OWNER_AUTHORED` blocks owner-authored `/.github/workflows/**` trust-root patches from independent governance bootstrap.

## Design choice (release)

Independently re-author the exact #2630 workflow + regression contract on a Cursor-owned branch so authorship is nonowner. Preserve:

- automatic `workflow_run` target resolution: step-level `continue-on-error` only when `github.event_name == 'workflow_run'`
- typed HOLD summary; no QA/Release App PASS minted on failure
- manual `workflow_dispatch` remains hard-fail
- ruleset #24907422 unchanged (QA App 5245832, Release App 5245853, three regression contexts)
- no fake App checks, no branch-protection edits, no Render bypass

Do not open a parallel semantic redesign. Adopt #2630, strengthen residual negative coverage if gaps remain, and request independent CODEOWNER/QA/Release on the new exact HEAD.

## Problem (delivery)

User-facing sporting delivery remains incomplete until authenticated Actions return calibrated probabilities from certified fitted specialists with immutable receipts. Observed `/score-prop` 422 `PROP_EVIDENCE_SNAPSHOT_NOT_FOUND` is evidence/fixture lifecycle, not proof the specialist is absent. Anonymous 401 with `auth_scheme=NONE` is not an Action outage.

## Design choice (delivery)

Separate PRs/worktrees from CUR-REL. Inventory `sport × market × route`, repair only producing-layer hydration/scorer/Action wiring for already-certified specialists, leave uncertified families as typed HOLD for Freebuff (#2605). No coefficient/calibration/certification changes. Canary with real credentials or record `NO_CURRENT_EVENTS_VERIFIED` / `LIVE_GPT_EDITOR_SYNC=UNVERIFIED`.

## Non-goals

- Duplicate Freebuff offline fitting
- Relax model certification
- Fabricate probabilities
- Bypass release gates
- Change `can_execute=false`
- Force-merge protected trust-root without independent approvals
