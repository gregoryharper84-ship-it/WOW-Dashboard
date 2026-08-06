---
name: WNBA Evidence Acquisition Structural Patch
description: Gate order, blocking rules, and non-blocking categories for the WNBA acquisition pipeline (WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL).
---

# WNBA Evidence Acquisition Structural Patch

## Gate insertion point
Inserted in `pipeline.py` between `status_role.run()` and `_wnba_opp_gate.run()` (the WNBA opportunity engine). Import: `from .wnba import evidence_acquisition as _wnba_evidence_acq`.

## New modules
- `gate_engine/wnba/acquisition_packet.py` — WNBAOpportunityPacket schema, ledger reconstruction, source normalization
- `gate_engine/wnba/missing_field_detector.py` — required fields detector, REQUIRED_PACKET_FIELDS list
- `gate_engine/wnba/fallback_router.py` — source priority config + in-pipeline reconstruction handlers
- `gate_engine/wnba/evidence_acquisition.py` — main orchestrator

## Critical blocking rule
`PACKET_INCOMPLETE_REJECTED` (which sets DATA_CONTRACT_FAIL and skips the analytical pipeline) is ONLY triggered when `role_status.*` categories are DATA_UNOBTAINABLE_AFTER_EXHAUSTION.

**Non-blocking categories** (unobtainable → PACKET_RECONSTRUCTED, NOT PACKET_INCOMPLETE_REJECTED):
- `event_status` — informational; existing analytical gates don't depend on it
- `matchup` — spec explicitly permits null/proxy values
- `box_score_log` — existing opportunity engine handles absence with HOLD (not hard reject); adding a hard block here would be a regression

**Why:** When `box_score_log` and `event_status` were made blocking, 2 existing tests failed (`test_polymarket_only_caps_at_market_verified_hold`, `test_pipeline_lowest_ceiling_preserved_end_to_end`). They use WNBA rows without `event_status` in enrichment. The fix was `_NON_BLOCKING_PROXY_CATEGORIES = frozenset({"matchup", "event_status", "box_score_log"})`.

## fallback_triggered semantics
`acquisition_audit.fallback_triggered` is True when `initial_missing_fields` was non-empty (fallback was NEEDED), regardless of whether fallback succeeded. Bug to avoid: passing the post-fallback `missing_after_primary` (which may be empty after reconstruction) to `_build_acquisition_audit`—pass `initial_missing_fields` instead.

## Terminal status enum
Six allowed terminal statuses per field (replaces NOT_CALLED as terminal):
PRIMARY_RETRIEVED, FALLBACK_RETRIEVED, MULTI_SOURCE_RECONSTRUCTED, PROXY_ONLY, SOURCE_CONFLICT, DATA_UNOBTAINABLE_AFTER_EXHAUSTION.
NOT_CALLED is intermediate only — never final in field_status_map.

## Empty list = absent
In missing_field_detector, `_ALLOW_EMPTY_LIST = frozenset()` — empty list counts as absent for all required fields. A box_score_log=[] means no data retrieved → triggers fallback.
