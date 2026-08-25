# Regression Tests — wow-cross-sport-high-probability-selector

PATCH:   WOW-PATCH-2026-08-05-CROSS-SPORT-HIGH-PROBABILITY-SELECTOR
STATUS:  ANALYTICAL SHADOW MODE — tests required before skill-registry.json activation
FILE:    gate_engine/tests/test_cross_sport_selector_regressions.py

---

## Test Matrix

### POLICY — Permanent governance invariants

| ID | Assertion | Fixture | Expected |
|---|---|---|---|
| POLICY-001 | `can_execute` is False in every output | Any valid candidate | `can_execute=False` unconditional |
| POLICY-002 | `requires_human_confirmation` is True in every output | Any valid candidate | Field present and True |
| POLICY-003 | `NO_PLAY` is returned when no candidate qualifies | All candidates below threshold | `terminal_state=NO_PLAY` |

### COMBO — Kalshi combo gate (combo_gate.py)

| ID | Assertion | Fixture | Expected |
|---|---|---|---|
| COMBO-001 | 1-market combo is allowed | 1 Kalshi leg | `allowed=True`, `reject_code=None` |
| COMBO-002 | 2-market combo is allowed | 2 Kalshi legs | `allowed=True`, `reject_code=None` |
| COMBO-003 | 3-market combo is rejected | 3 Kalshi legs | `allowed=False`, `reject_code=REJECT_BAD_STRUCTURE` |
| COMBO-004 | 4-market combo is hard-rejected | 4 Kalshi legs | `allowed=False`, `reject_code=HARD_REJECT_COMBO_MULTIPLICATION` |

All combos: `can_execute=False`, `dry_run_only=True` unconditional.

### LEDGER — Backend dependency degradation

| ID | Assertion | Fixture | Expected |
|---|---|---|---|
| LEDGER-001 | Immutable prediction ledger unavailable → non-blocking | Ledger absent from backend | `prediction_write_status=NOT_AVAILABLE`, output not blocked |
| LEDGER-002 | Cross-ticket exposure ledger partial → PARTIAL status | Slip-scoped modules present, prediction-keyed absent | `cross_ticket_exposure_status=PARTIAL` with detail string |

### LANE — Output lane logic

| ID | Assertion | Fixture | Expected |
|---|---|---|---|
| LANE-001 | Candidate with stale market excluded from Lane C only | `market_freshness=STALE`, `raw_probability=0.75`, `calibrated_lower_bound=0.68` | In Lanes A and B; absent from Lane C |
| LANE-002 | Candidate with `calibrated_lower_bound=0.60` excluded from Compact Card | Lower-bound below 0.65 floor | Not in Compact Card; may appear in Lane A |
| LANE-003 | Kalshi pair from same event capped at 1 by portfolio governor | 2 Kalshi legs same event_id | 1 retained in pool, 1 in Lane D with governor label |

### GOVERN — Selector governance rules

| ID | Assertion | Fixture | Expected |
|---|---|---|---|
| GOVERN-001 | Winning prior card does not upgrade current candidate | `prior_card_won=True`, current candidate `raw_probability=0.68` | Current candidate label unchanged; does not reach Lane A threshold |
| GOVERN-002 | Missing event identity blocks all lanes | `event_key=None` | No lanes populated; Lane D entry with `REJECT_DATA_QUALITY` |
| GOVERN-003 | Human confirmation field present in output schema | Any output | `requires_human_confirmation=True` in output |
| GOVERN-004 | Same injury thesis across legs → at most one retained | 2 legs with `injury_thesis="player_X_return"` | 1 retained in Compact Card; 1 in Lane D with dependence flag |
| GOVERN-005 | Cross-book legs not one executable parlay | 2 legs from different books | `can_execute=False`, `parlay_executable=False` |
| GOVERN-006 | Outcomes do not overwrite predictions | Settle event after prediction written | Original `prediction_id` record unchanged |
| GOVERN-007 | NO_PLAY when nothing qualifies | All candidates: `raw_probability < 0.70`, `calibrated_lower_bound < 0.65` | `terminal_state=NO_PLAY` |

---

## Fixture Definitions

### FIX-STALE-MARKET
```json
{
  "candidate_id": "test-stale-market-001",
  "sport": "MLB",
  "event_key": "CHC@STL-2026-08-05",
  "market": "moneyline",
  "side": "CHC",
  "raw_probability": 0.75,
  "calibrated_probability": 0.72,
  "calibrated_lower_bound": 0.68,
  "calibrated_upper_bound": 0.76,
  "market_freshness": "STALE",
  "no_vig_probability": null,
  "material_market_conflict": null
}
```
Expected: In Lanes A (rank by 0.75) and B (rank by 0.72/0.68). **Absent from Lane C** (market_freshness=STALE).
Lane C blocker: `"missing_required_field": "no_vig_probability (market stale)"`

