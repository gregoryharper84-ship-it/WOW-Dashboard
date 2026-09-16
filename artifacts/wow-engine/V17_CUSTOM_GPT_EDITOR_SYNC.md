# WOW V17 Custom GPT editor synchronization

Updated: 2026-09-16

Status: **LIVE_EDITOR_SYNC_PENDING**

Repository changes, protected CI, Render/backend deployment, and production Action replay do **not** update the live Custom GPT editors. Editor synchronization is a separate product-configuration step.

## Current state

- `BACKEND_RUNTIME`: remains independently determined by the governed backend.
- `MODEL_CAPABILITY`: remains route-specific and must preserve the backend's exact typed status.
- `REPOSITORY_GOVERNANCE`: current code/instruction changes may be merged and CI-green independently of editor state.
- `LIVE_GPT_EDITOR_SYNC`: **PENDING** until the production editors are saved with the current canonical instructions/actions and verified after reload.
- `USER_JOURNEY_HEALTH`: **FAIL** until a real ChatGPT-originated Full Model request crosses the canonical Action boundary and returns governed row-level results or an exact typed Action-attempt failure.

The canonical editor sources are:
- WOW: `artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt`
- LLP: `artifacts/wow-engine/LLP_V17_CUSTOM_GPT_INSTRUCTIONS.txt`
- WOW Action schema: `artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml`

The LLP canonical source explicitly requires this separation. A repository/backend fix may be reported as verified at those layers while editor synchronization is still pending, but the full stack must not be called `FIXED_VERIFIED` until the production editor has the current canonical instructions/actions saved, reloaded, and canary-tested.

A pending or failed editor sync is a product/admin state. It must never be rewritten as `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, `MODEL_OUTPUT_INVALID`, or another sporting-model result.

## Live-editor verification checklist

After saving the current canonical instructions/actions in each affected production editor, reload and verify:
1. V17 identity/terminal authority and `can_execute=false` remain intact.
2. WOW continues to own player/scalar props; LLP continues to own team/event winners/favorites/underdogs/upsets.
3. The production WOW editor exposes `scoreWowV17PickRequest` for `POST /score-pick-request` against `https://wow-governed-probability-engine.onrender.com` using the configured bearer credential.
4. A still-pregame supported prop request made from the live ChatGPT host actually attempts the Action. `scoring_attempted=true` is permitted only after the Action boundary was crossed.
5. Success requires a canonical row-level Action receipt; a typed post-invocation failure is acceptable evidence that the Action boundary is live. A host-local reconstruction, local replay, capability preflight, or generic health prompt does not satisfy this requirement.
6. `BACKEND_RUNTIME`, `MODEL_CAPABILITY`, `REPOSITORY_GOVERNANCE`, `LIVE_GPT_EDITOR_SYNC`, and `USER_JOURNEY_HEALTH` are reported independently.
7. A pending editor sync does not become `MODEL_UNAVAILABLE`.
8. The full stack is called `FIXED_VERIFIED` only after save + reload + real user-path canary verification.

The acceptance canary is dry-run only. It must preserve `can_execute=false` and must never place, route, modify, approve, or cancel a wager/order.
