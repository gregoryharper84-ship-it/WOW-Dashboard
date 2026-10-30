# WOW V17 — Flint fixed-input hosted research canary

**Status: source-only, non-production, disabled by default.** This is not an active engineering agent, live worker, scorer, release controller, or GitHub Actions workflow. An earlier proposed hosted workflow and issue-driven worker were removed from this PR following independently authenticated security REJECT findings; no security verdict was waived.

## Purpose and boundary

`artifacts/wow-engine/v17/engineering_openrouter_flint_probe.py` defines a small OpenRouter test using exactly one fixed, non-secret synthetic Python question. No issue titles, repository code, private metadata, credentials in prompts, model-produced probabilities, or operational logs are sent. It never accepts an arbitrary prompt, issue, PR, file or job input. The response is not stored or returned: only redacted status plus bounded non-sensitive token usage is produced.

The allowlisted provider endpoint and model are `https://openrouter.ai/api/v1/chat/completions` and `recursive/flint-v1.0:free`; the model's presence on OpenRouter, free-tier quotas and credentials are **not currently production verified**. Each invocation is explicitly opt-in, at most one request, with a 30-second socket timeout, 65,536-byte response limit and no retry/failover. HTTP 404 is unattributed rather than treated as model unavailability; a completion without `finish_reason=stop` never passes. Bearer authorization is unredirected and cleared from the Python Request after use, including transport failures.

No local Mac installation or CLI step is required from the owner. Future hosted execution must use the existing WOW engineering control plane, not a new schedule or parallel controller. An independently reviewed **net-negative** workflow-lines protected PR is necessary before attaching this source to a manual-only hosted runner. The only approved credential location is the restricted `wow-engineering` GitHub environment, after explicit operator consent and authenticated access checks. No key is ever posted to ChatGPT or a PR.

## Nonnegotiable policies

- `custom_gpt_identity=WOW_BETTING_ENGINE`
- `runtime_generation=V17_ACTIVE`
- `terminal_authority=V17_TERMINAL_REDUCER`
- `can_execute=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`

The canary cannot create code changes, incident triage reports, reviewer decisions, deployments, market probabilities, recommendations, wagers or orders. `SMOKE_PASSED` is a transport/content smoke signal only, never independent release, model-calibration or production evidence.

## Security disposition and future authorization

Prior Flint heads `5a681f9b74d0a5167f7131070bd0378046ce734a` and `6f98073509f73d3b1e4c5b026e83c2e8338f0428` received authenticated reviewer REJECT; the intervening `444f1fc16961feed6044a9bf64be1c0454c0df3e` received a typed malformed-review HOLD. These remain preserved, not erased or reinterpreted. This removal of issue-driven worker, workflow YAML, workflow contract tests and downstream handoff is a substantive scope reduction, **not** a security bypass. The new smaller source must earn an independent exact-head verdict and pass the three protected CI checks.

A real hosted worker requires a separate protected integration change, authenticated independent security/QA/Release review, provenance tests, owner consent to any issue/title transfer, explicit manual dispatch, and readback of its actual non-writing receipt. None of these are claimed by this document.
