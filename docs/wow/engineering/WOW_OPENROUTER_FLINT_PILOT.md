# WOW V17 — OpenRouter Recursive Flint: opt-in synthetic canary

Status: source-only adapter/diagnostic worker + inert workflow proposal; **not merged or production-enabled**. A historical owner-reported Mac `SMOKE_PASSED` is not an independently authenticated provider receipt or proof that hosted GitHub credentials work. No real GitHub Flint worker run has been verified.

## Purpose

This pilot tests the OpenRouter-hosted `recursive/flint-v1.0:free` API without downloading Flint to the owner's 18 GB M3 Pro Mac. The canary only sends one fixed public synthetic coding prompt. It does not send repository content, private issues, logs, code, credentials or betting data. It cannot propose a patch, publish probabilities, create a PR, grant a writer lease, dispatch an engineering worker, merge, deploy, or place a wager.

## Hosted setup — no owner-side CLI required

Flint is intended to run as a **hosted, manual-only research candidate**. The operator does **not** need to install Flint, run its CLI or expose an API key in a local terminal.

1. For an approved free-only pilot, use a dedicated OpenRouter API key with an appropriate spending limit. Never paste its value into chat, source, comments, PRs, or logs.
2. Store the key only as the `OPENROUTER_API_KEY` environment secret for the existing GitHub `wow-engineering` environment, after reviewing its restrictions. Whether the secret is actually readable is **unverified** until a legitimate hosted execution succeeds.
3. The proposal is inert. An authorized protected-workflow author must separately review the exact promoted file, obtain authentic security/governance authorization, and merge it through the live ruleset before `workflow_dispatch` exists.
4. Only then, explicitly consent to the one-time transfer of a public issue number/title and lease group and dispatch from protected `main`. Do not transfer issue bodies, repository code, credentials, logs, or betting data.
5. Require an independently checked one-request receipt and untrusted diagnostic artifact. A model answer or old local smoke report cannot certify a repaired system, probability model or production integration.

## Free-tier considerations

OpenRouter Free currently advertises 50 free-model requests/day and 20/minute; limits and model availability may change. A free hosted model is not an uptime or production availability SLA. At most **one** opt-in live request is sent by this script; no retries. Do not automatically switch to a paid model. HTTP 429 and 402 produce diagnostic holds, not provider substitutions. An unattributed 404 is classified `FLINT_HTTP_404_UNATTRIBUTED` (verify endpoint/model/permissions before considering provider availability); a response without `finish_reason=stop` is `FLINT_MODEL_COMPLETION_INCOMPLETE`. Neither counts as a completed diagnostic and neither permits silent fallback.

## Scope / controls

- `OPENROUTER_API_KEY` — required for an authenticated synthetic call; never log or commit it.
- `WOW_FLINT_PROBE_ENABLED` — must be exactly `true` to permit a network attempt; otherwise `DISABLED`.
- `preflight` — checks only opt-in state and whether a nonempty key is supplied; makes no network request.
- `smoke` — one HTTPS POST to `https://openrouter.ai/api/v1/chat/completions`, `model=recursive/flint-v1.0:free`, max 96 output tokens, 30-second timeout, **no retries** and **no source-code content**.
- All responses are reduced to a redacted diagnostic `status` and non-sensitive usage counts. The model's text and upstream error body are not printed.
- No live dispatcher/worker code, GitHub workflow, secret or production setting is changed by the pilot.

## Adoption gates

1. Independent source review and local regression tests pass on the exact branch head.
2. User installs the API key directly in their environment and authorizes one synthetic live smoke call.
3. Verify a real `SMOKE_PASSED` receipt. Keep errors distinct from missing model capability.
4. Only then propose a worker adapter in the **existing** engineering provider dispatcher/lease group; retain Claude/Gemini/Cursor fallback.
5. Benchmark real non-secret historical bug fixtures, safety, rate-limits, reproducibility, patch+test quality and cost. Independent SIRT/QA/Release verification precedes any protected release.
6. Maintain `V17_TERMINAL_REDUCER` as sole final authority and `can_execute=false`.

## Sources

- https://openrouter.ai/recursive/flint-v1.0:free
- https://openrouter.ai/pricing
- https://openrouter.ai/developers
- https://openrouter.ai/privacy

## Stage 2 — Manual AMRT engineering diagnostic (source-only candidate)

