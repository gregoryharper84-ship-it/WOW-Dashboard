---
name: WOW pipeline row_id desync pattern
description: Why any pre-pass that normalizes rows before run_pipeline() must carry row_id back onto raw_rows, or enrichment silently fails to attach.
---

# row_id desync between a pre-pass and run_pipeline()

`run_pipeline()` (`gate_engine/pipeline.py`) always calls `board_intake.normalize_board(raw_rows)` internally on whatever `raw_rows` it's given. `normalize_row` mints a random `uuid4`-based `row_id` whenever the row doesn't already carry one.

**Why this matters:** any caller/pre-pass (e.g. an auto-enrichment step) that normalizes rows *before* calling `run_pipeline()` — in order to know each row's row_id and key enrichment data by it — will get a DIFFERENT row_id than the one `run_pipeline()` generates internally, unless the pre-pass writes the generated row_id back onto the original `raw_rows` dicts before calling `run_pipeline()`. Otherwise the enrichment dict is keyed by an id that pipeline's own internal `_get_enrichment()` will never see, and it silently fails to attach (may appear to work by accidental fallback to a `player:prop` string key, masking the bug).

**How to apply:** after any pre-pass `normalize_board()` call whose row_ids will be used as enrichment keys, do `for raw, normalized in zip(raw_rows, normalized_rows): raw["row_id"] = normalized["row_id"]` so the row_id is stable across both normalization passes. Prove it with an end-to-end test that runs the full pre-pass → `run_pipeline()` chain and asserts `result["prop_ledger"][i]["row_id"] == generated_row_id` (a pure unit test on the pre-pass alone won't catch this class of bug, since it's a bug between two consumers, not within either).

Also note: `enrichment` in this pipeline can legitimately be keyed by EITHER `row_id` OR `"player:prop"` (lowercased) — `_get_enrichment()` checks row_id first, falls back to the key. A write-priority scheme that always prefers row_id when the row_id is present in caller-supplied base data, else the player:prop key on first use, else falls back to row_id for a second row claiming an already-used key (e.g. doubleheaders/duplicate player+prop rows) avoids cross-row contamination while preserving back-compat with callers that never supply row_id.
