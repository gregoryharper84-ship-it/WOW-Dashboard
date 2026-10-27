# WOW V17 immutable prediction-receipt research projection

**Scope:** Class A/B read-only SELECT expansion in the existing authenticated `/v17/prediction-receipts/lookup` endpoint. **No change** to input identity filters, canonical Score Pick Request, Action authentication, probability calculation, calibrated bounds, terminal authority, persistence, model registry, wager operations or `can_execute=false`.

## Defect and verified evidence

The accuracy controller PR #2142 requires exact frozen model lineage for any meaningful champion/challenger replay. Existing `wow_predictions` already persists useful metadata (model family, artifact version/checksum, certification, specialist version, feature snapshot hash, training hashes). On 2026-10-26 a read-only `information_schema.columns` query in the connected WOW **validation** project verified the added SQL projection columns exist, but the installed lookup endpoint omitted them.

This patch adds only those existing columns to `_SELECT_FIELDS` so the authenticated endpoint can return them inside its normal `prediction` object. Existing identity-conflict checks, `is_immutable_pregame`, exact-once recovery and authorization remain unchanged. No new endpoint or privileged database client is introduced. Projection tests verify the fields round-trip and `can_execute=false` remains enforced.

## Critical gaps preserved

- `wow_predictions` does **not** expose a trustworthy canonical athlete `entity_id` or separately certified `period` column in the verified validation schema. Do not invent either by interpreting display labels or defaulting to full game.
- `7+` and `8+` are literal at-least offers; the numeric prediction line is not authority for a PrizePicks exact threshold without a separately certified adapter.
- A double-double requires its own compound fitted model route and exact settlement.
- Model metadata in a row is *evidence*, not automatic certification. Independent model artifact registration, actual prediction/snapshot receipts, official settlement and Class C calibration/replay requirements remain.
- Column verification was performed against validation, not an independently authenticated live production read. Before deployment, confirm the production schema is compatible and run the exact endpoint regression.
- Caller must possess existing WOW Action API bearer credential; never introduce a service-role key into the Custom GPT editor.

**Status:** Proposed source change pending exact-head CI/QA/Release, merge, authenticated canary and deployed commit proof. `V17_TERMINAL_REDUCER` is unchanged and `can_execute=false`.
