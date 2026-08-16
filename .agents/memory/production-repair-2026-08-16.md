---
name: Production repair 2026-08-16
description: Six-patch R1→R3c chain fixing RUN_PARTIAL_BACKEND_FAILURE and direct_game_log_feed=NOT_CALLED for MLB batter props in live GPT sessions.
---

## R1 (b97eca1) — Six structural defects
Schema migration ordering, moneyline team acquisition, exposure ledger idempotency,
snapshot refresh, Stage 2 schema repair endpoint, quota table sync.

## R2 (e38da2a) — Accent-strip + active fetch
`_lookup_mlb_player_id` failed on accented names; `_check_prop_game_log` was
advisory-only (never fetched). Both fixed. Deployed — still showed NOT_CALLED.

## R3 (eaf8cc0) — Enrichment identity + in-pipeline acquisition
Two bugs:
- `pipeline.py`: `enrichment = enrichment or {}` replaced caller's dict with
  a new private object; fixed to `if enrichment is None: enrichment = {}`.
- Pre-pipeline orchestrator keyed writes by player name; `normalize_board()`
  generated a fresh uuid4 rid that never matched. Fixed by adding the fetch
  INSIDE the pipeline loop (after canonical rid is known), using a per-row
  scratch dict (`_row_enr`) so the batch enrichment dict is not touched before
  the market-join audit.

## R3b (a025cdd) — Preserve batch enrichment audit semantics
R3 wrote `enrichment[rid] = enr` for every row before market-join audit,
inserting unexpected keys. Changed to per-row scratch dict; fetched fields
merged into `enr` in-place via `enr.update(_fetched)`.

## R3c (e2d6deb) — Canonical stat_key via normalizer ← FINAL ROOT CAUSE
`_MLB_STAT_FIELDS` uses short uppercase keys (`"H"`, `"K"`, `"PA"`).
`normalize_board()` copies `prop_type` verbatim from GPT payload
("Hits", "hits", "hitter hits"). `_fetch_mlb` raised `GameLogUnavailable`
for any non-canonical string; `_attempt_game_log_fetch` caught silently,
returned None → `NOT_CALLED`.

Fix: lazy-import `normalizer._resolve_stat_key(prop_type, sport)` in the
R3 in-pipeline block and use the canonical form for the fetch.

**Why:** normalizer's alias table already maps all common display labels to
canonical stat_key; _fetch_mlb's dict does not and never should (it is a
low-level MLB API mapping, not a display-label resolver).

**How to apply:** any code path that takes user-supplied `prop_type` and
passes it to `fetch_game_log` / `_fetch_mlb` MUST resolve through
`normalizer._resolve_stat_key` first. Direct uppercase of the raw string
is NOT sufficient ("HITS" ≠ "H").

## Persistent runtime sentinel
`uac_b9_readiness_ruling.json` is rewritten by the running gunicorn worker
on every deploy. The git-diff sentinel test always sees it as dirty during
test runs. Commit it after each run as housekeeping; it is not a code defect.
