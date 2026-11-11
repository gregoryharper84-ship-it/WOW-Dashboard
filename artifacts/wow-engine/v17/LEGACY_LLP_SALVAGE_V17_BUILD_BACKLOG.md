# WOW V17 — Legacy LLP Capability Salvage: Engineering Build and Backlog Specification

Status: DESIGN + ISOLATED DIAGNOSTIC CODE ON FEATURE BRANCH; NOT DEPLOYED.
Parent issue: #2162. Date: 2026-10-27. Priority after current four-sport P0 restoration: NFL, NBA, NHL, NCAAF.
Canonical source: artifacts/flask-scoring-api/LLP_GROUND_TRUTH.md (31 actual sections; a historical v16/Replit snapshot).
Inventory: artifacts/wow-engine/v17/legacy_salvage_manifest.json.
Reference controlling V17: artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt and the live runtime registry.

## Hard architecture and build boundary

V17 fitted controlling specialists alone own sporting probabilities and certified numerical model inputs. The V17 terminal reducer alone owns final terminal publication. Scout and legacy gates remain evidence/diagnostic only. No legacy badge, historical price, bookmaker implied odds, L5 hit rate, generic assistant reasoning, approximate model or historical 'SHIPPED' note may promote a probability or terminal status. can_execute=false is invariant. Never place, approve, cancel or modify bets/trades.

Preserve a finished fitted sporting probability when downstream sportsbook price is absent, unless the certified route explicitly requires the missing market data as a model input. Exact-line evidence and adjacent-line evidence stay distinct. Historical timestamps, player status and period/settlement rules must not be reconstructed from postgame data. All source candidates must reconcile exactly once. A model invoked and failing must not be mislabeled MODEL_UNAVAILABLE.

## Deliverables already built in this branch

- legacy_salvage_manifest.json — machine-readable disposition of every 31 headings, source line, workstream, acceptance guard, and explicit non-deployment state.
- legacy_salvage_diagnostics.py — isolated, pure standard-library evidence preflight; timestamp/field/line context issues only; no live integration, probability or terminal classification.
- test_legacy_salvage_manifest.py — checks exact source coverage and prohibits accidental V16 market/edge authority.
- test_legacy_salvage_diagnostics.py — fixtures for started event, missed direction, nonstandard 7+ threshold, compound market, stale source, material update after model, adjacent price, no price and duplicate rows.

The current diagnostic helper is intentionally NOT used by /score-pick-request or the V17 reducer. The current artifacts are source-level scaffolding, not numerical model upgrades.

## Build order and workstream tickets

| Lane | Order | Implementation deliverable | Evidence before promotion |
|---|---|---|---|
| WS0 — contracts | 0 | Exact production-revision and code-path inventory for all 31 sections; V17 field-mapping table; no duplicated decision authorities | CI, source paths, API/receipt contract, runtime registry, same-SHA verification |
| WS1 — provenance/data | 1 | Official event and participant identity resolver; source family/as-of time; market/stat/period and literal at-least offers; role/status and per-stat data-contract audits | Wrong-team, changed opponent, ruled-out/IR, stale injury, traded player, 7+/8+, double-double fixtures |
| WS3 — confidence debt | 2 | Four separate diagnostic axes (MODEL, DATA, MARKET, PUBLICATION) and per-row missing-input recovery hints | Missing model input vs missing market vs bad Action invocation vs scorer failure |
| WS2 — distribution/role | 3 | Certified-adapter role state challenger with raw L5/L10/L20 game logs and with/without, post-trade/injury, starter/bench splits | Out-of-time replay, lower bound reliability, Brier/log loss and no double-counting |
| WS4 — market ledger | 4 | Durable first_seen/current/lock/close snapshots and explicit incomplete CLV anchors; exact side price/settlement audits | Same book/source/side timestamp, price stale/closed, no opener, adjacent-only evidence |
| WS5 — lifecycle/learning | 5 | Immutable pregame write, receipt lookup, exact settled match and five-axis postmortem grading | No hindsight edits, no line substitution, duplicate thesis counted as one selection but multiple exposure instances |
| WS6 — specialties | 6 | Evaluate WNBA ingestion, calibrated Kalshi exact settlement, MLB F5 separate-route candidates | Verified registry, independent fitted model for market where needed, dedicated holdout and settlement checks |

WS1/WS3 can be built in parallel with P0 production restoration as isolated data helpers. WS2 is Class C if any fitted feature, calibration method or bound changes. WS4 and WS5 cannot change sporting probability; WS6 is lower-priority until the top four sports stabilize.

## WS0: contract inventory and compatibility

