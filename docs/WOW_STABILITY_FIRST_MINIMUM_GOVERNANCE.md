# WOW Minimum Release Governance — authoritative source contract

**Owner authorization:** [WOW governance issue #1830](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1830), whose active minimum-release policy explicitly removes independent SIRT/source-review approval as a mandatory **routine engineering** premerge gate, including protected/security-adjacent (P) engineering source. **Policy status:** owner-approved. **Technical status:** live ruleset #24907422 reconciled to three required regressions and independently read back on 2026-11-15. This document cannot override GitHub's current protections or any genuine security defect.

## Routine engineering release — one path

1. One existing, reviewed-scope pull request with immutable **exact head SHA**, complete changed-file inventory, and provenance. The repository owner is the sole required merge authorizer; zero mandatory approving-review count. An owner's intentional protected merge or otherwise authenticated exact-head owner release decision provides authority; no second human or independent SIRT signature required for routine engineering.
2. **Exactly three required GitHub Actions checks (App ID 15368), all SUCCESS at the same PR head**:
   - `WOW required-three regression`
   - `WOW governed probability backend`
   - `WOW additional required regression`
3. **Protected/security-adjacent engineering (Class P):** issue [#1830](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1830) explicitly authorizes the same owner-plus-three-check minimum without an independent premerge QA/SIRT reviewer. This is **not** a waiver of source-security assurance: retain exact diff review, narrow **negative-path tests** for secret/token/permission boundaries, stale SHA, event identity, wrong-origin evidence, and non-execution, plus a documented rollback. Real high/critical security defects or credential exposure require repair and confirmation; owner authorization does not make them vanish.
4. Authorized merge through native GitHub protection (non-fast-forward and deletion rules stay enabled; strict required status-check policy stays enabled). No unprotected push, fabricated PASS, forced check run, waived scientific model gate or change to `can_execute=false`.
5. Observe Render web and worker **LIVE at the accepted protected-main SHA**; verify health plus actual relevant governed Action request, immutable receipt, persistence and terminal reconciliation before marking product completion.

## What is NOT a mandatory engineering merge gate

`WOW Independent QA exact-head` (App 5245832), `WOW Release Authority exact-head` (App 5245853), a paid/free AI reviewer, signed SIRT semantic source review, legacy certifier, extra dispatcher lease or approval polling. These lanes may provide **diagnostic** evidence. Never call a deterministic-only QA run an independent semantic review. A substantiated security REJECT must be investigated and fixed regardless of whether the advisory lane is required.

## Permanent exclusions

A **fitted, certified sport specialist**, calibration and calibrated lower bound, fresh inputs, pregame event eligibility, immutable model/Action receipts, governed model publication and independent scientific acceptance remain distinct and mandatory. `V17_TERMINAL_REDUCER` alone owns terminal dispositions. `can_execute=false`: no wager, trading, funds movement or order authorization.

## Live GitHub ruleset reconciliation — verified

Repository ruleset: [#24907422](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/rules/24907422). A fresh read on 2026-11-15 **after the owner saved the settings** confirmed exactly the three required App-ID-15368 regression contexts and **no longer required** `WOW Independent QA exact-head` (App 5245832) or `WOW Release Authority exact-head` (App 5245853). Strict required-check matching, active `~DEFAULT_BRANCH`, 0 required approving reviews, no bypass actors, non-fast-forward and deletion protection remain. This is observed live settings, not an assertion that committing this document changed the ruleset.

**Verification:** reread the live GitHub ruleset on each sensitive release and compare check **context plus integration ID**; all three required checks must be genuine SUCCESS at the current PR SHA and an authorized owner must decide the protected merge. Any failed/stale/missing required check produces HOLD. An advisory QA/Release finding cannot be forged into success, and material high/critical source-security findings must be investigated.

**Rollback:** if the new policy causes a material security escape or CI qualification failure, restore the prior two App-pinned required contexts (QA 5245832 and Release 5245853), preserve the original three, and revert the affected release through a protected PR. Open an incident with the exact head, findings and deployment receipts. No silent automatic gate changes.

**Owner incident:** #1830; implementation/restoration: #2136; SIRT remains independent read-only challenger, not routine merge approver.
