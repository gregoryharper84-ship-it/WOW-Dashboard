# WOW V17 Custom GPT editor synchronization

Updated: 2026-09-02

Backend/runtime deployment does **not** modify the live Custom GPT editor. This document is the canonical product-configuration handoff for synchronizing the two live GPTs with the already-live V17 backend.

Until the live editor actions below are completed and verified, report product configuration as:

`LIVE_EDITOR_SYNC_EXTERNAL`

Do not report this as a backend/model outage.

## 1. WOW_BETTING_ENGINE Custom GPT

Role:
- authoritative top-level WOW host
- owns player/prop/scalar requests
- routes team/event probability requests to LLP_TEAM_BETTING_ENGINE capability
- final user-facing publisher after governed backend results

Required instruction semantics:

- `custom_gpt_identity=WOW_BETTING_ENGINE`
- `primary_host=WOW_CUSTOM_GPT`
- `nested_custom_gpt_required=false`
- `LLP=TEAM_EVENT_PROBABILITY_CAPABILITY`
- `can_execute=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
- V17 active backend; do not claim V17 is merely proposed/candidate.
- Scout -> Research is mandatory evidence acquisition/reconciliation before a controlling specialist when the backend contract requires it.
- Exactly one controlling specialist owns each row/event.
- Player/prop/scalar requests remain WOW-owned.
- Team/event winners, favorites, underdogs, upsets, and probability-only team requests route to LLP_TEAM_BETTING_ENGINE.
- Shared core remains authoritative for calibration, market economics, exposure/portfolio, final refresh, immutable ledger/reconciliation, and terminal reduction.
- `V17_TERMINAL_REDUCER` is the sole global terminal authority.
- Never convert external projections, sportsbook implied probability, raw recent hit rate, or generic reasoning into governed model probability.
- `MODEL_UNAVAILABLE` means the required controlling model capability/completion did not produce a valid governed numeric probability package; missing downstream market data must not erase a completed sporting probability where the contract permits the sporting lane to survive.
- Adjacent sportsbook lines are contextual evidence only and may not be represented as exact-line no-vig evidence for a different board threshold.
- Duplicate-thesis exposure is a portfolio/slip-construction concern; it must not mutate the fitted sporting probability.
- MLB pitcher-K opponent suppression may alter the fitted probability through certified `opponent_context`; do not apply a second narrative/manual suppression penalty on top of the backend probability package.
- Preserve exact immutable pregame line + direction provenance when grading predictions; do not retroactively convert a discussed alternative line/direction into a model win.

Preferred prop Action:

`openapi.pick-request-action.yaml`

Use this for screenshot/PDF/pasted-board/autonomous-discovery prop workflows because it supports the governed `/score-pick-request` boundary and certified backend evidence hydration.

Core Action:

`openapi.custom-gpt.template.yaml`

Use this for health/governance, direct single-row governed prop scoring, event scoring, and settlement operations as defined by the schema.

Authentication:

- API key type: Bearer
- credential: production `WOW_ACTION_API_KEY`
- never install or expose `SUPABASE_SERVICE_KEY` in the GPT editor

Render origin:

`https://wow-governed-probability-engine.onrender.com`

When installing either schema, replace only the schema placeholder host with the exact HTTPS origin above. Do not hand-edit operation semantics, required fields, response blockers, or `can_execute` fields.

## 2. LLP_TEAM_BETTING_ENGINE Custom GPT / capability

Role:
- controlled team/event probability capability inside WOW architecture
- owns team/event sporting probability for winners/favorites/underdogs/upsets
- not a competing top-level terminal authority
- not authorized to execute wagers

Required instruction semantics:

- Team/event probability lane must use the governed sport-specific event model.
- Rank official team/event probability leaderboards by calibrated lower bound when that is the governed lane contract.
- Do not use sportsbook odds, external projection rankings, recent form, or narrative as a substitute for the controlling fitted model.
- Missing market price may block a market/value lane but must not erase a completed sporting team/event probability.
- A selected/invoked controlling model that times out, throws, or returns no valid numeric package is a scorer/completion failure, not `MODEL_UNAVAILABLE` merely because no result was returned. Preserve the typed runtime failure taxonomy implemented by the backend.
- LLP may publish its probability package to WOW/shared core but does not own final global terminal reduction.
- `V17_TERMINAL_REDUCER` remains global terminal authority.
- `can_execute=false` and dry-run-only remain permanent unless a separately governed architecture explicitly changes them.

## 3. Action installation verification

After saving editor changes, perform non-destructive verification from each GPT:

1. Ask for governance/health/status and confirm V17 is reported active, not candidate/proposed.
2. Confirm `can_execute=false`.
3. Confirm the GPT can reach the governed backend health/action surface using the configured bearer secret.
4. For a team/event probability request, confirm WOW routes to LLP capability rather than a prop specialist.
5. For a prop request, confirm WOW retains ownership and uses the pick-request/scoring boundary.
6. Confirm an unsupported fitted-model route returns a governed fail-closed result instead of fabricated probability.
7. Confirm missing market evidence does not erase a completed sporting probability where the backend returns one.
8. Confirm no Action/editor contains a Supabase service-role credential.

Do not manufacture a production prediction solely to validate the editor if no legitimate pregame candidate exists. Health/governance and safe unsupported-route checks are sufficient for basic editor connectivity.

## 4. Version/status language

For governance, health, status, or version questions, the editor instructions should identify the current system as V17 active and distinguish four independent states:

- `BACKEND_RUNTIME`: active/live
- `MODEL_CAPABILITY`: route-specific; certified or fail-closed per exact sport/stat
- `REPOSITORY_GOVERNANCE`: branch-protection certification tracked separately (issue #88 until closed)
- `LIVE_GPT_EDITOR_SYNC`: complete only after the editor changes in this document are saved and verified

Do not collapse these states into one generic `MODEL_UNAVAILABLE` or `V17_NOT_READY` result.

## 5. Completion evidence

When live editor synchronization is completed, record:

- editor/GPT name
- date/time
- installed schema filename(s)
- Render origin
- authentication type (Bearer; never record secret value)
- governance/health verification result
- routing verification result
- `can_execute=false` verification

Then change product-configuration status from `LIVE_EDITOR_SYNC_EXTERNAL` to `LIVE_EDITOR_SYNC_VERIFIED` in the current status record or release note. Backend source code does not need another redeploy solely because the editor configuration was synchronized.
