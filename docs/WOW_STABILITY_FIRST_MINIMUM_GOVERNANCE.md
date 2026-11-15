# WOW Minimum Release Governance — authoritative source contract

**Owner authorization:** WOW governance issue #1830. **Policy status:** owner-approved. **Technical status:** migration INCOMPLETE until GitHub ruleset #24907422 actually enforces only the three regression checks. This document is not GitHub admin access, a source-review receipt, or permission to bypass a failed required check.

## Routine engineering release — one path

1. One existing, reviewed-scope pull request with immutable **exact head SHA**, complete changed-file inventory, and provenance. The repository owner is the sole required merge authorizer; zero mandatory approving-review count. An owner's intentional protected merge or otherwise authenticated exact-head owner release decision provides authority; no second human or independent SIRT signature required for routine engineering.
2. **Exactly three required GitHub Actions checks (App ID 15368), all SUCCESS at the same PR head**:
   - `WOW required-three regression`
   - `WOW governed probability backend`
   - `WOW additional required regression`
3. For protected/security-adjacent source, retain exact diff review, narrow **negative-path tests** for secret/token/permission boundaries, stale SHA, event identity, wrong-origin evidence, and non-execution, plus a documented rollback. Real high/critical security defects or credential exposure require repair and confirmation; owner authorization does not make them vanish.
4. Authorized merge through native GitHub protection (non-fast-forward and deletion rules stay enabled; strict required status-check policy stays enabled). No unprotected push, fabricated PASS, forced check run, waived scientific model gate or change to `can_execute=false`.
5. Observe Render web and worker **LIVE at the accepted protected-main SHA**; verify health plus actual relevant governed Action request, immutable receipt, persistence and terminal reconciliation before marking product completion.

## What is NOT a mandatory engineering merge gate

`WOW Independent QA exact-head` (App 5245832), `WOW Release Authority exact-head` (App 5245853), a paid/free AI reviewer, signed SIRT semantic source review, legacy certifier, extra dispatcher lease or approval polling. These lanes may provide **diagnostic** evidence. Never call a deterministic-only QA run an independent semantic review. A substantiated security REJECT must be investigated and fixed regardless of whether the advisory lane is required.

## Permanent exclusions

A **fitted, certified sport specialist**, calibration and calibrated lower bound, fresh inputs, pregame event eligibility, immutable model/Action receipts, governed model publication and independent scientific acceptance remain distinct and mandatory. `V17_TERMINAL_REDUCER` alone owns terminal dispositions. `can_execute=false`: no wager, trading, funds movement or order authorization.

## Required native GitHub admin reconciliation (one-time)

Repository ruleset: [#24907422](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/rules/24907422). As verified on 2026-11-15, the live ruleset has **five**, not three, required contexts. An authorized repository administrator must edit the existing ruleset, **remove only** the two named QA/Release App contexts above, and retain the three exact App-ID-15368 checks. Preserve active branch target `~DEFAULT_BRANCH`, 0 required approving reviews, strict matching, no bypass actors, non-fast-forward and deletion protection. Save and re-read the live ruleset before merging any previously held PR. Do not rename or forge statuses to trick enforcement.

**Verification:** read the GitHub ruleset again and compare both context **and integration ID**; all three actual checks must SUCCESS at the current PR SHA and owner must authorize release. For any failed/stale/missing check, HOLD. The current ruleset cannot be changed by editing this Markdown file or committing a PR.

**Rollback:** if the new policy causes a material security escape or CI qualification failure, restore the prior two App-pinned required contexts (QA 5245832 and Release 5245853), preserve the original three, and revert the affected release through a protected PR. Open an incident with the exact head, findings and deployment receipts. No silent automatic gate changes.

**Owner incident:** #1830; implementation/restoration: #2136; SIRT remains independent read-only challenger, not routine merge approver.
