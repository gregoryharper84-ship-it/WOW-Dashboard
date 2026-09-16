# WOW V17 Custom GPT editor synchronization

Updated: 2026-09-15

Status: **LIVE_EDITOR_SYNC_PENDING**

Repository changes, protected CI, Render/backend deployment, and production Action replay do **not** update the live Custom GPT editors. Editor synchronization is a separate product-configuration step.

## Current state

- `BACKEND_RUNTIME`: remains independently determined by the governed backend.
- `MODEL_CAPABILITY`: remains route-specific and must preserve the backend's exact typed status.
- `REPOSITORY_GOVERNANCE`: current code/instruction changes may be merged and CI-green independently of editor state.
- `LIVE_GPT_EDITOR_SYNC`: **PENDING** until the production editors contain the current canonical instructions/actions and are verified after reload.

The canonical editor sources are:
- WOW: `artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt`
- LLP: `artifacts/wow-engine/LLP_V17_CUSTOM_GPT_INSTRUCTIONS.txt`

## LLP live-editor sync evidence

Operator-reported live verification on 2026-09-15 confirms that the new LLP editor-sync governance block was added to the production LLP Custom GPT, saved, and verified after reload. That block preserves independent status reporting and `can_execute=false`.

This is **not** sufficient to mark the full canonical editor sync verified. The live LLP instructions still differ from the canonical source and currently end mid-sentence in the TheRundown section, and the production Actions configuration has not yet been verified against the canonical Action definition.

Therefore:
- requested LLP editor-sync governance addition: **VERIFIED AFTER RELOAD**
- canonical LLP instruction parity: **PENDING**
- LLP production Actions verification: **PENDING**
- full `LIVE_GPT_EDITOR_SYNC`: **PENDING**
- full LLP stack `FIXED_VERIFIED`: **NO**

The LLP canonical source explicitly requires this separation. A repository/backend fix may be reported as verified at those layers while editor synchronization is still pending, but the full LLP stack must not be called `FIXED_VERIFIED` until the production LLP editor has the current canonical instructions/actions saved and verified after reload.

A pending or failed editor sync is a product/admin state. It must never be rewritten as `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, `MODEL_OUTPUT_INVALID`, or another sporting-model result.

## Live-editor verification checklist

After saving the current canonical instructions/actions in each affected production editor, reload and verify:
1. V17 identity/terminal authority and `can_execute=false` remain intact.
2. WOW continues to own player/scalar props; LLP continues to own team/event winners/favorites/underdogs/upsets.
3. Full Model team/event requests still require the canonical Action attempt and preserve exact backend typed failures.
4. `BACKEND_RUNTIME`, `MODEL_CAPABILITY`, `REPOSITORY_GOVERNANCE`, and `LIVE_GPT_EDITOR_SYNC` are reported independently.
5. A pending editor sync does not become `MODEL_UNAVAILABLE`.
6. The full canonical LLP instruction text matches the repository source after reload, including the complete TheRundown section.
7. Production LLP Actions are verified against the canonical Action definition after reload.
8. The full stack is called `FIXED_VERIFIED` only after all canonical instruction and Action checks pass.

Do not manufacture a betting recommendation merely to test editor synchronization. Governance/health prompts and deliberately unsupported routes are sufficient acceptance checks.
