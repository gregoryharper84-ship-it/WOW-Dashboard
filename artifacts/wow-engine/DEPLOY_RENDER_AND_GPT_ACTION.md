# WOW governed probability backend — Render + Custom GPT Action release runbook

This runbook deploys `artifacts/wow-engine` as the separate
`EXTERNAL_GOVERNED_BACKEND`. It does **not** replace the existing Replit scoring
runtime and it does **not** authorize betting/trading execution.

Hard invariants throughout this runbook:

- `governed_probability_capability = UNAVAILABLE` until the full live-host deployment gate passes.
- `Section_8A = ACTIVE` while that capability is unavailable.
- `can_execute = false`.
- No missing fitted sport/stat parameters may be invented or qualitatively substituted.

## 1. Create the Render service from the Blueprint

Use the repository-root `render.yaml` as the Blueprint and deploy from the default
`main` branch. The service is intentionally configured with automatic deploys off;
future releases should follow the same reviewed PR/CI/merge/deploy sequence.

During initial Blueprint creation, Render will prompt for the three `sync: false`
environment variables. Enter them directly in Render; never place their values in
Git, issue comments, chat messages, screenshots, or logs:

- `SUPABASE_URL` — validation/production Supabase project URL appropriate for this release.
- `SUPABASE_SERVICE_KEY` — backend-only Supabase service-role credential.
- `WOW_ACTION_API_KEY` — independent application-layer credential used only by the Custom GPT Action.

The `WOW_ACTION_API_KEY` and the Supabase service-role key must be different secrets.
The Custom GPT must never receive the Supabase service-role key.

Expected service configuration is supplied by `render.yaml`:

- service name: `wow-governed-probability-engine`
- runtime: Python
- Python version: 3.11.11
- root directory: `artifacts/wow-engine`
- build: `pip install -r requirements.txt`
- start: `uvicorn api:app --host 0.0.0.0 --port $PORT`
- health path: `/health`
- free instance tier
- automatic deploys: off

Record the exact HTTPS Render service origin after the first successful deploy.
Do not guess the hostname.

## 2. Live-host validation before enabling governed probability

First verify the public endpoints on the deployed Render origin:

- `GET /health` returns HTTP 200 and identifies `EXTERNAL_GOVERNED_BACKEND` / Render / Supabase.
- `GET /governance` returns HTTP 200 and still reports
  `governed_probability_capability = UNAVAILABLE`.

Then perform the governed live-host validation against the real Supabase project.
The existing validator documents its own prerequisites and cleanup contract in
`scripts/live_gate_validation.py`. Credentials belong in environment variables,
never command-line arguments or chat text.

Do not flip `governed_probability_capability` based only on host reachability or
authentication. The patch's complete 11-point deployment gate remains controlling,
and the fitted sports lane remains a separate Step 6 requirement.

## 3. Configure the Custom GPT Action

Start from `openapi.custom-gpt.template.yaml`.

1. Replace only the template server origin with the exact HTTPS Render origin.
2. In the GPT editor, create a new Action and provide the resulting OpenAPI schema.
3. Configure authentication as **API key → Bearer**.
4. Enter the same `WOW_ACTION_API_KEY` value that is stored in Render. Do not enter
   the Supabase service-role key.
5. Test `getWowProbabilityHealth` and `getWowProbabilityGovernance` in Preview.
6. While governance reports `UNAVAILABLE`, the GPT must not call `scoreWowProp` as
   a way to obtain a probability. A protected test call may return the governed
   409 hold; that is expected and must not be bypassed.
7. `settleWowPrediction` is for verified official results on existing prediction
   IDs only; it is not a betting/trading action.

The GPT's instructions should explicitly require a governance check before governed
probability scoring and preserve the manual/fallback lane whenever capability is
unavailable.

If the GPT is later shared publicly, satisfy any current ChatGPT Action-domain and
privacy-policy requirements before publication.

## 4. Promotion boundary

Only after the live-host deployment gate is independently clean may the project
consider changing the governed probability capability. Even then, Step 6 remains
substantive: real per-sport cohort/regime/simulation parameters must be fit from
actual historical data and validated lane-by-lane. Hosting and authentication do
not create a fitted model.

A valid release may therefore end in this state:

```text
governed_probability_capability = UNAVAILABLE
Section_8A = ACTIVE
Custom GPT Action = CONNECTED
host/auth = PASS
real fitted sports lane = NOT YET AVAILABLE
can_execute = false
```