1. For each JSON section, confirm exact SHA and current code path, and whether actually bound by V17 scoring. Record VERIFIED_ACTIVE_ON_RUNTIME, PRESENT_BUT_NOT_WIRED, MISSING, V17_CONFLICT, SHADOW_ONLY, or UNKNOWN (no optimistic default).
2. Capture old input/output field mapping to the exact current V17 schema. Flag breaking differences in status vocabulary, market/line/threshold normalization, persistence, scheduling and API auth.
3. Explicitly remove v16-specific source of authority: market weight 40–50%, universal haircuts, edge-first ANCHOR/BET, CLV/Kelly prerequisites for sporting-probability ranking, Replit production assumption.
4. Create bounded unit tests proving V17 native typed failures: MODEL_INPUTS_INSUFFICIENT vs MODEL_UNAVAILABLE vs MODEL_SCORER_FAILED vs MODEL_OUTPUT_INVALID vs ACTION_TRANSPORT_FAILURE vs market only.
5. Terminal reconciliation must be performed by V17_TERMINAL_REDUCER, not the legacy L10/LLP processors.

## WS1: data integrity and numerical-feature readiness

Canonical candidate key must include official event ID, league, scheduled start, player/team official ID, participant/opponent, exact stat family, period, line/at-least operator, direction, offer type when relevant, platform and settlement. Preserve input literals (e.g., '7+' means >=7, not automatic 6.5 substitution) unless the exact certified adapter defines the equivalent settlement. Compound double-double needs a separate route/settlement and cannot be rebuilt as independent PTS×REB events.

Build source-family consensus and typed contradiction evidence. Independent official feed remains superior to a pasted unverified claim for identity/status; never invent a player status. Every material fact carries source, source_timestamp, retrieval_timestamp, confidence and latest_material_update_at. Recent but contradictory evidence is not 'clean'. Staleness windows are per route and field: lineup/scratch feeds often require much fresher verification than a completed-game stat log. Missing prices do not block a price-independent fitted sporting prediction.

Before a certified fitted specialist runs, distinguish exact route unsupported, required features not hydrated, invalid timestamp, missing/ambiguous identity, started/final event and unsupported line. Continue scoring independent valid rows after blocking problematic ones.

## WS2: role-state challenger and calibrated distribution

Create canonical read-only role cohort materialization with complete history, not averages alone: starter, bench, full role, reduced role, minutes-qualified, post trade, pre trade, post injury, pre injury where needed, with/without material teammate, recent all-games and matchup-specific contextual cohorts. Keep sample count, effective N, exclusions, raw values, median, dispersion, opportunity minutes and source timestamps. Do not claim every legacy named ledger is numerically causal.

Challenger experiments: distribution overdispersion (Poisson vs negative-binomial where certified), mixture regimes for role uncertainty, robust outlier diagnostics and continuous effect from opponent strength only where fitted coefficients exist. Legacy 55/65% and proportional gap bands may triage research, never calibrate a probability by themselves. A legacy 0.5 binary-event blanket purge becomes an exact threshold/offer study: discrete mass, push rules, special payouts, certified OOD semantics.

Evaluation uses rolling-origin or strict time-blocked replay with no future data leakage. Compare baseline/challenger log loss, Brier, ECE/reliability, calibrated lower-bound coverage, exact-line coverage and the previously successful cohort. Pre-register targeted hypotheses and require independent sample/counterexamples; an isolated losing slip does not change weights. Do not impose an arbitrary numeric lift target or pretend a small cohort establishes calibration.

## WS3: source-confidence diagnostics and recovery queue

Report MODEL_FITTED_PACKAGE, INPUT_DATA, MARKET_EVIDENCE, PUBLICATION/TERMINAL readiness as independent axes; each axis must expose source values, phase, missing fields, as-of timestamps and next permitted recovery action. A completed probability can have MARKET_UNAVAILABLE without losing the probability. A validation queue is only a display/audit bucket. Confidence-debt flags can prioritize engineering intake but cannot change calibrated probabilities, approval status or lower-bound rank.

Automatic recovery is idempotent and constrained to permitted evidence fetching and retrying unresolved rows with the same request ID/row key. Action timeout or ambiguous completion first queries exact receipt; do not reenqueue a RUNNING job. A row counts 'scored' only on an actual scorer receipt. No propagation of arbitrary new V16 failure codes into terminal publication without a registered native mapping.

## WS4: market and CLV instrumentation

Use the current existing durable scheduler, not an import-time background daemon that duplicates per gunicorn worker. Snapshot event/selection/settlement/source/book/line/side/american price/provider event ID/fetched timestamp/stage. Exact matching is required for no-vig/EV; adjacent lines remain comparison context. On close, write CLV only when opening and closing anchors are real: write INCOMPLETE when opener is missing, not fabricated. Distinguish CLV of observed market movement from CLV of an actual pregame selection.

