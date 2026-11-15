# WOW V28 — One-Path Autonomous Engineering Release

**Status:** reviewed design candidate on #2667; **not activated, merged or deployed by the existence of this document**. Canonical incident: #2136. No new release gate, authority, dispatcher, credential, recurring certifier or queue.

## Outcome and invariant

One existing source PR -> one exact-head classification -> existing three native CI checks -> one independent semantic decision for P/C -> existing QA App 5245832 -> distinct Release App 5245853 -> authorized protected merge -> real production acceptance. The installed ruleset #24907422 and verified current CODEOWNERS are authoritative; this document cannot override either. Preserve their five currently pinned check contexts and correct App identities.

**Never equate deterministic-only QA or a CodeRabbit green completion with independent semantic sign-off.** A check stating 'ZERO-CREDIT DETERMINISTIC QA ONLY' is exactly that, not a source-review PASS. An App-authored byte-for-byte copy is not independent authorship or review. A signed human or App actor must be genuinely distinct from the source author and authorized by the trust policy. Owner CODEOWNER authorization for P/C changes remains a separate exact-head action when required; reviewer installation does not preapprove future changes.

## One-time bootstrap (setup, not a per-PR owner chore)

1. On the **existing GitHub repository**, establish ONE independently authenticated nonauthor source-review identity with the least necessary read and review permissions. Reuse the existing independent-source-review and QA/App workflow surfaces; no new dispatcher, SaaS vendor or paid API without separately approved spend. Confirm that the identity cannot author the PR, forge the QA/Release App identities, alter rulesets, or approve its own policy change.
2. Bind the reviewer to the **existing** exact-head review receipt: repository, PR number, author identity, SHA, complete file inventory, diff digest, independent decision PASS/REJECT, security/probability integrity assessment, and authenticated producer run. Preserve immutability, unique artifact checks, freshness and stale-head rejection. Do not treat unstructured comments, labels or a merely successful CodeRabbit status as that receipt.
3. Configure a single supported reviewer mode in `wow-v17-independent-qa-check.yml`. `WOW_QA_INDEPENDENT_REVIEW_ACTIVE=true` requires an actually functioning independently authorized review source; do not flip it just to make CI green. `WOW_QA_ZERO_CREDIT_MODE=true` remains a transparent reduced-assurance deterministic mode, not semantic evidence. Never run both modes simultaneously; unavailable credentials/quotas produce a typed HOLD, never fallback PASS. Free or already-licensed reviewer capacity is preferred; no spending is authorized here.
4. Prove one authentic **PASS** and one authentic **REJECT** on controlled exact-SHA test PRs, including wrong App, self-review, stale SHA, changed diff, incomplete source inventory, high/critical finding, provider outage, duplicate/replayed artifact, and a concurrent writer. A rejected same SHA stays rejected. Confirm the owner cannot submit the independent verdict and the reviewer cannot merge or modify source.
5. Only after these red/green checks, independent acceptance, and authorized protected merge, activate the reviewer trigger on the existing PR/CI completion path. Verify a real unattended review cycle and a second no-duplicate cycle. Persist one lesson in `.agents/memory/` and the existing `MEMORY.md` line after FIXED_AND_VERIFIED; register a new typed code only if actually new.

**This setup is one-time.** Its ability to review is recurring; an exact-head, genuinely independent verdict is still generated for **every** protected P/C diff, and required owner protected-path authorization cannot be silently pregranted. If that owner action is mandatory, the release remains autonomous through preparation and review but requires the authorized owner's decision to merge.

## Recurring autonomous path (no manual re-dispatch after each passing test)

