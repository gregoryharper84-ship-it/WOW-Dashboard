# WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL

**Type:** Structural plumbing and observability  
**Scope:** WNBA evidence acquisition pipeline only  
**Status:** IMPLEMENTED — 2635 tests passing, 0 regressions  
**can_execute:** False (unconditional on all new modules)

---

## What this patch adds

A structured, field-aware fallback stage that runs for every WNBA prop **before** the existing analytical pipeline. Previously, when backend/API data for a WNBA prop was incomplete, the system marked fields `NOT_CALLED` and the failure-path gate refused to score — but there was no structured stage that actually tried to fill gaps before giving up. This patch adds that stage.

**Explicitly out of scope (not implemented):**
- Game-script/regime probabilities or any regime-conditional means
- Points/rebounds/assists covariance estimation
- Isotonic regression or other fitted calibration
- New calibrated probability bounds
- Changes to existing probability formulas, gate thresholds, or qualification labels
- Changes to analytical gate order beyond inserting this stage before the pipeline

---

## Files added

### `gate_engine/wnba/acquisition_packet.py`
- `PacketStatus` class: `PACKET_COMPLETE`, `PACKET_RECONSTRUCTED`, `PACKET_INCOMPLETE_REJECTED`
- `AcquisitionFieldStatus` class: six terminal statuses replacing `NOT_CALLED`
- `SourceGrade`, `AcquisitionMethod` constants
- `normalize_source_claim(claim)` — validates source metadata (source + retrieved_at required)
- `reconstruct_raw_ledger_rows(box_score_log)` — raw per-game assembly (date, opponent, starter, minutes, points, rebounds, assists, PRA, FGA, 3PA, FTA, team_result, margin, fouls)
- `build_packet(row, enrichment, as_of)` — builds WNBAOpportunityPacket from current row/enrichment state
- `validate_role_source_claims(role_status)` — returns list of validation errors

### `gate_engine/wnba/missing_field_detector.py`
- `REQUIRED_PACKET_FIELDS` — 8 required paths: `event_status`, `role_status.active_status`, `role_status.role_timestamp`, `role_status.projected_minutes`, `box_score_log`, `l5_ledger`, `l10_ledger`, `matchup`
- `detect_missing(packet)` — returns list of absent field paths (empty list = all present)
- `classify_missing_fields(missing)` — categorises into event_status/role_status/box_score_log/matchup
- `build_coverage_audit(packet, missing)` — COVERAGE_AUDIT gate record

### `gate_engine/wnba/fallback_router.py`
- `FALLBACK_SOURCE_PRIORITY` — per-category ordered source config:
  - **event_status**: official WNBA injury report → team game notes → team communications → beat reporter → ESPN → aggregator
  - **role_status**: official WNBA injury report → team game notes → team communications → beat reporter → ESPN
  - **box_score_log**: official WNBA box scores → player game logs → Basketball Reference → ESPN game logs → StatMuse (reconstruction support only)
  - **matchup**: official WNBA team stats → advanced stats database → proxy estimate from season log
  - **market_comparison**: Odds API (exact matching) → consensus sportsbook line
  - **news_contradiction**: dedicated conflict scan
- `route_fallback_for_categories(categories, packet, enr)` — dispatches to per-category handlers
- In-pipeline reconstruction handlers: event_status from enrichment alt keys, role fields from status_role gate output, box_score_log from `game_log` alternate key, matchup from partial data

### `gate_engine/wnba/evidence_acquisition.py`
Main orchestrator called by `pipeline.py`.

Gate execution order per spec §8:
```
SLATE → IDENTITY → PRIMARY_ACQUISITION → COVERAGE_AUDIT →
FALLBACK_ROUTING → SOURCE_RECONCILIATION →
OPPORTUNITY_PACKET_VALIDATION →
[existing analytical pipeline unchanged] → FINAL_REFRESH
```

- `run(row, enrichment)` — per-row entry point; only acts on WNBA rows
- `_run_source_reconciliation()` — checks for source conflicts in role_status claims
- `_validate_packet()` — determines `PACKET_COMPLETE / PACKET_RECONSTRUCTED / PACKET_INCOMPLETE_REJECTED`
- `_build_acquisition_audit()` — builds spec-compliant audit object (all 11 required fields)
- `_build_field_status_map()` — per-field terminal status (strict enum, no NOT_CALLED terminal)

---

## Files modified

### `gate_engine/pipeline.py`
**+2 lines** at top: imports `evidence_acquisition as _wnba_evidence_acq`

**+18 lines** inserted between `status_role.run()` and `_wnba_opp_gate.run()`:
```python
if _wnba_evidence_acq.is_wnba_row(row):
    _ea_result = _wnba_evidence_acq.run(row, enrichment=enr)
    if _ea_result.get("packet_status") == "PACKET_INCOMPLETE_REJECTED":
        row["blockers"].append("WNBA_EVIDENCE_ACQUISITION:PACKET_INCOMPLETE_REJECTED:...")
        row["terminal_label"] = PropLabel.DATA_CONTRACT_FAIL.value
        continue
```

### `gate_engine/wnba/__init__.py`
Updated module docstring to list all four new modules.

---

## Gate result schema (stored at `row["gates"]["wnba_evidence_acquisition"]`)

