---
name: UAC Fast-Track Conveyor State
description: B0–B9 Universal Agent Core conveyor phases, closure status, git SHAs, and key design decisions per lane.
---

# UAC Fast-Track Conveyor — Final State

Conveyor: B0-B3C (FROZEN) → B4 → B5 → B6 → B7 → B8 → B9 → UAC V1 COMPLETE

## Phase Closure Status

| Phase | Status | Commit SHA | Tests |
|---|---|---|---|
| B0 (hardening) | CLOSED | — | pre-existing |
| B3A MLB Moneyline adapter | CLOSED | prior session | test_b3_lanes.py |
| B4 WNBA/NBA Props adapter | CLOSED | eb5079e (evidence) | 237/237 focused; 4783 full |
| B5 MLB Props adapter | CLOSED | 66487f9 | 83/83 |
| B6 Tennis Props adapter | CLOSED | (B6 commit) | 71/71 |
| B7 Generic Moneyline adapter | CLOSED | a121006 | 59/59 |
| B8 Consolidated Shadow Validation | CLOSED | cced129 | 52/52 |
| B9 Readiness Ruling | COMPLETE | cced129 | 35/35 |

Full regression at HEAD (cced129): **5083 passed / 0 failed / 11 skipped**

## Machine-Auditable Records on Disk
- `artifacts/flask-scoring-api/uac_b4_closure.json` — B4 closure (status=MACHINE_AUDITABLE_CLOSURE_PASS)
- `artifacts/flask-scoring-api/uac_b9_readiness_ruling.json` — B9 ruling (PENDING_ARCHITECTURAL_AUTHORITY_ACCEPTANCE)

## Lane Registry

| Lane const | Module path | Adapter class |
|---|---|---|
| Lane.MLB_MONEYLINE | lanes/mlb_moneyline/ | MlbMoneylineAdapter |
| Lane.WNBA_PROPS | lanes/wnba_props/ | WnbaPropsAdapter |
| Lane.MLB_PROPS | lanes/mlb_props/ | MlbPropsAdapter |
| Lane.TENNIS_PROPS | lanes/tennis_props/ | TennisPropsAdapter |
| Lane.GENERIC_MONEYLINE | lanes/generic_moneyline/ | GenericMoneylineAdapter |

## Key Design Decisions Per Lane

### B5 MLB Props
- `pitcher_strikeouts` → `failure_path_prob_required=True` in SS payload
- `pitcher_outs` → `outs_equivalent` echoed in MEL+SS
- `pitcher_1ip_pitches` → event-tree routed (OneIpGate); `routing_required=True`, `generic_model_blocked=True`
- `ip_to_outs()` uses `round()*10%10` to avoid IEEE-754 drift

### B6 Tennis Props
- Markov chain required for total_games / set_games / first_set_games stat keys
- `is_first_set_market` flag blocks cross-market model application
- Simplex probabilities stored as raw `float` (not 6dp-rounded) to prevent FP drift in constraint checks
- surface defaults to UNKNOWN (not fabricated)

### B7 Generic Moneyline
- DEDICATED_LANE_SPORTS frozenset: mlb/baseball/wnba/nba/tennis/atp/wta etc. → raise SPORT_HAS_DEDICATED_LANE
- `probability_fabrication_flag=False` always; `generic_fallback_blocked=True` always
- `llp_probability_specialist_ref="wow.llp-moneyline-probability-expert"` in SS payload
- probability_status: AVAILABLE / RAW_ONLY / IMPLIED_ONLY / PROBABILITY_UNAVAILABLE

### B8 Shadow Validation
- 10 invariant suites: authority violations, blocker erasures, label writes, schema bypass, row reconciliation, bundle stability, determinism, cross-lane isolation, TECHNICAL_FAILURE consistency, no-app-import
- All 5 lanes tested together in one module

## Global Invariants (All Phases)
- `can_execute = False` all modules — AST-checked in B8/B9
- `PRODUCTION_AUTHORITY = False` — unconditional
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS` in all adapter EXECUTION_RULE
- `advisory_only = True` on every role payload
- No forbidden governance keys (place_bet, settlement, stake_tier, final_decision etc.)
- EvidencePacket is frozen dataclass — all lanes
- Row wins on enrichment key collision

## Ruling (B9)
`UAC_V1_COMPLETE_PENDING_ARCHITECTURAL_AUTHORITY_ACCEPTANCE`

ChatGPT architectural authority must accept the B9 ruling before UAC V1 is production-authorized. No code changes needed for acceptance — it is purely a governance acknowledgment.
