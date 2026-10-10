# WOW V17 — Agent identity separation: owner setup (#1550, parent #1540)

Status: **PROPOSED — owner actions required.** Engineering prepared and tested this; it changes no settings.
Source of truth: `artifacts/wow-engine/v17/agent_identity_policy.py` (`spec` subcommand).
`evaluate` returning `PASS` is the configuration-acceptance evidence for #1550. It is **not** closure:
#1550 closes only after independent QA acceptance and verified production operation.

## Why

Every actor (owner, ChatGPT, Claude) currently writes to GitHub as `gregoryharper84-ship-it`, so AI actions are
indistinguishable from the owner's and GitHub cannot enforce independent approval. Measured 2026-10-09 from the
Claude session: it cannot change protection, rulesets or repository settings (403), and its bounded merge probe reached
GitHub's SHA guard (409), i.e. merge authorization was **not denied**. `main` requires 3 regression checks, 0 reviews,
and has no rulesets and no CODEOWNERS.

## Target model (sole human owner, full autonomy)

- Ordinary PRs: no human review. Merge only when the 3 regression checks **and** two source-pinned App checks pass:
  `WOW Independent QA exact-head` (QA App only) and `WOW Release Authority exact-head` (Release App only).
- Trust roots also need the owner's code-owner approval: everything under `/.github/` and `/.agents/` (including new
  workflows, actions and agent skills) plus the governance files listed in `CODEOWNERS.proposed`.
  An App-authored PR can be approved by the owner; an owner-authored PR cannot (GitHub forbids self-approval).
- No bypass actors. While any AI session acts as the owner user, an admin bypass is an AI bypass.
- No AI runtime acts as the owner user or can merge; only the Release App merges.

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
Install each App on **Only select repositories → WOW-Dashboard**. QA and Release both hold `checks: write`;
separation comes from pinning each required check to its App's `integration_id` in the ruleset.

## Evidence model (three independent sources)

1. **Owner inventory**, run with *your* credential:
   `python artifacts/wow-engine/v17/agent_identity_policy.py collect-owner --repo gregoryharper84-ship-it/WOW-Dashboard > owner.json`.
   Records repository ID, rulesets, CODEOWNERS (searched in `.github/`, root, `docs/`), role bindings, installations and
   each installation's selected-repository list, plus a fresh `nonce`. Every unreadable or truncated input is a typed
   `IDENTITY_SNAPSHOT_INCOMPLETE` finding, never "absent". It records nothing about AI, so your own owner rights never
   look like an AI finding.
2. **AI principal evidence**, run *inside each AI runtime* (`claude_session`, `chatgpt_session`) with the
   inventory's nonce:
   `collect-principal --repo … --runtime claude_session --nonce <nonce> --merge-probe-pr <open PR>`.
   Expires within 24h. The merge probe calls the **mutating** merge endpoint once, with an all-zero SHA, only against
   an open PR. GitHub's SHA guard refuses it, so nothing merges. Only 403/404 proves denial; a 409 SHA-guard refusal means
   *not denied*; anything else is inconclusive. Both of those HOLD. This evidence is self-reported; the authoritative
   check is live probe `ai_session_cannot_merge`.
3. **Live acceptance**, observed by you on real probe PRs once the ruleset is enforced. One JSON record per probe:
   `{"probe", "expected", "observed": "ALLOW"|"DENY", "pr", "head_sha", "observed_at", "nonce"}`.

| Probe | Required outcome |
|---|---|
| `ordinary_app_pr_merges_without_human_review` | ALLOW |
| `trust_root_pr_blocked_without_owner_approval` | DENY |
| `trust_root_pr_mergeable_after_owner_approval` | ALLOW |
| `push_after_owner_approval_requires_reapproval` | DENY |
| `new_workflow_file_requires_owner_approval` | DENY |
| `qa_check_published_by_release_app_does_not_satisfy` | DENY |
| `release_check_published_by_qa_app_does_not_satisfy` | DENY |
| `engineering_app_cannot_merge` | DENY |
| `ai_session_cannot_merge` | DENY |
| `direct_push_to_main_blocked` | DENY |

