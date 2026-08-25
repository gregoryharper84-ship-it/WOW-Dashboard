---
name: Acquisition routing / identity plumbing patch
description: Root causes and fixes for WNBA identity handoff, enrichment merge inconsistency, market join mismatch, MLB NOT_CALLED, and image transport — 2026-08-10 patch
---

## WNBA Identity Handoff (FIX-A)
`build_packet()` in `acquisition_packet.py` did NOT copy `game` into the packet dict. `fallback_router._attempt_event_status` used `packet.get("game")` which was None → fell to `f"{team} vs {opponent}"` = `" vs "` when both were empty.

**Fix:**
- Added `game_from_row = row.get("game") or enr.get("game") or ""` to `build_packet()`
- Parse `team`/`opponent` from the game string via regex when blank: `re.split(r"\s+(?:vs\.?|@)\s+", game_str, 1)`
- Added `"game": game_from_row` to the packet dict
- `fallback_router`: guard — if `game_str` collapses to blank, return `IDENTITY_HANDOFF_ERROR` result (DATA_UNOBTAINABLE) instead of constructing `" vs "`

## WNBA Enrichment Merge Inconsistency (FIX-B)
`_validate_critical_field_value` returns `True` unconditionally for non-`role_status.*` fields.  Post-merge cleanup must be **scoped to `role_status.*` only** — filtering other fields clears them from `fields_unresolved` even when they're absent from the packet.

**Fix (evidence_acquisition.py after `_validate_packet()`):**
```python
fields_unresolved = [
    f for f in fields_unresolved
    if not (f.startswith("role_status.") and _validate_critical_field_value(f, packet))
]
```

## WNBA / General Market Join Mismatch (FIX-C)
`_get_enrichment()` and `_build_market_join_audit()` both use `prop_type.lower()` to build the enrichment key. After stat_key normalization, `prop_type = "REB"` but enrichment is keyed `"angel reese:rebounds"` → JOIN_KEY_MISMATCH.

**Fix (pipeline.py):** Both functions now also try `f"{player}:{stat_key_lower}"` as an alternate key when `stat_key_lower != prop`.

## MLB Acquisition NOT_CALLED (FIX-D)
`fetch_missing_game_logs()` had `stat_key = row.get("stat_key") or prop_type`. Display strings like "Pitcher Strikeouts" aren't in `_MLB_STAT_FIELDS` → `GameLogUnavailable` silently swallowed → `NOT_CALLED`.

**Fix (auto_enrichment.py):**
- Added `_STAT_KEY_CANONICAL` dict mapping display names → canonical keys: `"Pitcher Strikeouts"→"K"`, `"Pitching Outs"→"OUTS"`, `"Plate Appearances"→"PA"`, `"1st Inning Pitches Thrown"→"1IP_PITCHES_THROWN"`, etc.
- Added `_canonicalize_stat_key()` helper used in both `build_auto_enrichment()` and `fetch_missing_game_logs()`
- Added `_lookup_mlb_player_id(player_name)` that calls MLB Stats API `/people/search?names=...` when player_id is absent on an MLB row

## Image Transport (FIX-G)
GPT serialization may inject whitespace (newlines, spaces) into base64 payload. The stdlib `b64decode(validate=True)` rejects any non-alphabet byte.

**Fix (app.py, analyze-and-score route):** `image_base64 = "".join(image_base64.split())` before decode validation. Also added explicit IMAGE_DECODE_ERROR 422 response on failed validation instead of downstream confusion.

## IP-to-Outs Conversion
Added `ip_str_to_outs(ip_str)` to `auto_game_log.py`: `.1` and `.2` fractional parts mean 1 or 2 extra outs (not tenths of an inning). 6.1 → 19, 6.2 → 20.

## Test Suite
40 new regression tests in `gate_engine/tests/test_targeted_acquisition_patch.py`.
Final count: 5225 passing, 5 pre-existing Poisson firewall failures (spec conflict, unresolved).
