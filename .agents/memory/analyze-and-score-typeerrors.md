---
name: analyze-and-score TypeErrors
description: Two pre-existing TypeErrors in /analyze-and-score that caused BACKEND_PIPELINE_FAILURE on every real screenshot call, and how they were fixed.
---

# /analyze-and-score TypeError fixes

## Rule
After calling `_normalize_legs(...)`, immediately convert results to mutable dicts. Also use `(row.get(key) or "")` not `row.get(key, "")` before any slice/subscript operation.

## Bug 1 — NormalizedRow item assignment
`_normalize_legs` returns `NormalizedRow` objects which implement `Mapping` (read-only). The gap-fill step tried to mutate them (`row["player_name_resolved"] = ...`), raising `TypeError: 'NormalizedRow' object does not support item assignment`.

**Fix:** `norm_rows = [dict(r) for r in _normalize_legs(...)]` immediately after normalization in `analyze_and_score` (~line 3511).

## Bug 2 — None-safe slice in _norm_to_pipeline_row
`norm_row.get("game_time", "")[:10]` crashes with `TypeError: 'NoneType' object is not subscriptable` when `game_time` key exists but has a `None` value — `.get(key, default)` only uses the default when the key is **absent**, not when it's `None`.

**Fix:** `(norm_row.get("game_time") or "")[:10]` (~line 3955 in `_norm_to_pipeline_row`).

**Why:** Any field that can be `None` in a `NormalizedRow` dict and is also subscripted/sliced must use the `or ""` guard pattern, not the `.get(key, "")` default.

**How to apply:** Whenever adding a `[:N]` slice on a dict `.get()` call in `_norm_to_pipeline_row` or similar conversion functions, always use `(row.get(key) or "")[:N]`.

## E2E result after fixes
- Synthetic PrizePicks PNG (390×844, 5 NBA legs) → HTTP 200, `props_extracted=5`
- All 5 legs: `SLATE_PURGE:NO_SLATE_DATE` — expected (synthetic image has no game time)
- Claude extracted player names correctly via legacy platform proxy (Anthropic)
- `can_execute: false` unconditional on all legs ✓
