# WOW V17 Class A — Independent Approval & Release Automation

Date: 2026-10-08. Status: PROPOSED / NOT DEPLOYED. Related: #1536 #1537, draft PR #1538.

## What this PR implements

- A fail-closed policy classifier checks exact PR head, changed paths, current review receipts and configured reviewer allowlists.
- An advisory workflow automatically REQUESTS independent reviewers for non-draft same-repository PRs, if GitHub repository variables contain valid usernames. It NEVER submits approvals.
- High-risk PRs and opted-in PRs can raise one idempotent escalation issue; a 4-hour cron rechecks PRs labeled wow-approval-audit.
- Protected-main code only is executed. PR-head code is never checked out by privileged workflows. JSON evidence receipts are retained for 30 days.
- Risk classes: R1 documentation; R2 general engineering; R3 trust roots, workflow, editor, auth, credentials, deploy/terminal/calibration; Class C specialist/model behavior.
- Tests cover head staleness, independent reviewer identity, bot/self review, review overrides, malformed evidence and no fake merge authorization.

The workflow is ADVISORY. The success of an automated audit is NOT a protected branch review, SIRT approval, verified production promotion or permission to deploy.

## One-time GitHub setup — actual repo admin required

1. Designate authorized real independent QA reviewer(s) and distinct release reviewer(s). A GitHub bot or the PR author cannot serve as its own independent reviewer. The repository currently belongs to an individual GitHub account, not an organization team.
2. Give legitimate accounts adequate reviewer permissions. Under GitHub Settings > Secrets and variables > Actions > Variables:
   - WOW_INDEPENDENT_QA_REVIEWERS = comma-separated real QA GitHub usernames
   - WOW_INDEPENDENT_RELEASE_REVIEWERS = comma-separated distinct real release reviewer usernames
   These values only control review requests and receipt classification; they are not permissions, credentials or authorizations.
3. Create a real .github/CODEOWNERS only after the collaborator names are verified, especially for security, deployment, V17 models, calibration, workflows and terminal reduction. Do not commit placeholders or claim a bot is independent QA.
4. Under GitHub Settings > Rules, protect main: PR reviews required, code-owner review, required exact-head CI, dismiss stale reviews, no force-push/unchecked bypass. Independently verify the rules apply. The GitHub connector returned no repository rulesets and could not read branch protection (403), so effective protection is not presently proven.
5. Under Settings > Environments, create a protected production environment with authorized reviewers, prevent self review and restrict deployment branches and credentials. Verify plan eligibility and effective enforcement.
6. In a SEPARATE protected security PR, attach environment: production to the actual Render deployment job and remove alternative deployment paths that bypass that gate. The current Render workflow automatically deploys eligible main commits after successful checks. Do not enable auto-merge before production environment approval is enforced and independently verified.
7. Validate that independent SIRT review, CI and deploy evidence have named verifiers and immutable receipts. An automated reviewer-request action is not itself an approval.

If there is no real independent reviewer to designate, hold the rollout and record REVIEWER_AUTHORITY_MISSING. Do not let the engineering bot approve itself.

## Risk policy (proposal, not active GitHub enforcement)

R1: Docs/noncode, independent reviewer + native checks, potentially automated merge only after protections are proven.

R2: Bounded restorative code, independent QA, applicable SIRT, distinct release approval + required CI, then governed release.

R3: Security, workflows, auth/secrets, Action/editor sync, protected deployment, calibration/terminal contracts: explicit authorized approval and release environment controls; no unattended merge.

Class C: Model, fitted artifacts, probability/calibration or qualification changes: chronological replay, counterexamples, holdout, forward validation, independent review and explicit governed promotion.

All classes preserve can_execute=false, dry-run-only no wagers/orders, and V17_TERMINAL_REDUCER sole publication authority.

## Acceptance before merging this implementation

- Independent review of privileged pull_request_target / pull_request_review event contexts and least-privilege token scopes.
- Proven no PR code or body executed as shell; collaborator and bots verified by native GitHub protected review, not by untrusted issue text.
- Automated reviewer requests never equal APPROVED. Missing reviewers, stale head, pending draft, untrusted identity and missing quote/model are fail-closed in their respective lanes.
- New workflow and policy code are on the protected Morning-Green path denylist.
- Python unit tests, YAML validity, shell parsing, and action checks independently run at exact head.
- No change to Render workflow, existing production deployment permissions, branch settings or V17 scoring/reducer/model is implied by this PR.
- This audit is not a required merge gate until independently reviewed and installed as an enforceable repository-native policy; do not interpret advisory green as production approval.

## Operations

The periodic loop scans only label wow-approval-audit to avoid amplifying the current P0 repair backlog. PR events audit automatically. Missing approval issues are idempotent per PR and remain for independent review. Subsequent live workflow behavior must be verified after a protected merge before calling this feature operational.