The **source-only** worker is `artifacts/wow-engine/v17/engineering_openrouter_flint_worker.py`. The proposed manual workflow is STAGED at `artifacts/wow-engine/v17/proposals/wow-v17-flint-engineering-worker.candidate.yml` as inert configuration. **This PR deliberately DOES NOT change `.github/` or install the workflow.** Activation requires a separate, genuinely independently authored trust-root PR plus authenticated exact-head owner approval and the normal CI/QA/Release gates; the staged YAML must be independently reviewed before adoption.

**Owner setup already reported:** An `OPENROUTER_API_KEY` was added to the `wow-engineering` GitHub Environment, but the connector cannot reveal its value or independently prove that the environment secret is readable by an actual workflow. The successful Mac smoke test showed user-reported auth for the owner's local key.

**ONLY after the independently authored workflow has been reviewed, authorized, and merged to protected `main`, run the manual canary:**

1. Open [GitHub Actions](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/workflows/wow-v17-flint-engineering-worker.yml).
2. Select **Run workflow** on `main`.
3. Set `target_incident=1688` and `lease_group=P0_AUTONOMY_RESTORE`. This issue was independently checked as OPEN on October 26, 2026. If it later closes, choose another **currently OPEN** issue from the canonical manifest—do not use the closed #1841.
4. Affirm `consent_external_title=true` only after approving a single external transfer of the **issue number, title and lease group**, with no issue body, code or credentials. The model prompt also carries fixed public safety guidance.
5. Confirm the run held the existing engineering single-writer concurrency group and passed the unresolved remote-writer guard. A legitimate unresolved writer reservation means HOLD, not bypass.
6. Inspect the `wow-flint-diagnostic-<run>-<attempt>` artifact. `worker-receipt.json` must contain `status=TRIAGE_CANDIDATE_ONLY`, `can_execute=false`, `writer_lease_granted=false`, `production_routing_changed=false` and `terminal_authority=V17_TERMINAL_REDUCER`. `diagnostic-candidate.json` contains untrusted research notes only; evaluate their correctness before any repair task.

**Boundaries:** exactly one external hosted-model attempt (45s socket timeout, a separate 75s GNU process wallclock cap, 8-minute hosted job safety cap, 700 output tokens; no automatic retry), no shell/tool execution by Flint, no source or test edits, no GitHub writer credential in the model step, no PR authoring, no betting, no live runtime changes, no independent certification. The model's diagnosis is a hypothesis based on **issue title only**, so it cannot be presented as an actual root cause or verified repair. All source/credential data stays out of its prompt.


## Adversarial security review and staged-only limits

The first authenticated Claude review of Flint head `5a681f9b74d0a5167f7131070bd0378046ce734a` returned **REJECT**. Its receipt is immutable. The ensuing source hardening is proposed for a **new** exact-head review, not a reattempt to overturn that verdict.

- **Action supply chain:** The staged example now uses the exact reviewed SHA pins for checkout, Python setup and artifact upload; it does not authorize installing a workflow.
- **Credential boundaries:** The worker and synthetic probe attach their bearer header only as an *unredirected* request header and remove it in a `finally` block even after provider transport failures. This is defense in depth, not proof that arbitrary downstream logging cannot leak secrets. Do not log a live Request or key.
- **Untrusted metadata:** A GitHub read-only exact issue number/state/title is checked against the requested incident, bounded to 200 characters and rejected if malformed, then passed through the canonical manifest selector. The model only sees public issue number, title and lease group as untrusted data.
- **Provenance:** The candidate requires the trusted checkout SHA to contain all dependent scripts, and the test checks that no installed `.github/workflows/` Flint entry exists. These checks do **not** replace a separately independently reviewed protected-workflow adoption PR.
- **Timeout and output:** The request has a bounded response byte limit, a socket timeout, and a separate process wallclock cap at the GitHub runner. Missing, filtered, truncated and malformed output remains HOLD. No probability-specialist or live V17 runtime authority is inferred from receipts or documentation.
- **Review:** Preserve the original signed REJECT and assess every original finding at a newly reviewed source SHA. Run negative, regression and provenance tests before source merge; do not rewrite verdicts, use backup-reviewer shopping, or treat native regression success as semantic PASS.

**Stage 3, requiring separate evidence:** only after one real manual worker run with a validated diagnostic, propose a constrained repair worker and attach trusted source/test fixtures under explicit data-transmission consent. Benchmark against Claude/Gemini and require tests, independent SIRT/QA verification and deployment SHA before any claim of production restoration. No duplicate dispatcher or auto-enable toggle.
