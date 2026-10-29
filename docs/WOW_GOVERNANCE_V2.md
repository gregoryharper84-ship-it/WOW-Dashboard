# WOW Governance V2 — One independent security check, not a certification chain

## Replacement architecture

Keep Engineering and Security as distinct principals. Engineering opens a PR, three existing native regressions run, and one protected-main QA reviewer performs a real semantic source review. The reviewer reads the entire available PR diff *as untrusted data* without checking out or executing PR code. It invokes Claude first and Gemini only for typed provider unavailability. A substantive REJECT, malformed response, policy denial or missing evidence **must not** switch provider or become a PASS.

The QA reviewer independently issues one `WOW Independent Security Review` GitHub check against the live PR **head SHA**, using the existing separate QA App (ID `5245832`), with `checks:write` only. Its artifact records the reviewer provider, findings, run ID/attempt, PR/head SHA, diff digest, and negative/trading-safe disposition. This GitHub check is the source of truth; no separate QA → certifier → receipt → QA handshake is involved.


## Three-stage security reviewer with authenticated owner recovery

The only semantic-review sequence is **Claude (primary) -> Gemini (fallback on provider
unavailability only) -> repository owner as a real human third reviewer**. No provider
switchover is allowed after a substantive REJECT, malformed output, incomplete evidence,
policy failure, stale identity, or security finding. A missing key, typed quota error
or provider transport/auth outage is availability; each attempt is recorded.

Owner recovery is not an automatic approval. After *both* providers are unavailable,
the QA App evaluates a signed-in GitHub `APPROVED` pull-request review by
`gregoryharper84-ship-it`, attached to the exact adopted **head SHA**, with an
explicit body binding the PR number, base SHA and complete diff SHA-256:

`WOW_SECURITY_FALLBACK_PASS PR=<number> HEAD=<40-char-SHA> BASE=<40-char-SHA> DIFF_SHA256=<64-char-digest>`

An owner approval without that exact binding is **not** a security review. The
workflow also checks original source PR lineage, exact source commit, immutable Engineering App adoption
commit message/parent SHA, changed-file blob identities, and human/source-author
separation. An editable PR description alone never certifies source authorship. **An owner who authored the
original source is NOT an independent reviewer**, even when an Engineering App
copies the files into its own PR. For that case the check fails closed and a
different authenticated Security/Architect is mandatory. The owner may still
authorize release separately but cannot manufacture an independent semantic PASS.
This applies to bootstrap PR #2254 (original owner source #2251).

A genuine owner-reviewed, source-author-independent PASS can be published by
the separate QA App; the owner cannot directly forge the QA check. Any absent,
stale, dismissed or malformed review, untrusted file provenance, or missing
identity is a typed HOLD. A missing review produces GitHub `action_required`,
never `success`; after authentic GitHub approval, the owner explicitly dispatches the read-only reviewer from protected main at the exact current PR head; GitHub PR review events execute from untrusted merge refs and MUST NOT be granted QA secrets.
For example, after review: `gh workflow run wow-v17-simple-security-review.yml --ref main -f pr_number=<PR> -f head_sha=<HEAD_SHA>`. The check's detailed failure log/receipt supplies the required digest-bound approval text. This is **manual fallback**, not an automatic third model or permission to
place orders, self-approve, merge or deploy.

For Class A routine repairs keep the existing deterministic CI/release
requirements. Class B trust roots and probability-adjacent changes need an
independent reviewer; Class C probability changes need the full governed
validation process. No owner fallback can waive those other gates.

**One-time bootstrap:** obtain a genuine external distinct-principal review
of the reviewer installation itself before protected merge; then configure
the QA App and providers and canary-test both the PASS and no-review
failure path, pin `WOW Independent Security Review` in ruleset #24907422,
enable strict base synchronization, and retire the redundant legacy loops
only after demonstrated enforcement. All GitHub ruleset/admin/secret
operations require the authorized administrator; documentation is not an
automatic ruleset mutation.

## Protected transition — must be completed before retiring legacy

1. A distinct, authenticated Security/Architect reviewer validates this new trust-root migration itself through the existing authorized path. Neither an owner's self-review nor a deterministic CI PASS is that independent review.
2. Configure credentials only in the existing `wow-qa` GitHub Environment: `WOW_QA_APP_ID`, `WOW_QA_APP_PRIVATE_KEY`, and QA-only `WOW_QA_REVIEW_ANTHROPIC_API_KEY` and/or `WOW_QA_REVIEW_GEMINI_API_KEY`. Owner authorizes any relevant API spend; never expose credential values in comments or logs.
3. After the reviewed migration merges to `main`, run it on one exact-head PR. Confirm an actual external reviewer response, correct SHA/diff digest, **QA App** check provenance, and fail-closed results for a REJECT and a moved SHA.
4. Repository admin updates ruleset `24907422`: **require** `WOW Independent Security Review` pinned to GitHub App `5245832`, keeping `WOW required-three regression`, `WOW governed probability backend`, and `WOW additional required regression`. Set `strict_required_status_checks_policy=true` so advancing `main` requires recertification against the new base SHA. The direct review receipt binds **both PR head SHA and base SHA**, and its runner rejects either moving during review. Verify the effective branch protection rejects unreviewed trust-root changes and stale head/base check combinations. The GitHub ruleset is not automatically updated by adding this workflow.
5. Only after that enforcement is verified, retire the obsolete trusted certifier/canary dependencies via a separately independently reviewed change. Keep release actor separation and independent postdeploy verification by SIRT. Release is not authorized merely because a reviewer check passed.