### FIX-LOW-LOWER-BOUND
```json
{
  "candidate_id": "test-low-lb-001",
  "sport": "WNBA",
  "event_key": "CHI@LAS-2026-08-05",
  "market": "moneyline",
  "side": "LAS",
  "raw_probability": 0.71,
  "calibrated_probability": 0.67,
  "calibrated_lower_bound": 0.60,
  "calibrated_upper_bound": 0.74,
  "failure_path_score": 0.22,
  "specialist_gate_label": "RESEARCH_INTEREST"
}
```
Expected: In Lane A (raw_probability=0.71 >= 0.70). **Absent from Lane B** (calibrated_lower_bound=0.60 < 0.65 floor). **Absent from Compact Card** (weakest leg by lower bound). Appears in Lane D for Compact Card with reason: `"calibrated_lower_bound=0.60 below 0.65 floor"`.

### FIX-KALSHI-SAME-EVENT
```json
[
  {
    "candidate_id": "kalshi-001",
    "sport": "KALSHI",
    "event_key": "KXMLB-CHC-WIN-2026-08-05",
    "market": "sports_winner",
    "side": "CHC",
    "raw_probability": 0.73,
    "calibrated_lower_bound": 0.67
  },
  {
    "candidate_id": "kalshi-002",
    "sport": "KALSHI",
    "event_key": "KXMLB-CHC-WIN-2026-08-05",
    "market": "sports_winner",
    "side": "STL",
    "raw_probability": 0.68,
    "calibrated_lower_bound": 0.62
  }
]
```
Expected: Portfolio governor caps at max 1 per event. `kalshi-001` retained (higher probability). `kalshi-002` in Lane D with `reject_source="kalshi_portfolio_governor"`, `terminal_label="DUPLICATE_EXPOSURE_BLOCK"`.

### FIX-SAME-INJURY-THESIS
```json
[
  {
    "candidate_id": "tennis-001",
    "sport": "Tennis",
    "injury_thesis": "player_X_return",
    "calibrated_lower_bound": 0.70,
    "raw_probability": 0.75
  },
  {
    "candidate_id": "nba-001",
    "sport": "NBA",
    "injury_thesis": "player_X_return",
    "calibrated_lower_bound": 0.66,
    "raw_probability": 0.71
  }
]
```
Expected: Dependence audit flags the pair. At most one appears in Compact Card. `tennis-001` retained (higher lower bound). `nba-001` in Compact Card Lane D with `dependence_flag="same_injury_thesis"`.

### FIX-MISSING-EVENT-IDENTITY
```json
{
  "candidate_id": "test-no-event-001",
  "sport": "NFL",
  "event_key": null,
  "market": "moneyline",
  "raw_probability": 0.78
}
```
Expected: **All lanes blocked.** Lane D entry: `terminal_label="REJECT_DATA_QUALITY"`, `reject_source="event_identity_lock"`, `reject_reason="event_key is null"`.

### FIX-BELOW-ALL-THRESHOLDS
```json
[
  { "candidate_id": "below-001", "raw_probability": 0.65, "calibrated_lower_bound": 0.60, "lower_bound_edge": -0.02 },
  { "candidate_id": "below-002", "raw_probability": 0.62, "calibrated_lower_bound": 0.58, "lower_bound_edge": 0.01 }
]
```
Expected: `terminal_state=NO_PLAY`. All three lanes empty. Lane D shows both candidates with rejection reasons.

---

## Reconciliation Logic

After each test run, verify:

1. **Labels match**: every `terminal_label` in Lane D reproduces a label that exists in the backend label set. No invented labels.
2. **Ceiling intact**: no candidate in Lane A or B has a `specialist_gate_label` that is a blocking label (`REJECT_*`, `SLATE_PURGE`, `SOURCE_CONFLICT`, `DUPLICATE_EXPOSURE_BLOCK`). If a blocking label is present, the candidate must be in Lane D, not in A or B.
3. **Ledger block fields present**: every output includes `prediction_write_status`, `cross_ticket_exposure_status`, `final_refresh_status`.
4. **can_execute=False**: scan all output objects; no object may have `can_execute=True`.
5. **Compact Card invariant**: the Compact Card must not contain a leg whose `calibrated_lower_bound < 0.65` AND the only reason it was kept was its raw probability.