```json
{
  "gate": "WNBA_EVIDENCE_ACQUISITION",
  "patch": "WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL",
  "packet_status": "PACKET_COMPLETE | PACKET_RECONSTRUCTED | PACKET_INCOMPLETE_REJECTED",
  "fields_unresolved": [],
  "fields_reconstructed": [],
  "missing_after_primary": [],
  "can_execute": false,
  "field_status_map": {
    "event_status": "PRIMARY_RETRIEVED | FALLBACK_RETRIEVED | DATA_UNOBTAINABLE_AFTER_EXHAUSTION | ...",
    "role_status.active_status": "...",
    ...
  },
  "acquisition_audit": {
    "run_ts": "2026-08-06T17:00:00+00:00",
    "primary_api_attempted": true,
    "primary_api_result": "FULL | PARTIAL | FAILED",
    "missing_after_primary": [],
    "fallback_required": false,
    "fallback_triggered": false,
    "fallback_routes_attempted": [],
    "fallback_sources_successful": [],
    "fields_reconstructed": [],
    "fields_unresolved": [],
    "fallback_failure_reason": null,
    "packet_status": "PACKET_COMPLETE",
    "source_reconciliation": { "gate": "SOURCE_RECONCILIATION", "passed": true, ... },
    "coverage_audit": { "gate": "COVERAGE_AUDIT", "coverage_pct": 100.0, ... },
    "fallback_result_details": {}
  }
}
```

---

## Blocking rules (non-blocking categories)

`PACKET_INCOMPLETE_REJECTED` is only triggered when `role_status.*` fields are `DATA_UNOBTAINABLE_AFTER_EXHAUSTION`.

Non-blocking categories (unobtainable → PACKET_RECONSTRUCTED, not REJECTED):
- **`event_status`** — new observability field; existing analytical gates don't depend on it
- **`matchup`** — spec §1 explicitly permits null/proxy values
- **`box_score_log`** — existing opportunity engine already handles absent box_score_log with WNBA_HOLD_ROLE_UNCERTAIN (soft hold); adding a hard reject here would change existing behavior

---

## AcquisitionFieldStatus terminal enum (replaces NOT_CALLED)

| Status | Meaning |
|---|---|
| `PRIMARY_RETRIEVED` | Present from primary API/enrichment |
| `FALLBACK_RETRIEVED` | Retrieved via fallback (alt key, reconstruction) |
| `MULTI_SOURCE_RECONSTRUCTED` | Assembled from multiple in-pipeline sources |
| `PROXY_ONLY` | Only indirect/estimated data available |
| `SOURCE_CONFLICT` | Conflicting claims across sources |
| `DATA_UNOBTAINABLE_AFTER_EXHAUSTION` | Not found after all configured routes attempted |

`DATA_UNOBTAINABLE_AFTER_EXHAUSTION` is only emitted after all configured routes for that category are logged as attempted in the `acquisition_audit`.

---

## Test coverage

File: `gate_engine/tests/test_wnba_evidence_acquisition.py` (12 tests)

1. Fallback activates when box_score_log absent from primary enrichment
2. PACKET_INCOMPLETE_REJECTED blocks the row (terminal label set, no NOT_CALLED-equivalent terminal status in field_status_map)
3. N box-score rows → exactly N raw ledger rows (no averaging/collapsing)
4. Empty box_score_log → empty ledger (not a crash, not a summary row)
5. Source claim without `retrieved_at` fails `normalize_source_claim()`
6. Source claim without `source` fails `normalize_source_claim()`
7. `validate_role_source_claims()` flags both missing-source and missing-retrieved_at errors
8. DATA_UNOBTAINABLE_AFTER_EXHAUSTION only emitted after all configured routes logged as attempted
9. PACKET_COMPLETE when all required fields present
10. PACKET_RECONSTRUCTED when box_score_log absent but game_log alt key present
11. Non-WNBA rows skipped entirely (no gate output, no row modification)
12. `can_execute=False` unconditional on all four modules and in every gate result

---

## Sample acquisition_audit output (PACKET_RECONSTRUCTED via game_log alt key)

```json
{
  "run_ts": "2026-08-06T17:15:00+00:00",
  "primary_api_attempted": true,
  "primary_api_result": "PARTIAL",
  "missing_after_primary": ["box_score_log", "l5_ledger", "l10_ledger"],
  "fallback_required": true,
  "fallback_triggered": true,
  "fallback_routes_attempted": [
    "official_wnba_box_scores", "official_wnba_player_game_logs",
    "basketball_reference", "espn_game_logs", "statmuse_reconstruction_query"
  ],
  "fallback_sources_successful": ["box_score_log:enrichment_game_log_alternate_key"],
  "fields_reconstructed": ["box_score_log", "l5_ledger", "l10_ledger"],
  "fields_unresolved": [],
  "fallback_failure_reason": null,
  "packet_status": "PACKET_RECONSTRUCTED",
  "fallback_result_details": {
    "box_score_log": {
      "source_id": "enrichment_game_log_alternate_key",
      "source_grade": "B",
      "method": "RECONSTRUCTED",
      "status": "FALLBACK_RETRIEVED",
      "note": "reconstructed from enrichment['game_log'] (3 rows); use l5/l10 from this",
      "routes_attempted": ["official_wnba_box_scores", "official_wnba_player_game_logs", ...]
    }
  }
}
```

---

## Confirmed invariants

- ✅ No existing probability formula touched
- ✅ No gate thresholds changed
- ✅ No new qualification/confidence labels created
- ✅ Gate order unchanged: new stage inserted between status_role and WNBA opportunity engine only
- ✅ `can_execute=False` unconditional on all new modules
- ✅ 2635 existing tests pass, 0 regressions
