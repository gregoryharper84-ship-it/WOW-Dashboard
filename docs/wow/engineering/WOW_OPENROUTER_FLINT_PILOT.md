# WOW V17 — Flint fixed-input research canary (not activated)

**Status:** source-only proof-of-concept; no installed Flint GitHub Actions workflow, no live-worker admission, and no production deployment. Historical security REJECTs remain binding for their original SHAs. The new source head must independently pass review and regression checks.

## Exactly what the code can and cannot prove

The canary sends a *fixed public synthetic* Python prompt to the hardcoded OpenRouter HTTPS endpoint, targeting `recursive/flint-v1.0:free`. It accepts no user-provided prompt, incident title, source code, model probability, secret in the prompt or GitHub write action. It has no filesystem writer, fitted scorer or orders path. It returns only a redacted research receipt.

A `SMOKE_PASSED` receipt is possible only when all of these checks hold:

1. `WOW_FLINT_PROBE_ENABLED=true` **and** `WOW_FLINT_EXTERNAL_CONSENT=true`, with an available OpenRouter key. An environment flag alone never authorizes an external network call.
2. An authenticated hosted GitHub Actions context claims the fixed repository, `main` ref, `workflow_dispatch` event, 40-hex source SHA, positive run ID, and the **specific approved Flint workflow reference**.
3. The script reads `GET /repos/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/{run_id}` using a read-only GitHub token, checks its returned repository, run number, exact source SHA, `main` branch, manual event, workflow path and active status. Absent, malformed, failed or inconsistent readback fails closed *before* contacting OpenRouter.
4. All outbound calls use a no-redirect transport. Each credential is an unredirected header cleared from the Request object in a `finally` block. No upstream or GitHub response body, provider text or secret is printed.
5. The response fits the 64 KiB cap, has one completed choice, nonblank content, nonempty bounded completion ID and a provider-reported `model` **exactly equal** to the requested model. An echoed model is only an *authenticated API claim*, **not independent cryptographic evidence** of which model weights served or whether calibrated probabilities exist.

**The approved workflow reference is deliberately NOT installed on `main`.** Therefore steps 2–3 cannot be satisfied by the present repository and no actual Flint request is permitted through this source. A local shell setting the right variables, without an authenticated GitHub run at the exact workflow and source SHA, cannot produce success. The named GitHub environment `wow-engineering` must be required by a *separately governed* protected workflow, with a verified consent input and restricted read-only token. This source file cannot verify environment membership from GitHub's workflow-run endpoint alone; it does **not** claim that it can.

## V17 authority boundary

`custom_gpt_identity=WOW_BETTING_ENGINE`, `runtime_generation=V17_ACTIVE`, `terminal_authority=V17_TERMINAL_REDUCER`, `can_execute=false`, and `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true` remain unchanged. The code's `can_execute=false`, `production_verified=false` and terminal label are **non-authoritative research receipt metadata**, not a signed call to the V17 terminal reducer. This probe has no fitted model, calibration, qualifier, market-feed access, write permission or pipeline for publishing probabilities. Nothing here changes or asserts production routing.

## Typed research diagnostics — not governed scoring codes

Research-only statuses include `DISABLED`, `KEY_MISSING`, `CONSENT_MISSING`, `HOST_IDENTITY_UNVERIFIED`, `HOST_READBACK_REQUIRED`, `MODEL_IDENTITY_UNVERIFIED`, `REDIRECT_DENIED`, `AUTH_FAILED`, `PAYMENT_REQUIRED`, `RATE_LIMITED`, `UPSTREAM_FAILURE`, `HTTP_FAILURE`, `FLINT_HTTP_404_UNATTRIBUTED`, `FLINT_MODEL_COMPLETION_INCOMPLETE`, `TRANSPORT_FAILURE`, `OUTPUT_INVALID` and `SMOKE_PASSED`. They exist **only inside this disabled research probe** and are NOT added to the canonical governed scorer failure registry. They must not be mapped onto `MODEL_OUTPUT_INVALID` or `MODEL_INPUTS_INSUFFICIENT`, which belong to certified fitted scoring rather than this network transport.

No-request preflight always exits nonzero from the CLI, even with a credential; a workflow cannot confuse disabled/configured with a successful provider call. Only a valid provider-reported `SMOKE_PASSED` from `smoke` exits 0, and even that conveys no governance, security or production-verification approval. There are no automatic retries, provider/model fallbacks, paid model substitutions or claimed availability SLAs. A 30-second socket timeout and fixed response cap are local bounds, not a guaranteed process wallclock for trickle responses; any hosted workflow must provide an independent runner timeout.

## Activation is a separate protected change

For live hosted use, an independent authorized maintainer must integrate this as a **manual-only** action under the existing engineering control plane, with verified `wow-engineering` environment restriction, explicit and recorded consent, pinned dependency SHAs, limited permissions, exact source provenance, external-host egress controls, and fail-closed tests for credential/redirect/identity races. That Class P change must pass correct-App exact-head regressions, negative/security/concurrency tests and authenticated independent sign-off before merging. The user need not install a Mac CLI or expose the credential in chat. The present PR installs no workflow.

The earlier rejected issue-driven worker, inert draft workflow, and downstream handoff were removed rather than treated as approved; their original signed security verdicts are preserved. No Flint engineering agent or sportsbook model is operational as a result of this PR.
