---
name: Claude extraction prompt verbatim-label gap
description: Claude's analyze-and-score extraction prompt silently drops unusual prop labels — fix and pattern for future prop additions.
---

# Claude extraction prompt — verbatim prop label gap

## The rule
The `extract_prompt` in `app.py` at the `/analyze-and-score` handler MUST explicitly tell Claude to copy prop label text verbatim, with unusual examples listed inline. Without this, Claude returns `prop=""` for any non-standard label (e.g. "1st Inn. Pitches Thrown", "Fantasy Score") and the normalizer never gets a chance to map it.

**Why:** Claude's default behavior for `"prop": stat category (examples...)` is to normalize/simplify what it sees. It may skip or return empty when the board text doesn't match the simple examples. A two-layer fix is required:
- **Layer 1 (upstream)**: extraction prompt must say "EXACT text verbatim" with unusual examples explicitly listed.
- **Layer 2 (downstream)**: normalizer `_STAT_KEY_MAP["MLB"]` must have aliases for each display variant → canonical stat_key.

Both layers are needed: the extraction prompt delivers the raw label; the normalizer converts it to the canonical key that downstream gates recognize.

**How to apply:** Any time a new unusual prop type is added to the system (e.g. a new PrizePicks category, a Kalshi-adjacent MLB prop), verify:
1. The extraction prompt in `app.py` at `/analyze-and-score` includes a representative example of the label text.
2. The normalizer's `_STAT_KEY_MAP` has all display-label variants → canonical stat_key.
3. `route_registry.PROP_TYPE_REQUIRED_GATES` has the canonical key registered.

## Current state (as of 2026-08-07)
- `extract_prompt` updated to say "EXACT prop type label shown on the card — copy it verbatim" with examples including "1st Inn. Pitches Thrown", "Fantasy Score", "Total Bases", "Hitter Fantasy Score".
- `_STAT_KEY_MAP["MLB"]` has 11 aliases for the 1st-inning-pitches family → `"1IP_PITCHES_THROWN"`.
- `route_registry.PROP_TYPE_REQUIRED_GATES["1IP_PITCHES_THROWN"]` already existed (requires `calibration_health` gate).

## Confirmed E2E
Before: `board.prop_type=""`, all 4 legs → `DATA_CONTRACT_FAIL:missing_field:prop_type`.
After: `board.prop_type="1IP_PITCHES_THROWN"`, all 4 legs route past the data-contract check into real data-gap signals (`L10:NO_GAME_LOG_PROVIDED`, `model_status=NO_REGISTERED_MODEL`).
