---
name: Universal Agent B3A — MLB Moneyline lane adapter
description: B3A adapter maps WOW/LLP MLB moneyline evidence rows (read-only, post-preflight) into EvidencePacket + six B1 advisory role payloads
---

## Four new modules under gate_engine/universal_agent/lanes/mlb_moneyline/

| Module | Responsibility |
|---|---|
| `validation.py` | `AdapterInputError` + `validate_mlb_moneyline_row()` — fail-closed: wrong sport/market/event_id raises before any mapping |
| `field_map.py` | Pure extraction functions + derivation helpers; `SOURCE_ROW_FIELDS_USED` fixed audit tuple; all missing fields → "MISSING"/"UNKNOWN", never fabricated |
| `role_inputs.py` | Six `build_*_input()` functions — each builds a validated B1 payload; `RoleInputBuildError` on validation failure |
| `adapter.py` | `MlbMoneylineAdapter.adapt(row, run_id, snapshot_id=None)` → frozen `MlbMoneylineAdapterResult` |

## Required input row fields (raises AdapterInputError if wrong/absent)
- `sport` = "MLB" (case-insensitive)
- `market` or `prop_type` containing "winner"/"moneyline"/"ml"/"game winner"
- `event_id` non-empty string → becomes `canonical_event_id`

## Evidence field → B1 role mapping decisions

| Source field | Target |
|---|---|
| `starter_status` CONFIRMED/PROBABLE_STRONG | NEWS_STATUS.player_status = ACTIVE |
| `starter_status` PROBABLE_ONLY | NEWS_STATUS.player_status = QUESTIONABLE |
| `starter_status` SCRATCHED/OUT | NEWS_STATUS.player_status = OUT, injury_flag = True |
| `starter_status` None | NEWS_STATUS.player_status = UNKNOWN |
| `event_status` SCHEDULED/ACTIVE_PREGAME_VALID | MEL.market_status = OPEN |
| `event_status` POSTPONED/CANCELLED | MEL.market_status = CLOSED |
| `sportsbook_no_vig_probability` | MEL.confirmed_line (float), line_confirmed = True |
| `preflight_status` PASS | DSI.slate_consistency = CONSISTENT, FC.recommendation = PROCEED |
| `preflight_status` WATCH | DSI.slate_consistency = CONSISTENT, FC.recommendation = HOLD |
| `preflight_status` FAIL/FAIL_POSTPONEMENT | DSI.slate_consistency = INCONSISTENT, FC.recommendation = ABORT |
| hard_blockers non-empty | FC.contradiction_detected = True, severity = HIGH |
| watch_blockers only | FC.contradiction_detected = False, severity = LOW |
| all 8 coverage fields present | FINAL_REFRESH.refresh_status = COMPLETE |
| any coverage field absent | FINAL_REFRESH.refresh_status = PARTIAL |
| FAIL/FAIL_POSTPONEMENT | FINAL_REFRESH.evidence_snapshot_valid = False |

## `player_id = None` — intentional
Team-level moneyline market; no individual player involved. Adapter always sets `player_id=None` and `player_name=None`.

## `AdapterStatus`
- COMPLETE: all 8 coverage evidence fields present
- DEGRADED: ≥1 coverage field absent; all 6 role payloads still built with UNKNOWN/MISSING values; orchestrator can still run

## `_scan_forbidden_keys` return semantics (discovered in B3A)
Returns `None` when no violations found (not `[]`). Tests must use `assertIsNone()`, not `assertEqual(violations, [])`.

## Frozen dataclass mutation testing
`object.__setattr__()` bypasses Python frozen dataclass protection. Tests must use plain attribute assignment (`obj.field = value`) to correctly assert frozen behavior raises.

## Test counts
- `tests/test_universal_agent_b3a.py`: 156 collected, 156 passed, 12 subtests passed, 0 failed
- 10 test classes covering: validation, field_map, DSI/NS/MEL/SS/FC/FR role inputs, adapter integration, end-to-end orchestrator pipeline

**Why:** The adapter is pure data transformation — all authority (terminal labels, probability math, can_execute, preflight thresholds) remains in existing WOW/LLP pipeline code. The adapter is explicitly read-only from the row.