1. **Repair on existing issue/PR/lease.** Capture real source SHA and canonical incident. One writer at a time. Do not spawn a replacement PR to bypass a review or invent an App copy. The existing source-adoption lane is optional transport unless its current protected contract truly requires it.
2. **Classify actual diff R, P or C.** R is ordinary safe engineering; P includes `.github/**`, `.agents/**`, credentials, permission, QA/Release trust roots, protected schemas or release safety; C changes fitted probability, calibration, thresholds or qualification. P/C overlap is possible. Unknown classification => HOLD. Preserve one certified fitted specialist per probability row.
3. **Run existing tests and checks at exact SHA.** Reproduce failure red and fix green; required three regression contexts remain pinned. For P/C also require negative, security, concurrency, duplicate-event, and fail-closed tests; retain source provenance. An irrelevant old-head or main-scoped green is not source readiness.
4. **Independent review for P/C.** The same existing QA source-review mechanism consumes the authenticated per-head review PASS/REJECT and no other pseudo-approval. One verified independent semantic/security assessment is enough; do not stack multiple provider reviews. A real REJECT is sticky at the same SHA; a missing reviewer produces `SEMANTIC_REVIEW_REQUIRED` (pending/hold), not PASS.
5. **Existing QA and Release Apps consume the same evidence.** QA validates source review, CI and exact identity. Release independently validates QA and final head; both publish only their original App-pinned exact-head checks, never a substitute authorization. No new check name, workflow or extra certifier. A new head invalidates previous review evidence.
6. **Merge only through an authorized actor**, with any required owner CODEOWNER decision and all five actual ruleset checks green at the identical SHA. R may use the already authorized auto-merge lane; P/C use governed protected merge after independent approval. For Class C probability/model/calibration/threshold changes (including P/C overlap), certification is an additional separate prerequisite; HOLD the Class C merge when certification is missing. If approval is missing, advance unrelated independently releasable work in the same lane without watering down a gate.
7. **Observe deployed reality** before DONE: require a protected main merge SHA, both Render web and worker LIVE SHAs matching main, successful `/health/live`, appropriate functional acceptance, product and process scoreboard deltas, and the existing memory-ledger entry. **Affected write route:** exercise the actual Action/route and require its expected successful response, then read back a persisted Supabase row attributable to that same request or receipt identifier. **Affected read-only route:** require a successful probe of that actual route and a separately persisted production-verification receipt binding the exact main/LIVE SHA, probe identity and result. **No affected Action/route:** do not require an impossible affected-route invocation; instead require successful `/health/live` and a relevant read-only production health/configuration probe, plus a separately persisted exact-SHA production-verification receipt of those observations. Do not invent business-data rows, perform side effects solely for certification, or count a heartbeat or CI-green run as deployment proof. For local Antigravity, verify an actual native Sidecar event/new conversation separately from backend Render.

## Implementation order and root-cause checks

- **P0 #2667:** full 426-open-PR discovery must stop failing at 400; keep the bounded six-page ceiling and the negative saturated-inventory test. This is a capacity repair, not semantic independence.
- **P0 #2596:** reuse the existing SIRT intake, link old PRs without claiming an intake issue is approval; test 0/2 -> 2/2 authentic intake-to-verdict cycles, no duplicate or forged provenance.
- **P0 #2136:** eliminate *duplicated* source certification/routing only after the surviving existing QA->Release path proves nonauthor independent acceptance and exact-head negative coverage. The owner-authored protected PR path must not deadlock on an unnecessary App copy; owner self-review is still invalid.
- **P1 #2010:** after source integrity and protected acceptance, integrate the Antigravity Sidecar and observe one actual native task. Keep it opt-in, no paid Google API, no execution.

## Mandatory counterexamples and process score

| Counterexample | Expected result |
|---|---|
| Missing independent P/C verdict / zero-credit-only green | Typed HOLD, no semantic PASS |
| Same author signs review or copied App identity | Typed HOLD |
| Wrong App ID / missing CODEOWNER / source head changes | Typed HOLD; old receipts unusable |
| Actual high/critical finding / sticky REJECT | Reject at same SHA; repair and fresh head/review |
| 426 open PRs with cap six / 600 saturated entries | Full scan / fail-closed inventory HOLD |
| Duplicate workflow callback or competing writer | One action, no duplicate review or PR |
| Missing fitted model or missing features | Typed MODEL_UNAVAILABLE or precise input/scorer code; no odds/LLM substitution |
| Green source CI but Render/Supabase mismatch | Not production verified |

Track first-pass rate, rework rate, time-to-diagnosis, and escaped defects. Per sport/market track `model_evaluated/total`, qualified rank-eligible rows, live SHA parity and health. Every release receipt states the real delta, including 0. Three same-class failed attempts require a new mechanism hypothesis.

**Hard boundaries:** `custom_gpt_identity=WOW_BETTING_ENGINE`, `runtime_generation=V17_ACTIVE`, `terminal_authority=V17_TERMINAL_REDUCER`, `can_execute=false`, `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`. No wagers, orders, protected history rewrites, live capital, bypass, fabricated fitted probability, owner self-approval, or paid-provider activation.

**Current state:** this is an executable specification and release checklist attached to an existing P0 source PR, not proof that an autonomous reviewer identity has been installed, any PR merged, or any production path deployed.
