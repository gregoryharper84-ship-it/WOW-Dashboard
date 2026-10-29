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
separation. An editable PR description alone never certifies source authorship. An owner who
wrote the original source is **not an independent semantic reviewer** even if
the Engineering App copied the code. The QA App must never manufacture an
independent PASS on that basis.

**Solo-operator rule:** WOW has one human operator. No second GitHub user,
external human Security/Architect, or separate human login is required for
governed release. The owner's signed-in exact-head CODEOWNER approval is the
human release authorization; the existing separate QA and Release Apps verify
technical evidence independently of Engineering. An owner-approved first
installation is expressly recorded as an **owner-authorized bootstrap**, not
as an independent semantic AI review. After the reviewer is live, the isolated
QA App conducts automated Claude/Gemini semantic reviews.

A genuine owner-reviewed, source-author-independent PASS can be published by
the separate QA App; the owner cannot directly forge the QA check. Any absent,
stale, dismissed or malformed review, untrusted file provenance, or missing
identity is a typed HOLD. A missing review produces GitHub `action_required`,
never `success`; after authentic GitHub approval, the owner explicitly dispatches the read-only reviewer from protected main at the exact current PR head; GitHub PR review events execute from untrusted merge refs and MUST NOT be granted QA secrets.
For example, after review: `gh workflow run wow-v17-simple-security-review.yml --ref main -f pr_number=<PR> -f head_sha=<HEAD_SHA>`. The check's detailed failure log/receipt supplies the required digest-bound approval text. This is **manual fallback**, not an automatic third model or permission to
place orders, self-approve, merge or deploy.

For Class A reliability repairs, preserve the three native regression checks
and normal authorized release path without a separate semantic review. For
Class B governance and probability-adjacent changes, retain explicit owner
CODEOWNER authorization (when required), independently published QA/Release App
checks, and automated semantic security review after activation. Provider
unavailability cannot become a fabricated security PASS. For Class C
probability-producing changes, retain historical replay, independent model QA,
and full governed validation: no owner exception to sporting probabilities.

**One-time single-operator bootstrap:** on the exact Engineering App adoption
SHA require the existing QA App, distinct Release Authority App, all three
native regression checks, legitimate CODEOWNER approval, and unchanged
source/blob/SHA lineage. Missing evidence or known security findings remain
HOLD. This is a transparent owner-authorized first installation with reduced
semantic-review assurance, not a falsely signed independent review receipt.
After the protected merge, activate the isolated QA App provider credentials,
run actual PASS, REJECT, outage, and moved-SHA negative canaries, add
`WOW Independent Security Review` from QA App ID `5245832` to ruleset
`24907422` with strict base recertification, and verify unreviewed protected
changes are rejected. Only then retire old certifier loops. This does not
automatically mutate GitHub rulesets, enable trading, or authorize deployment.

## Protected transition — must be completed before retiring legacy

1. The repository's sole human owner authorizes the exact App-adopted trust-root SHA. Existing independent QA App, Release App and three native regressions independently enforce technical gates. This bootstrap is documented as owner authorization, not independent semantic source review; no second human reviewer account is required.
2. Configure credentials only in the existing `wow-qa` GitHub Environment: `WOW_QA_APP_ID`, `WOW_QA_APP_PRIVATE_KEY`, and QA-only `WOW_QA_REVIEW_ANTHROPIC_API_KEY` and/or `WOW_QA_REVIEW_GEMINI_API_KEY`. Owner authorizes any relevant API spend; never expose credential values in comments or logs.
3. After the owner-authorized migration merges to `main`, run Claude/Gemini through the existing isolated **QA App** on one exact-head PR. Verify real provider output, SHA/diff digest and QA App identity, and fail-closed outcomes for REJECT, stale SHA and quota outage. This is an automated independent service, not a second human account.
4. Repository admin updates ruleset `24907422`: **require** `WOW Independent Security Review` pinned to GitHub App `5245832`, keeping `WOW required-three regression`, `WOW governed probability backend`, and `WOW additional required regression`. Set `strict_required_status_checks_policy=true` so advancing `main` requires recertification against the new base SHA. The direct review receipt binds **both PR head SHA and base SHA**, and its runner rejects either moving during review. Verify the effective branch protection rejects unreviewed trust-root changes and stale head/base check combinations. The GitHub ruleset is not automatically updated by adding this workflow.
5. Only after the QA App gate is enforced and a negative no-review merge test passes, retire obsolete certifier/canary dependencies through the same owner-authorized, QA/Release-App-governed process. Retain independent postdeploy SIRT verification and exact live SHA acceptance. Review alone never authorizes deployment.

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

The first installation uses owner-controlled bootstrap plus existing independent
QA/Release App checks; **no second human reviewer account is needed**. Owner
authorship still cannot be labeled an independent semantic PASS. This must not
change the permanent post-bootstrap automated semantic check, which remains
separate from Engineering and fails closed on unavailable providers.
Preserve the three existing required regression checks.

Preserve the three existing required regression checks.
