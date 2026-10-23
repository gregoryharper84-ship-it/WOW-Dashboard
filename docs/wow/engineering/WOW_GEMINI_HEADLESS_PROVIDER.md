# WOW V17 — Headless Gemini engineering provider (backlog #2014)

**Implementation state:** Draft source candidate only. Does not enable billing, install credentials, approve changes, grant a writer lease, merge, or deploy. `can_execute=false`.

## Purpose and ownership

The **existing** `.github/workflows/wow-v17-engineering-provider-dispatcher.yml` remains the **only** automatic provider admission controller; it already runs hourly. There is no new cron/queue. The new `wow-v17-gemini-engineering-worker.yml` is a `workflow_dispatch` target, with the same `wow-v17-engineering-implementation-single-writer` GitHub concurrency group used by existing engineering providers.

AMRT/Conductor owns incident priority, write leases, and execution assignment. This Gemini worker can operate when the user's Mac/Antigravity is closed. It consumes a **separately billed Gemini API credential**, never the consumer Google AI Pro subscription session. It cannot certify betting probabilities or perform wagers; `V17_TERMINAL_REDUCER` stays final.

## Code paths

- Dispatcher: manual `force_provider=gemini` is permitted only when `GEMINI_ENGINEERING_ENABLED=true` **and** `GEMINI_SPEND_CAP_CONFIRMED=true`. By default both are absent/false, so there is no behavior change.
- Automatic routing: only after `GEMINI_AUTO_ROUTING_ENABLED=true` **and** the latest completed actual Gemini worker run succeeded. If Gemini failed, the dispatcher falls back to its **pre-existing** OpenAI/Anthropic selection rather than repeatedly spending on a broken provider. For safety it does not auto-route on a first run: perform the manual canary first.
- Worker: selects at most one canonical OPEN issue from the existing `engineering_dispatch_manifest.json` + live GitHub issue list. Never invents an incident or writer lease. Fails closed for an explicit incident/lease mismatch; an untargeted automatic run chooses a current RESTORATION priority for isolated work only.
- Gemini session: trusted-main checkout, disposable detached worktree, read-only GitHub Actions token not passed to the model step, restricted built-in file tools with shell/MCP/extensions disabled, fixed CLI version `@google/gemini-cli@0.64.0`, model alias `flash`, 900-second timeout, **one CLI invocation** per workflow run. No push, merge, order, or deployment capability.
- Postmodel: enforce patch path restrictions, `git diff --check`, syntax compilation, retain typed readback and a `candidate.patch` for existing Engineering App owner to consider. This **is not** a tested PR or production recovery; a human or authorized Engineering App writer with an actual atomic lease must adopt it through the existing path.
- Model errors: typed timeout/CLI input/turn-limit/quota/rate/auth/upstream/malformed-response receipts; no hidden retries and no invented APPROVED statuses.

## Required owner-side configuration — only after reviewed merge

1. In [Google AI Studio](https://aistudio.google.com/apikey), create a *separate* Gemini API key in a dedicated project. Ensure the real provider account has acceptable billing/quota limits. No ChatGPT/Antigravity consumer tokens are copied to CI.
2. In GitHub **WOW-Dashboard → Settings → Environments → wow-engineering → Environment secrets**, create `GEMINI_API_KEY`. The workflow references `secrets.GEMINI_API_KEY` from `wow-engineering` only. Never paste the key into GitHub issue, PR, or chat.
3. In **Settings → Secrets and variables → Actions → Variables**, set `GEMINI_ENGINEERING_ENABLED=true` and `GEMINI_SPEND_CAP_CONFIRMED=true` **only when the provider-enforced spend budget is independently checked**. Keep `GEMINI_AUTO_ROUTING_ENABLED` absent/false.
4. Run **one manual canary** through the **existing** dispatcher, selecting `force_provider=gemini`, e.g. assigned issue `1841` with `lease_group=P0_LLP_RESTORE`. Validate exact issue/run, nonzero patch if applicable, no git write, bounded billing, model receipt, path checks and CI. A candidate artifact is **not** a live production repair.
5. Only after a real successful worker canary, set `GEMINI_AUTO_ROUTING_ENABLED=true`. Hourly existing dispatcher may then dispatch Gemini when eligible; it continues to obey queue admission, the shared concurrency group and disable-on-latest-failure. Revert that variable to false immediately if quota/cost or repair quality is unacceptable.

**Important limitation:** confirmation variable plus a CLI timeout is *not itself a dollar-denominated spending cap*. Budget protection must be enforced through the provider/account, and usage monitored independently. Google applies quota at the project level with RPM/TPM/RPD and possibly tier-dependent spend-rate limits.

## Required backlog follow-up before autonomous source writing

The initial worker deliberately **does not push to GitHub**. Complete #2014's existing Engineering App handoff work: verify the real atomic per-incident writer lease, bind exact PR/branch/head/issue, present patch + tests to the authenticated Engineering App writer, and use independent QA/Release approval for any protected merge/deploy. Do not add another dispatcher, worker scheduler, credential proxy, or self-approval path.

## Acceptance / verification

- [ ] Exact-head workflow CI, including new provider contract tests, passes.
- [ ] Review and authorize merge. Verify all workflow code came from merged protected main.
- [ ] Independently confirm Gemini API authentication and real provider-budget bounds.
- [ ] Validate manual canary with typed pass/fail and a genuine GitHub Actions artifact.
- [ ] Enable auto route and observe successful Mac-off run without duplicate owner/PR or repeated quota calls.
- [ ] Add authenticated Engineering App same-PR writer integration and stage-specific negative tests.
- [ ] Complete one actual repair: exact-head CI + independent QA/Release + Render deployed SHA + live Action/persisted readback. Never call a candidate artifact production recovery.

References: https://geminicli.com/docs/get-started/authentication/ ; https://geminicli.com/docs/cli/headless/ ; https://ai.google.dev/gemini-api/docs/rate-limits .

Related: local Antigravity pilot PR #2010, existing NFL stage-proof issue #1841 / draft PR #1842, AMRT charter.
