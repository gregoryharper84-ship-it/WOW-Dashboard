# WOW V17 — OpenRouter Recursive Flint: opt-in synthetic canary

Status: source-only experiment; **not merged, not enabled, no live API call verified**.

## Purpose

This pilot tests the OpenRouter-hosted `recursive/flint-v1.0:free` API without downloading Flint to the owner's 18 GB M3 Pro Mac. The canary only sends one fixed public synthetic coding prompt. It does not send repository content, private issues, logs, code, credentials or betting data. It cannot propose a patch, publish probabilities, create a PR, grant a writer lease, dispatch an engineering worker, merge, deploy, or place a wager.

## Owner's minimal setup

1. Visit https://openrouter.ai/ and sign up or log in. Use the **Free** plan. No paid plan is required to test free Flint.
2. Open https://openrouter.ai/settings/keys and create a dedicated API key named `WOW-V17-Flint-Pilot`. Choose a conservative spending limit where available. Do **not** paste it into ChatGPT, a GitHub issue, source code, or a PR.
3. For a one-time Mac test, run the steps below in Terminal while located at the repo root. They use your Mac's current shell memory only; no .env file is created. The key will stay in the shell until explicitly unset.

   ```zsh
   cd ~/WOW-Dashboard
   read -s 'OPENROUTER_API_KEY?Paste OpenRouter key (hidden): '
   echo
   export OPENROUTER_API_KEY
   export WOW_FLINT_PROBE_ENABLED=true
   python3 artifacts/wow-engine/v17/engineering_openrouter_flint_probe.py preflight
   python3 artifacts/wow-engine/v17/engineering_openrouter_flint_probe.py smoke
   unset OPENROUTER_API_KEY WOW_FLINT_PROBE_ENABLED
   ```

   **Important:** This script must first be obtained from its reviewed experiment branch or subsequently merged repo; it is not on current `main`. If your local checkout has unrelated uncommitted changes, do not switch branches without preserving them.
4. Inspect only sanitized status, not the full provider response. `SMOKE_PASSED` confirms a single synthetic completion, **not** a running engineering agent or a repaired model.
5. After approved integration (separate work), store `OPENROUTER_API_KEY` as a GitHub Actions **environment secret** in the existing `wow-engineering` environment. Do not store this credential in Render or Supabase unless a reviewed worker requires it. Keep automatic Flint routing disabled until independent reliability acceptance.

## Free-tier considerations

OpenRouter Free currently advertises 50 free-model requests/day and 20/minute; limits and model availability may change. A free hosted model is not an uptime or production availability SLA. At most **one** opt-in live request is sent by this script; no retries. Do not automatically switch to a paid model. HTTP 429 and 402 produce diagnostic holds, not provider substitutions.

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