Cross-book discrepancy flags are research questions, not a substitute event identity or a universally hard-coded line-disagreement model penalty. Market-only failure must not rewrite an already certified probability into MODEL_UNAVAILABLE. No real order placement.

## WS5: immutable lifecycle and postmortem

Use V17 prediction_id, event/player/stat/line/direction/period and model_timestamp as pregame immutable identity. Save model family/version, raw and calibrated probabilities, bounds, source snapshot IDs and numerical features when permitted. Settlement is an append/link, not pregame overwrite. Grade model, calibration, data/process, card construction and realized variance independently. Brier/log loss require exact pregame p and official binary y; pushes/voids follow settlement metric policy. Duplicate slips sharing one thesis are not independent training samples.

## WS6: sport/market-specific continuation

WNBA: verify ESPN game logs, role/minutes, source audits and stat contracts; do not impose one 25h freshness TTL on late injuries. MLB F5: discover as a market candidate only; full game and F5 use different event windows/settlement, requiring their own certified probability route. Kalshi weather: official station/CLI date and contract settlement plus calibration of forecast distribution, not a hardcoded normal sigma treated as certified. Kalshi sports: direct orderbook, trading status, exact contract and final-lock are market-value checks, not probability engines.

## Required automated regression matrix

| Fixture | Expected result |
|---|---|
| 27-row historical NBA board, some missing fitted routes | All 27 rows accounted for individually; no blanket abort; typed failures remain intact |
| Player ruled out / scratched after original scoring | Remove stale pregame publication; rehydrate/rerun only through certified route |
| Two identical screenshots and different directions/lines | Deduplicate only exact identical theses, map all source instances, never collapse 7+ and 8+ |
| At-least 7+, 8+, and compound double-double | Literal offer semantics remain; route-specific adapter or typed unsupported/identity hold |
| Exact 4.5 K vs adjacent 5.5 K price | Adjacent never supplies exact no-vig authority |
| MLB 1IP supported vs unsupported lines | Preserve certified exact supported lines; unsupported OOD, no interpolation |
| Known model missing required data | MODEL_INPUTS_INSUFFICIENT, not MODEL_UNAVAILABLE |
| Registered model invoked and times out | MODEL_SCORER_FAILED or native scorer error, not capability absence |
| Model completes with malformed probability | MODEL_OUTPUT_INVALID; no assistant repair |
| Sporting probability complete, market feed down | Sporting probability persists; market/value unavailable |
| No CLV opening anchor | INCOMPLETE CLV, no synthetic historical opening price |
| Material lineup update later than model timestamp | Stale probability requires refreshed fitted scoring |
| Pasted contradictory game log or injury statement | Evidence/identity conflict, no narrative-only probability override |
| Duplicate thesis on multiple slips | One model forecast, multiple exposure instances |
| Postgame different stat/threshold/direction | Cannot be graded as same immutable model selection |
| Offline helper called on any candidate | No probability, terminal label, execution or claim of V17 scoring |

## Acceptance gates and independent proof

Unit: schema and deterministic fixture tests pass on exact PR head; no change to public production behavior. Integration: V17 canonical Action, official event/status hydration and exact source evidence verified for each supported route. Statistical: controlled holdout replay has adequate cohort support; no opportunistic selection after viewing outcome, no backtest benefit at expense of larger cohort; honest intervals and negative cases. Production: after authorized integration, confirm exact deployed main SHA, service/worker parity, live Action request/response/receipt and V17 reducer. SIRT independently verifies actual runtime, not merely green source checks.

Per-slice completion requires: code/test changes, one bounded owning issue, current production evidence where touched, full row reconciliation and rollback procedure. Only a slice meeting that evidence can be marked FIXED_AND_VERIFIED. The full legacy salvage program remains open until all 31 rows are dispositioned with proven current wiring or explicit archived/challenger-only status.

## Non-blocking backlog/admission contract

Backlog inclusion is NOT a new release check, CODEOWNER requirement, approval workflow, fitted model certification or authority to execute. Existing incident queue and provider routing own delivery; Claude, Gemini, Cursor and other available workers can accept bounded tasks only through authenticated existing dispatch, preserving single-writer leases and authorized review. Prioritize current P0 four-sport restoration. New model Class C hypotheses remain in shadow until replay/holdout and authorized model promotion. Every child work item links #2162 and this feature-branch artifact.

## Deployment and evidence status

At creation: SOURCE_SCAFFOLD_CREATED, HISTORICAL_CAPABILITIES_RECONCILED, PRODUCING_LANE_NOT_MODIFIED, RUNTIME_NOT_PROVEN, PR_CI_NOT_YET_VERIFIED, MODEL_ACCURACY_GAIN_NOT_PROVEN. These may change only with authentic source/CI/runtime receipts. can_execute=false.