## Policy

The reviewer does not author source, approve its own implementation, merge, deploy, or change sporting-probability semantics. Missing keys, provider outages, incomplete GitHub file patches, duplicate or stale identities, and malformed JSON fail closed. An authentic REJECT never triggers provider shopping. GitHub review/CI are for admission; Render LIVE SHA, behavior and reconciliation are separate production acceptance.

`custom_gpt_identity=WOW_BETTING_ENGINE`
`runtime_generation=V17_ACTIVE`
`terminal_authority=V17_TERMINAL_REDUCER`
`can_execute=false`
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

## Reviewer hardening consolidated for the final adoption

The reviewer validates a typed Anthropic tool response, not unstructured prose. Explicit
529, quota exhaustion and retired-model availability errors may invoke Gemini. Substantive
policy rejection, malformed output or actual security findings remain terminal and cannot
provider-shop. The QA App checks all prior review checks bound to the same head SHA: an
authentic REJECT is sticky until the code SHA changes. The workflow double-checks PR head,
base and current protected main immediately before publishing a PASS, including the human
fallback path, and fails closed if the source moves during diff pagination.

The initial reviewer installation requires separately authenticated Security/Architect
review. An owner-authored source cannot use owner fallback to certify itself. Pin the
QA App security check and strict protected-base recertification before retiring the old
governance certifier. Preserve the three existing required regression checks.

## Durable QA content-ledger recovery (Cursor P0 lane, issue #2136)

Engineering PR #2275 introduces a QA App content-ledger reservation on the immutable
sentinel SHA `79fa0c275a58cd9d397c5a1bc81d641b6dad7f75` before any semantic provider
call. A cancelled, killed, timed-out or disconnected runner can leave that reservation
`in_progress`, which #2275 fail-closes as `CONTENT_LEDGER_IN_PROGRESS`. That is correct
safety and an operational deadlock.

Recovery is a **separate** protected-main workflow, `wow-v17-qa-ledger-recovery.yml`,
using the same QA App identity (5245832, `checks:write` only) and the same global
single-writer concurrency group `wow-independent-security-qa-ledger-writer`. It is not
a second reviewer and is not a required merge check.

Recovery may complete an orphaned reservation only as **HOLD** or confirmed **REJECT**.
It never publishes PASS, never deletes check history, and never uses the Engineering
App, owner token, or workflow `GITHUB_TOKEN` to mutate the ledger. Missing or
non-distinct QA App authentication fails closed without mutation.

Required provenance is written onto the still-`in_progress` check by
`attach_reservation_evidence` immediately after reservation: exact check-run ID,
content digest, PR/head/base, originating run ID/attempt, actor, and whether a
provider has been invoked. Integration with #2275 is three calls after
`reserve_content_review` / before and after `semantic_review`; until those marks exist,
recovery refuses to guess and leaves the reservation in place.

Rules:

- Source workflow run must be completed with `cancelled`, `failure`, `timed_out` or
  `startup_failure`. An active run is not recovered.
- Confirmed REJECT (reservation mark, PR-head QA check, or sibling ledger REJECT)
  completes the reservation as REJECT.
- Kill/cancel **before** provider invocation, with provenance, completes as HOLD.
- Kill/cancel **during** provider review without a persisted decision fails closed
  (`LEDGER_RECOVERY_PROVIDER_DECISION_UNPROVEN`) and does not mutate.
- Two recovery workers take a lease on the same check; the loser aborts. Duplicate
  recovery events are idempotent. Transient GitHub 5xx/429 PATCH failures retry at
  most three times.
- A stale reservation is never converted automatically to PASS.

### Experimental re-roll budget — inactive unless authorized

A one-byte file modification changes the Git blob SHA and therefore the content
digest. The ledger blocks *identical* content across PRs; it does not by itself
stop trivial edits from resampling a previous REJECT. `wow_qa_reroll_policy.py`
implements a bounded per-PR REJECT budget behind
`WOW_QA_EXPERIMENTAL_PR_REJECT_BUDGET`. The variable is **not** set by the recovery
or reviewer workflows. An authorized administrator must set a positive integer
1–20 on the `wow-qa` environment before the budget may hold further distinct-content
attempts on the same PR. Identical-content REJECT remains terminal even when the
budget is inactive. A genuine revised file set on that PR remains reviewable until
the authorized budget is exhausted.

`custom_gpt_identity=WOW_BETTING_ENGINE`
`runtime_generation=V17_ACTIVE`
`terminal_authority=V17_TERMINAL_REDUCER`
`can_execute=false`
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
