---
name: PP Promotion Gate & Same-Game Fragility patch
description: WOW-PATCH-2026-08-15 architecture, gate design, and implementation pitfalls across wiring commits.
---

## Rule
HIGH_PROBABILITY ≠ QUALIFIED_PAID_CARD.  The gate caps `terminal_label` at `MARKET_VERIFIED_HOLD` but never touches probability fields or leaderboard rank.

## Modules
- `gate_engine/pp_promotion_gate.py` — break-even+safety_buffer lower-bound gate, two-way no-vig (3-level fallback: explicit field → computed from American odds → cal_prob proxy), recency-shock LOO (|full−loo| ≥ 0.030 blocks)
- `gate_engine/pp_pregame_snapshot.py` — immutable write-once snapshot; write failure preserves research output but caps paid-card labels
- `gate_engine/pp_final_refresh.py` — binding 7-category material-change detector (not a warning)
- `gate_engine/prediction_ledger.py::PostmortemClassification` — 9 canonical postmortem classifications

## Governance
Patch #25, precedence 104; governance hash `ff2a9ce5...`; patch count 25.

## Pitfall 1: fatal-rejected-leg gate inside run_hard_gates()
`_gate_fatal_rejected_leg` must receive `pre_existing_reject_ids` (row_ids that had REJECT labels BEFORE the call).  Otherwise it fires on REJECT labels that sibling gates just set, overwriting them with FATAL_REJECTED_LEG_IN_CARD.

**Fix applied**: `run_hard_gates()` snapshots pre-existing reject row_ids before g1–g5a.

## Pitfall 2: fatal gate must require at least one qualifying row
When ALL rows in the batch are rejected (single-row SLATE_PURGE, etc.), there is no card.  Gate must check `qualifying_count > 0`.

## Pitfall 3: float precision at threshold boundary
`BREAK_EVEN["POWER"] + DEFAULT_SAFETY_BUFFER = 0.556 + 0.020 = 0.5760000000000001`.  Tests asserting a boundary pass must use 0.577, not 0.576.

## Pitfall 4: snapshot write must be unconditional (not gated on record_entries)
The snapshot is an immutable audit trail — NOT an exposure counter.  Gating it on `record_entries=True` means it never fires during normal GPT sessions (GPT always sends `record_entries: false`).

**Fix applied**: Removed `if record_entries:` guard from snapshot block in `pipeline.py`.  `tracker.record_entry()` and session exposure ledger remain gated on `record_entries` exactly as before.

**Why**: `record_entries=False` prevents exposure-counter accumulation on repeated scoring/QA runs.  That rule does not apply to append-only audit snapshots with UUID PKs.

## Pitfall 5: pp_final_refresh baseline required a DB read path
`build_snapshot()` had `pipeline_meta=None` → baseline field data was never written to the `pipeline_meta` JSONB column → `fetch_latest_snapshot()` didn't exist → no way to read prior snapshots as baselines.

**Fix applied** (3 parts):
1. `_BASELINE_FIELDS` frozenset in `pp_pregame_snapshot.py` — 31 field names covering all 7 refresh detector categories (lineup/participant/market/price/settlement/weather/source).
2. `build_snapshot()` populates `pipeline_meta` from `_BASELINE_FIELDS` by default (caller-supplied meta still takes precedence).
3. `fetch_latest_snapshot(conn, row_id) -> dict | None` SELECT function; reconstructs `{"sources": sources_version_dict, ...pipeline_meta_fields}`.
4. Pipeline DB baseline-fetch block before `_pp_baselines` loop; caller-supplied `enrichment[row_id]["pp_baseline"]` always overrides.

**First-run bootstrap**: no prior snapshot → vacuous pass → snapshot written → comparison live on second run.

## Pitfall 6: _snap_baseline in pipeline-level tests must match _row() exactly
`_snap_baseline` helper (in TC-PW-10 tests) must ONLY include fields that `_row()` also sets.  Including `game_time`, `odds_more`, `odds_less`, `sources` causes spurious participant/price/source-change detections in tests that assert `refresh_required_count == 0`, even when lineup_status matches.

**Fix applied**: Removed `game_time`, `odds_more`, `odds_less`, `sources` from `_snap_baseline()`. Tests that assert count=0 now use a minimal matching baseline.

## Pitfall 7: caller-override test must capture baselines dict, not infer from count
Testing "caller baseline overrides DB-fetched baseline" via `refresh_required_count == 0` is fragile — the full pipeline may transform the row in ways that create spurious mismatches. The correct isolation: patch `_refresh_mod.run` with `side_effect=capturing_run` and assert `baselines[row_id]["sentinel"] == "CALLER"` directly.

## Pitfall 8: Stage A isolation test lacked Stage A commit guard on two sub-tests
`test_prohibited_filenames_not_in_diff` and `test_hard_fail_patterns_not_in_diff` were missing the `is_stage_a_commit` guard present in `test_most_recent_commit_only_touches_allowed_files`. Any non-Stage-A commit touching `pipeline.py` falsely triggers HARD FAIL.

**Fix applied**: Both tests now skip when the HEAD commit is not a Stage A commit (i.e., does not touch `prob_ledger_enforcer.py` or `outlier_recompute.py`).

## Pipeline wiring — final state (fully operational)

**Insertion order in run_pipeline() after finalize_card():**
1. DB baseline-fetch: for each row, `pp_pregame_snapshot.fetch_latest_snapshot()` → `_pp_baselines`; caller enrichment[row_id]["pp_baseline"] overrides; best-effort (DB error → vacuous pass, not in failed_modules).
2. `pp_final_refresh.run(rows, baselines=_pp_baselines)` — vacuous on first run (no prior snapshot).
3. `pp_promotion_gate.run(rows)` — unconditional, no DB.
4. Snapshot write — unconditional (NOT gated on record_entries); own DB conn; paid-card rows only; DB error → `failed_modules`.
5. `tracker.record_entry()` — still gated on `record_entries`.

**Commits:** original spec `8caf977d`, wiring `4aa5136`, gap repair `3c6359d`, Stage A test fix `4f4d42a`.
