# WOW V17 — Agent identity separation: owner setup (#1550, parent #1540)

Status: **PROPOSED — owner actions required.** Engineering prepared and tested this; it changes no settings.
Machine-readable source of truth: `artifacts/wow-engine/v17/agent_identity_policy.py` (`spec` subcommand).
Acceptance: `agent_identity_policy.py evaluate` returns `PASS` against a live snapshot.

## Why

Every actor (owner, ChatGPT, Claude) currently writes to GitHub as `gregoryharper84-ship-it`. An AI-posted comment
or merge is therefore indistinguishable from the owner's, and GitHub cannot enforce independent approval.
Measured 2026-10-09: the Claude session credential **cannot** change protection, rulesets or repo settings (403),
but **can merge** PRs (409 on an all-zero-SHA probe). Main requires only 3 regression checks, 0 reviews, no rulesets, no CODEOWNERS.

## Target model (sole human owner, full autonomy)

- Ordinary PRs: no human review. Merge only when the 3 regression checks **and** two source-pinned App checks pass:
  `WOW Independent QA exact-head` (QA App only) and `WOW Release Authority exact-head` (Release App only).
- Trust roots (everything under `/.github/`, incl. new workflows and CODEOWNERS): additionally require the owner's code-owner approval.
  An App-authored PR can be approved by the owner; an owner-authored PR cannot (GitHub forbids self-approval).
- No bypass actors. While AI sessions act as the owner user, an admin bypass is an AI bypass.
- No AI runtime holds the owner's user credential or any merge capability outside the Release App.

## App permissions (exact — anything else is a HOLD finding)

| Role | Permission | Level |
|---|---|---|
| engineering | `metadata` | read |
| engineering | `contents` | write |
| engineering | `pull_requests` | write |
| engineering | `issues` | write |
| engineering | `workflows` | write |
| engineering | `actions` | read |
| engineering | `checks` | read |
| qa | `metadata` | read |
| qa | `contents` | read |
| qa | `pull_requests` | read |
| qa | `issues` | read |
| qa | `actions` | read |
| qa | `checks` | write |
| release | `metadata` | read |
| release | `contents` | write |
| release | `pull_requests` | write |
| release | `issues` | write |
| release | `actions` | read |
| release | `checks` | write |
| release | `deployments` | write |

Never granted to any agent App: `actions_variables`, `administration`, `environments`, `members`, `organization_administration`, `pages`, `repository_hooks`, `repository_projects`, `secrets`, `security_events`.
Install each App on **Only select repositories → WOW-Dashboard**. No webhook is required for the first phase (uncheck *Active*).

QA and Release both hold `checks: write`; separation comes from pinning each required check to its App's
`integration_id` in the ruleset, so the Release App cannot satisfy the QA check and vice versa.

## Owner steps (signed-in GitHub UI; never paste keys or tokens into chat)

1. **Phase A — stop AI merges as the owner.** Review *Settings → Applications → Authorized OAuth Apps* and
   *Installed GitHub Apps*. Remove or restrict repository write for the integrations used by AI chat sessions
   (Claude, ChatGPT connectors) until they run as the Engineering App. Then re-measure with
   `agent_identity_policy.py collect --repo gregoryharper84-ship-it/WOW-Dashboard --merge-probe-pr <open PR>`
   from that session. A 403/404 probe means merge capability is gone.
2. **Phase B — create three Apps:** *Settings → Developer settings → GitHub Apps → New GitHub App*, names
   `wow-engineering`, `wow-independent-qa`, `wow-release-authority`; set the permissions above; *Only on this account*.
   Install each on WOW-Dashboard only. Store each private key **only** in the runtime that role uses
   (separate secrets; never in the repo, never in a shared runtime).
3. **Bind roles:** *Settings → Secrets and variables → Actions → Variables*: `WOW_ENGINEERING_APP_ID`,
   `WOW_QA_APP_ID`, `WOW_RELEASE_APP_ID` (numeric App IDs; these are identifiers, not secrets).
4. **Phase C (Engineering, governed PRs):** QA and Release workers publish `WOW Independent QA exact-head` / `WOW Release Authority exact-head`
   bound to the exact head SHA, failing closed on stale SHA, forged or missing evidence, PR-author self-review and provider outage.
5. **Phase D — protection (owner):** install `artifacts/wow-engine/v17/agent_identity/CODEOWNERS.proposed` as
   `.github/CODEOWNERS`; then *Settings → Rules → Rulesets → New branch ruleset → Import* with
   `ruleset-main.proposed.json`, replacing `__WOW_QA_APP_ID__` / `__WOW_RELEASE_APP_ID__`. Add the two App checks
   only after Phase C publishes them, or every PR blocks. Then retire the legacy branch protection.
   Probe: an App-authored trust-root PR must block without owner approval; an ordinary PR must block until both App checks pass.
   Confirm on GitHub that code-owner review is enforced with `required_approving_review_count: 0`.
6. **Phase E — recover #1544:** re-propose its exact reviewed content from the Engineering App (fresh exact-head CI,
   QA/Release checks, owner code-owner approval), then #1531.

## Typed findings

All `IDENTITY_*` / `PROTECTION_*` codes are registered in `artifacts/wow-engine/docs/failure_codes.md`.
`PASS` is a configuration verdict only; it is never merge, release or probability authority. `can_execute=false`.
