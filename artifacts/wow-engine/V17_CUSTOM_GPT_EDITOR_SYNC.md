# WOW V17 Custom GPT editor synchronization

Updated: 2026-09-02

Backend/runtime deployment does **not** modify the live Custom GPT editors. This is the canonical product-configuration handoff for synchronizing the live WOW and LLP editors with the already-live V17 backend.

Until both editors are saved and verified, report product configuration as `LIVE_EDITOR_SYNC_EXTERNAL`. Do not report editor drift as a backend/model outage.

## Canonical production Action schemas

Install the V17 source contracts, not the older generic templates:

- WOW editor: `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml`
- LLP editor: `artifacts/wow-engine/v17/openapi.llp-team-engine.v17.yaml`

Both schemas already point at:

`https://wow-governed-probability-engine.onrender.com`

Do not replace these schemas with `openapi.custom-gpt.template.yaml` or `openapi.pick-request-action.yaml` for final V17 editor synchronization. Those remain useful backend/general contracts, but the `v17/` schemas are the production host-specific editor contracts.

Authentication for both editors:

- API key type: Bearer
- credential: production `WOW_ACTION_API_KEY`
- never install, paste, expose, or log `SUPABASE_SERVICE_KEY`

Do not hand-edit operation semantics, required host identities, response blockers, or `can_execute` fields in the V17 schemas.

## WOW_BETTING_ENGINE editor

Install instructions from:

`artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt`

Required semantics include:

- `custom_gpt_identity=WOW_BETTING_ENGINE`
- authoritative top-level WOW host
- owns player/prop/scalar intelligence
- team/event winners, favorites, underdogs, upsets, and probability-only team requests route through the backend to `LLP_TEAM_BETTING_ENGINE` as controlling engine
- exactly one controlling specialist per row/event
- Scout -> Research is evidence/reconciliation only and never substitutes for the fitted specialist
- shared core owns calibration, market economics, portfolio/exposure, final refresh, immutable write/reconciliation, and terminal reduction
- `V17_TERMINAL_REDUCER` is the sole global terminal authority
- `can_execute=false`
- no live wager/order execution

The WOW V17 Action schema exposes the host-specific V17 route surface, including health/governance, `/v17/host-contract`, prop scoring, canonical pick-request scoring, and WOW-originated team/event ingress whose backend controlling engine must resolve to LLP.

## LLP_TEAM_BETTING_ENGINE editor

Install instructions from:

`artifacts/wow-engine/LLP_V17_CUSTOM_GPT_INSTRUCTIONS.txt`

Required semantics include:

- controlled team/event sporting-probability capability inside WOW
- owns winners/favorites/underdogs/upsets/team-event probability
- has no player-prop scoring authority
- uses the exact governed sport/event fitted model and calibration contract
- official probability ranking uses calibrated lower bound where required by the active lane
- missing market price may block market/value analysis but must not erase a completed sporting probability
- model invocation/scorer/output failures retain the backend typed failure taxonomy and must not be rewritten into fabricated probabilities
- LLP does not own global terminal reduction
- `V17_TERMINAL_REDUCER` is global terminal authority
- `can_execute=false`

The LLP V17 schema intentionally contains no player-prop scoring operation.

## Postmortem semantics that must survive editor sync

- MLB pitcher-K `opponent_context` may alter the fitted sporting distribution through the certified opponent factor; do not apply a second manual/narrative suppression penalty after backend scoring.
- Adjacent sportsbook lines are context only and cannot satisfy exact-line no-vig authority for a different board threshold.
- Duplicate-thesis exposure is a portfolio/card-construction risk only; it must not mutate model probability, calibrated probability, or calibrated lower bound.
- Immutable pregame line + direction provenance controls grading. Do not retroactively count a materially different discussed line/direction as a governed prediction win.
- Unsupported fitted-model routes stay fail closed. External projections, sportsbook implied probabilities, hit rates, or generic reasoning may not be relabeled as governed model output.

## Non-destructive editor verification

After saving each editor:

1. Ask for governance/health/status and confirm V17 is reported active rather than proposed/candidate.
2. Confirm `can_execute=false`.
3. Call the authenticated `/v17/host-contract` operation from the installed Action and confirm the expected host/lane identity.
4. From WOW, verify a team/event request resolves LLP as controlling engine and a prop request remains WOW-owned.
5. From LLP, verify no player-prop scoring Action is exposed.
6. Verify an unsupported fitted-model route returns a governed fail-closed result rather than fabricated probability.
7. Verify missing market evidence does not erase an already-completed sporting probability where the backend contract preserves it.
8. Confirm neither editor contains a Supabase service-role credential.

Do not manufacture a production betting recommendation merely to test editor connectivity. Health/governance/host-contract and safe fail-closed checks are sufficient for basic synchronization acceptance.

## Status language

Keep these independent:

- `BACKEND_RUNTIME`: V17 active/live when backend confirms it
- `MODEL_CAPABILITY`: exact route-specific certified support or governed fail-closed state
- `REPOSITORY_GOVERNANCE`: branch-protection certification tracked separately by issue #88 until effective protection is verified
- `LIVE_GPT_EDITOR_SYNC`: complete only after both live editors have saved and verified the V17 schema + instructions

Do not collapse repository/editor status into `MODEL_UNAVAILABLE` or `V17_NOT_READY`.

## Completion evidence

For each editor record:

- GPT/editor name
- synchronization date/time
- exact V17 schema filename
- Render origin
- Bearer authentication configured (never record the secret value)
- governance/health result
- `/v17/host-contract` result
- routing/ownership verification
- `can_execute=false` verification

After both editors pass, change product-configuration status from `LIVE_EDITOR_SYNC_EXTERNAL` to `LIVE_EDITOR_SYNC_VERIFIED`. No backend redeploy is required solely because editor configuration was synchronized.