`evaluate --owner owner.json --principal claude.json --principal chatgpt.json --live-acceptance live.json`

## Owner steps (signed-in GitHub UI; never paste keys or tokens into chat)

1. **Phase A — stop AI acting as you.** Under *Settings → Applications* (*Authorized OAuth Apps* and *Installed GitHub
   Apps*), remove or restrict repository write for the integrations AI chat sessions use, until they run as the
   Engineering App. Then have each AI runtime run `collect-principal`; the target is a non-owner login with merge DENIED.
2. **Phase B — create three Apps:** *Settings → Developer settings → GitHub Apps → New GitHub App*, names
   `wow-engineering`, `wow-independent-qa`, `wow-release-authority`, permissions exactly as above, *Only on this account*,
   webhook inactive. Install each on WOW-Dashboard only. Keep each private key **only** in that role's runtime.
3. **Bind roles:** *Settings → Secrets and variables → Actions → Variables*: `WOW_ENGINEERING_APP_ID`,
   `WOW_QA_APP_ID`, `WOW_RELEASE_APP_ID` (numeric App IDs; identifiers, not secrets). **Keep these in repository Actions Variables, not environment variables:** job-level `if: vars.WOW_*_APP_ID != ''` is evaluated before environment variables are available.
4. **Phase C (built; activates when the Apps exist):** `wow-v17-independent-qa-check` and
   `wow-v17-release-authority-check` run from protected `main`, never execute PR code, read evidence with the
   read-only workflow token, and publish their check with their own App token downscoped to `checks: write`.
   Store the private keys as **environment secrets only**, never repository secrets: `WOW_QA_APP_PRIVATE_KEY` under environment `wow-qa`, and `WOW_RELEASE_APP_PRIVATE_KEY` under environment `wow-release`. Restrict each environment's deployment branches to **only protected `main`** before adding secrets. Each workflow job declares only its role's environment. Do not grant Engineering Workflows: write until environment restrictions and trust-root protection are independently proven. While a role's `WOW_*_APP_ID` variable is unset the job is skipped (not activated, no red runs); once it is set,
   a missing key fails closed with `QA_APP_CREDENTIAL_MISSING` / `RELEASE_APP_CREDENTIAL_MISSING` and publishes nothing.
   - **QA passes only if**, at the exact head and from `github-actions` only: the 3 regression checks and the change
     impact gate pass, and the trusted governance gate passes. For **trust-root** changes, the owner's latest review
     must instead be APPROVED on this exact head, and an owner-authored trust-root PR can never pass. Trust roots are
     `.github/`, `.agents/`, and every file a governance workflow executes or reads, including its local imports and
     this QA module itself (`agent_identity_policy.governance_trust_roots()`). So no ordinary PR can weaken the gates.
   - **Release passes only if** it independently re-derives every QA condition and finds a successful QA check from
     the QA App, with QA and Release bound to different Apps.
   - Triggers: upstream workflow completion, an hourly sweep (bounded to 20 open PRs) and manual dispatch per PR.
     Each role runs as one serialized queue, and a decision is always published at the SHA it evaluated.
5. **Phase D — protection (owner):** install `artifacts/wow-engine/v17/agent_identity/CODEOWNERS.proposed` as
   `.github/CODEOWNERS`; *Settings → Rules → Rulesets → New branch ruleset → Import* `ruleset-main.proposed.json`,
   replacing `__WOW_QA_APP_ID__` / `__WOW_RELEASE_APP_ID__`. Add the two App checks only after Phase C publishes them,
   or every PR blocks. Run all live probes above and record them. This is where `required_approving_review_count: 0`
   together with code-owner review and last-push approval is proven or disproven. If an ordinary App PR cannot merge
   autonomously, or any `/.github/` change merges without you, the design must change. Never weaken owner protection to
   make autonomy work.
6. **Phase E — recover #1544:** re-propose its exact reviewed content from the Engineering App (fresh exact-head CI,
   QA/Release checks, owner code-owner approval), then #1531.

All codes are registered in `artifacts/wow-engine/docs/failure_codes.md`. `can_execute=false`.
