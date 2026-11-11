# V17 governance dispatcher

The protected-main `wow-v17-governance-dispatcher` reconciles same-repository,
open PRs every 15 minutes and can be run manually for one PR. It is a router,
not an authority: it may request only the protected-main source-review, QA, and
Release `workflow_dispatch` paths. It cannot publish checks, create reviews,
approve or merge a PR, deploy, or change model/governance behavior.

## Preconditions and activation

- Keep this controller on a separate non-owner-authored protected-source PR.
  Do not stack it on PR #1915 or activate it before that bootstrap is
  independently reviewed and merged.
- The protected source-review workflow and verifier must exist on `main`.
  Until they do, reconciliation returns a typed HOLD and dispatches nothing.
- Before rebasing or activating after #1915, verify its protected-main QA
  decision path requires the exact source-review artifact for manual and
  `workflow_run` invocations too, and Release independently re-derives that
  evidence. The dispatcher must not be the only layer enforcing the reviewer
  prerequisite; if the merged role workflows do not enforce it, keep rollout
  blocked.
- Preserve ruleset `24907422` and all required check names/App identities.
- Configure the `wow-dispatch` environment to allow only protected `main`, and
  define its `WOW_DISPATCH_JOURNAL_HMAC_KEY` secret as a randomly generated
  256-bit key represented by exactly 64 hexadecimal characters. The controller
  holds before any dispatch if that key is absent or malformed. Do not reuse
  this secret for QA, Release, or GitHub App signing.
- `wow-qa` and `wow-release` remain the only environments holding their
  respective QA and Release signing credentials. The dispatcher never reads
  those credentials.
- The workflow needs `actions: write` because GitHub's workflow-dispatch API
  permission is broader than a dispatch-only permission. Its code uses that
  authority only for the three exact allowlisted workflow dispatch endpoints.
  `issues: write` is used only to post a PR+head+role reservation before the
  dispatch POST.

Each schema-2 journal marker contains canonical JSON authenticated with
HMAC-SHA256. A marker is accepted only when its signature matches the protected
environment key and the referenced run is the exact protected-main dispatcher
workflow, repository, event, run ID, and run attempt. Unsigned, forged, or
tampered markers are not reservations. A valid marker with inconsistent run
provenance holds. Never print or expose the key.

Do not call the reservation comment an atomic lease. HMAC proves marker
authenticity, not distributed compare-and-swap. A fixed workflow concurrency
group serializes this controller, but does not exclude other writers. A
reservation with no corresponding protected-main role run is ambiguous and
must remain HOLD; there is no blind dispatch retry. Disable the controller and
manually reconcile existing reservations before any journal-key rotation.
Atomic per-intent `(PR, head SHA, role)` reservation, multi-controller exclusion,
and restart/key-rotation reconciliation still require independent proof before
describing this as fully autonomous closure.

When the legacy protected certifier fails alongside a newer same-head
zero-job `action_required` ancillary workflow run, the router reports
`ANCILLARY_CI_EVENT_POLICY_HOLD` with the workflow and run ID. This is not a
successful check and is not retried. The three actual required regression
checks and change-impact evidence remain separately enforced; changing the
legacy certifier's ancillary-workflow policy is a separate protected-gate
change requiring independent review.

## Reconciliation order

1. Resolve the live PR, same-repository source/base, exact head, complete
   changed-file inventory, and both sides of renames.
2. Treat current and previous file paths as trust roots. A trust-root PR must
   be authored by a non-owner and have the owner's genuine APPROVED review on
   that exact head.
3. Require the three Actions App 15368 regression checks and change-impact
   check to pass before dispatching the read-only source reviewer. These checks,
   exact PR/file provenance, and trust-root owner approval are reviewer
   admission; the downstream trusted-governance receipt is not.
4. Validate a fresh protected-main source-review artifact. A missing review
   receipt, including an exact protected source-review run that ended
   `action_required` with zero jobs, may request only a new read-only
   source-review dispatch. That blocked run is never treated as success.
   Rejected, malformed, ambiguous, or otherwise failed runs HOLD. For ordinary
   PRs, after the authenticated exact-head source-review PASS, require the
   Actions App 15368 `Trusted exact-head engineering governance` check before
   any QA or Release dispatch. If an
   earlier independently triggered review artifact expires while CI is
   pending, a fresh source-review dispatch is allowed only after its prior
   successful run is proven; controller-issued source-review attempts are
   capped at two for the same PR+SHA. A missing, rejected, malformed, or
   ambiguous artifact remains HOLD. Route QA only after review and evidence
   are ready, then Release only after the exact-head QA App 5245832 check
   succeeds. A legacy-certifier ancillary `action_required` HOLD cannot prevent
   the read-only reviewer dispatch, but remains a HOLD before QA/Release. The
   existing role workflows independently re-derive their authority.
5. Reserve the exact PR+SHA+role in a bot-authored issue comment before the
   non-idempotent POST. On an uncertain response, missing run, stale evidence,
   foreign App, failure, or duplicate run, HOLD and reconcile; never mint a
   check or retry blindly.

The sweep rotates a bounded 20-PR page by controller run number, dispatches at
most four roles per run, permits at most one active dispatch per role (these
role workflows use singleton concurrency groups), and caps total active role
workflow WIP at four. These bounds do not disable GitHub's separate approval
requirement for Copilot pull-request CI.

## Exact-head manual dispatch

The manual QA and Release workflows retain `pr_number`. Their optional
`head_sha` inputs add a stale-head check; when present, each workflow resolves
the live PR first and refuses a mismatched SHA. The run name records the
requested SHA. Always select `ref=main`; do not dispatch a role workflow from
a candidate branch.

## Safe rollback and verification

Before activation, complete protected-root approval, independent Security/SIRT
review, negative and adjacent workflow tests, full required CI, and exact-head
QA/Release checks. After merge, prove the behavior using an inert ordinary PR:
review-before-CI and CI-before-review; correct real GitHub App check identities;
stale, forged, missing, rejected, or unavailable evidence; and genuine QA then
Release. Verify that no check is fabricated and no merge/deploy occurs.

If a live acceptance check fails, stop the controller through a protected
workflow change (or disable it using the authorized repository control), leave
the independent QA/Release workflows and required ruleset checks intact, and
reconcile any pre-POST reservations manually. Code on this branch is not proof
of activation or production verification.

`can_execute=false` · `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

## Opt-in zero-credit routing

The protected-main controller can route directly to deterministic QA when
`WOW_QA_ZERO_CREDIT_MODE=true` and
`WOW_QA_INDEPENDENT_REVIEW_ACTIVE=false`. It skips the absent source-review
workflow and the non-required legacy certifier, but never claims semantic
source review. It still verifies exact-head regression checks and trust-root
owner approval, then uses separate QA and Release App checks. Release
workflow dispatch requires the authenticated successful QA run ID and attempt.
The dispatcher does not approve any pull request or make deployments.

Do not activate this controller unless the `wow-dispatch` environment is
configured for protected `main` with `WOW_DISPATCH_JOURNAL_HMAC_KEY` (a
random 64-character hex HMAC key). In free mode, no OpenAI, Claude, or
provider-model API key is needed.
