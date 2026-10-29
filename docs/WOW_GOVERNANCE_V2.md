# WOW Governance V2 — One independent security check, not a certification chain

## Replacement architecture

Keep Engineering and Security as distinct principals. Engineering opens a PR, three existing native regressions run, and one protected-main QA reviewer performs a real semantic source review. The reviewer reads the entire available PR diff *as untrusted data* without checking out or executing PR code. It invokes Claude first and Gemini only for typed provider unavailability. A substantive REJECT, malformed response, policy denial or missing evidence **must not** switch provider or become a PASS.

The QA reviewer independently issues one `WOW Independent Security Review` GitHub check against the live PR **head SHA**, using the existing separate QA App (ID `5245832`), with `checks:write` only. Its artifact records the reviewer provider, findings, run ID/attempt, PR/head SHA, diff digest, and negative/trading-safe disposition. This GitHub check is the source of truth; no separate QA → certifier → receipt → QA handshake is involved.

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
