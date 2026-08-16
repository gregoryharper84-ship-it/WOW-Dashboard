---
name: Production repair batch 2026-08-16 (session tasks)
description: Summary of all task-driven fixes implemented this session; what was done and any gotchas.
---

# Session Task Batch — 2026-08-16

## Implemented (Committed)

### Batch 1 (commit e68b60f)
- **#65/#66 Keepalive**: `_keepalive_loop` is now silent on success; WARNING on failure; ERROR escalation after `_KEEPALIVE_MAX_CONSECUTIVE_FAILURES=5` consecutive failures. Added `_log_keepalive_failure()` helper.
- **#52 NO-side calibration orphan**: `settle-result` response now includes `no_side_calibration_orphan_warning` when `no_side_calibration_id` is absent.
- **#206 Pipeline enforcer wiring**: `prob_ledger_enforcer.enforce_for_label()` and `outlier_recompute.run()` wired into pipeline.py as post-loop advisory passes. Results in `prob_ledger_enforcement_report` and `outlier_recompute_report`.
- **#71 prob_ledger_incomplete flag**: `summary.prob_ledger_incomplete` (bool) and `summary.prob_ledger_incomplete_count` (int) now in pipeline return dict.
- **#150 KALSHI_REJECT_UNCALIBRATED guard**: `test_kalshi_wx_label_registry.py` (8 tests); AST-based import isolation check.

### Batch 2 (commit 2e3072b)
- **#119/#118 First-inning efficiency wiring**: `_1ip_eff.calculate_recent_1ip_efficiency_score()` called for `stat_key=1IP_PITCHES_THROWN` before `mlb_directional_firewall.run()`. Reads `pitcher_metric_flags` from enrichment. Applies MODEL_QUALIFIED_HOLD (MATERIAL band) or WATCH (SEVERE band) ceiling. Result stored in `gates.first_inning_efficiency`.
- **#74 Scan summary undercounting**: Both CAT_KEYS call sites now add `summary_counts.get("FINAL_APPROVED", 0)` to `total_final_approved` for legacy rows.

### Batch 3 (commit a275812)
- **#126 MLB AB/OBP/AVG scoring paths**: Added 9 new entries to model_registry.py (AB/AT_BATS → mlb_counting_poisson_v1; AVG/BA/BATTING_AVERAGE/OBP/ON_BASE_PCT/ON_BASE_PERCENTAGE → mlb_binary_bernoulli_v1); all PROVISIONAL.
- **#72 llp_source_snapshots error logging**: `_audit_calibration_entry_provenance()` now logs WARNING via named logger `llp_stage2_tables` on INSERT failure instead of silent `except: pass`.
- **#69 Governance snapshot pre-warm**: gunicorn `post_fork` hook now synchronously calls `GovernanceSnapshot.instance().refresh()` (5-second timeout) in each worker before serving traffic.
- **#51 NO-side tail-risk firewall test**: `test_no_side_tail_risk_firewall.py` (16 tests) confirms HIGH_PRICE_THRESHOLD=0.85, high-priced uncalibrated contracts are not playable, blocking reasons non-empty on rejection.

### Batch 4 (commit 80788b5)
- **Bugfix enforce_for_label arg order**: `enforce_for_label(ledger_payload, label, row)` — ledger is FIRST, label is SECOND. My pipeline call had them swapped; fixed.

## Critical Lessons

**enforce_for_label signature**: `enforce_for_label(ledger_payload, label, row=None)` — ledger is first positional arg, label is second. Counterintuitive but correct. Always use keyword args when calling to prevent future regressions.

**first_inning_efficiency enrichment key**: The module reads `pitcher_metric_flags` from enrichment. The enrichment key is NOT `first_inning_metric_flags`. Tier-2 booleans: `whip_increase_15pct`, `hard_hit_increase_5pp`, `chase_decrease_4pp` (all direct keys in enrichment, not nested).

**first_inning_efficiency constants**: `CEILING_HOLD = "MODEL_QUALIFIED_HOLD"`, `CEILING_WATCH = "WATCH"`, `CEILING_NONE = None`. Accessible as module attributes.

## Structurally Resolved (No Code Gap)
- **#207** (settlement re-grade): `TestPropSettlementIdempotencyDB` + `TestKalshiSettlementIdempotencyDB` (6 tests in `test_settlement_idempotency_db.py`) already cover this.
- **#54** (WNBA milestone stale): `get_unique_player_game_count()` queries DB via `COUNT(DISTINCT (player_name, event_date))` per request — not a process-local variable, worker-safe.
- **#53** (log tables in DB): `wnba_composite_forward_test_ledger`, `mlb_directional_pitcher_ledger`, `cross_ticket_exposure_log` all exist with INSERT calls from pipeline gates.
- **#63** (Odds API quota): `/wow/odds/quota-status` returns live data (HTTP 200 confirmed in deployment logs Aug 16).
- **#208** (prob_ledger enforcer): Addressed by Batch 1 (#206/#71).
- **#67** (Odds API quota cross-worker): Fixed previously (cross-worker-quota-fix.md); `ensure_table_exists` called synchronously at app startup.
- **#125** (MLB PA props): `plate_appearances_gate` imported and wired in pipeline; model_registry has MLB_PLATE_APPEARANCES + PA + PLATE_APPEARANCES entries.
