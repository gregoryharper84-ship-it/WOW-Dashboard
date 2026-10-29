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

## Cross-PR content REJECT ledger — P0 repair candidate

A GitHub check bound to a PR head SHA cannot alone prevent the same rejected
file content from being resubmitted in a new PR or on a new base. The reviewer
therefore computes a base-independent SHA-256 digest of the sorted canonical
`(path, Git blob SHA)` changed-file inventory. This is a conservative identity
for identical *changed bytes* and does not replace complete-diff semantic
review on each distinct PR.

Before a semantic provider is invoked, the **separate QA App** reserves this
digest with an `in_progress` check run on the immutable, reachable Governance
V2 bootstrap commit `79fa0c275a58cd9d397c5a1bc81d641b6dad7f75`.
The check name is `WOW Semantic Content Ledger <64-digit digest>`, and
`external_id` must match the full digest. The check is NOT a branch-protection
status. The reviewer reads **all** check runs, bounded to 1,000, and only
accepts exact QA App ID 5245832, sentinel SHA and external ID. Previous
`REJECT` is terminal across PRs and rebases. A stale/crashed `in_progress`
reservation is also a typed HOLD, never a new ungoverned attempt. Completed
`HOLD` (unavailability) and `PASS` do not prohibit a genuine new review.

The GitHub workflow uses one **global single-writer concurrency group** for
all reviewer PRs, preventing two separate PRs from racing between the history
read and reservation. The reviewer commits a `PASS`, `REJECT` or `HOLD`
verdict to the sentinel ledger *before* publishing any PR-head PASS. Failed
ledger writes turn the PR result into HOLD; missing credentials, incorrect
identity, provider policy denial and malformed evidence cannot become PASS.
The QA App's checks-write permission was already required; there is no new
public database or approval identity.

**Limitations and bootstrapping:** Before this change merges, historical
same-content REJECT runs may not have ledger entries; Security/Architect must
independently reconcile any earlier rejected trust-root diffs or retain a hold
on their reentry. This does not guarantee cross-PR replay security until
the source is deployed, the QA App check-read/write canary (PASS and REJECT)
runs, and the required semantic check is enforced. A ledger check failure
or dangling reservation must be repaired with an authenticated provenance
audit, not a silent delete/bypass. Credential names for the `wow-qa`
environment are `WOW_QA_REVIEW_ANTHROPIC_API_KEY` and
`WOW_QA_REVIEW_GEMINI_API_KEY`; source code cannot supply absent secrets.
Do not print or paste actual keys.

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
