# Temporary Owner Exact-SHA Release Bridge

Interim control for #1540/#1550 only. This is not a replacement for the planned
Engineering / Independent QA / Release GitHub Apps.

## Purpose

Allow Class A/B PRs to advance while App identity setup is incomplete without
letting Engineering self-approve. The bridge executes from protected main and
requires all of:

1. exact PR number and 40-character head SHA;
2. the named canonical incident is open and referenced by the PR;
3. all required exact-head deterministic CI green;
4. independent read-only Claude QA PASS from protected-main code;
5. QA class A or B (Class C is always denied);
6. candidate changes under `.github/**` or `.agents/**` are denied and stay on
   the explicit owner-bootstrap path;
7. owner-held environment secret `WOW_OWNER_RELEASE_APPROVAL` exactly equal to
   `<PR_NUMBER>:<HEAD_SHA>`;
8. the PR head must contain the current protected `main` revision;
9. both the PR head and protected-main SHA must remain unchanged through QA and
   immediately before merge.

The secret must be held separately from Engineering credentials and is scoped to the
existing `wow-release` GitHub environment. A stale approval cannot authorize a
different PR or SHA.

## Owner operation

**Preferred: owner-controlled workflow_dispatch in the authenticated GitHub UI.**
After human review of all seven exact-head CI gates and separately obtained
read-only technical QA, configure the existing `wow-release` environment secret
`WOW_OWNER_RELEASE_APPROVAL` to exactly `<PR_NUMBER>:<EXACT_HEAD_SHA>`.
Run **Actions -> wow-v17-temporary-owner-release-bridge -> Run workflow**
on **main**, entering `pr_number`, `expected_head_sha` and `incident_id`.
This action is for eligible **non-trust-root Class A/B** PRs only. Human-only
trust-root bootstrap PRs must be manually merged by the owner in the UI after
independent evidence; the bridge intentionally denies them.

The owner-held factor is supplied **only** to the step-local environment of
the first `owner-authorization` job, which has **read-only GitHub permissions**
and the protected `wow-release` environment. A failed or missing factor
terminates the workflow **before any paid Claude review** or privileged merge
job runs, with a durable Actions summary. This preauthorization publishes
only validated PR number, exact head SHA, and incident number; no secret bytes.
The `owner-readonly-qa` job runs only after successful authorization, has
read-only GitHub permissions and no release secret/environment. The privileged
`owner-bridge` job requires both authorization and exact-SHA Class A/B QA
PASS, and independently rechecks the authorized PR/SHA/incident, all CI, branch
provenance, and unchanged PR/base SHAs. It never receives the owner factor.

An alternative PR comment command exists, but the account name is shared
by some Engineering integrations, so the command **alone cannot prove human
authorization**. It is ineffective without the separate protected exact-head
owner secret, and the manual-dispatch UI is preferred:

`/wow-owner-bridge <EXACT_HEAD_SHA> incident=<ISSUE_NUMBER>`

Never post any secret value into an issue, comment, log, PR, or chat. Rebind
the protected environment approval after any new PR commit. A missing,
mismatched or stale secret must fail closed with a typed error and a run
summary; never retry with guessed values.

Trust-root changes are never eligible for either bridge path; they remain
explicit owner-bootstrap candidates. A stale command or secret cannot authorize a different SHA. If `main` moves,
the bridge fails closed so the PR can be updated/revalidated against the new
base rather than merging stale certification.

## Boundaries

- merge only; never deploys;
- never authorizes Class C;
- never authorizes `.github/**` or `.agents/**` trust-root changes;
- never changes sporting probability, calibration, thresholds or model ownership;
- never grants wagering/order execution;
- production acceptance and independent SIRT verification remain separate;
- `V17_TERMINAL_REDUCER` remains authoritative;
- `can_execute=false`.

Remove this bridge after #1540/#1550 App identities and source-pinned QA/Release
checks are verified in production.
