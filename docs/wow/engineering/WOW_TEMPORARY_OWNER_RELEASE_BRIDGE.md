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

The secret is intentionally not readable by Engineering and is scoped to the
existing `wow-release` GitHub environment. A stale approval cannot authorize a
different PR or SHA.

## Owner operation

Preferred chat/UI path: on the target PR, the owner posts exactly:

`/wow-owner-bridge <EXACT_HEAD_SHA> incident=<ISSUE_NUMBER>`

The protected-main workflow accepts that command only from
`gregoryharper84-ship-it`, re-resolves the live PR head, then applies every CI
and independent-QA gate before merging.

Alternative manual-dispatch path: set the `wow-release` environment secret
`WOW_OWNER_RELEASE_APPROVAL=<PR_NUMBER>:<EXACT_HEAD_SHA>` and run
**wow-v17-temporary-owner-release-bridge** with the matching inputs.

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
