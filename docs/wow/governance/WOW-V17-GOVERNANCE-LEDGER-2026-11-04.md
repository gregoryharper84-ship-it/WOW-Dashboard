# WOW V17 Governance Ledger — Historical Reconciliation and Current Control Map

**Ledger ID:** GOV-2026-11-04-001  
**Revision:** proposed v1.0, 2026-11-04  
**Status:** PROPOSED_FOR_INDEPENDENT_REVIEW; not a runtime gate or release authorization  
**Scope:** WOW Betting Engine, LLP Team Betting Engine, Scout/Research, Kalshi Weather when applicable, engineering/recovery, SIRT and product acceptance  
**Owner:** WOW Betting Intelligence (policy/audit intent); Engineering (implementation); independent QA/SIRT and Release Authority (acceptance)  
**Execution:** can_execute=false; dry run only; no betting/order placement, approval, modification or cancellation

## 1. Audit disposition

The original WOW master specification (June 27, 2026, WOW v16 Clean Core / Framework v2.2.0) and LLP-PATCH-2026-06-27-EXECUTION-GOVERNANCE-v16.1 remain **historical evidence**, not active V17 scoring or publication authority. Preserve their exact historical versions. Do not overwrite them, rewrite immutable records, or retrospectively reinterpret their recorded approvals.

This ledger reconciles those provisions with the current V17 contracts. It does **not** create a new scoring engine, grant rank eligibility, modify model weights, alter production gates, or assert that currently unmerged specialist adapters have been deployed.

### Primary repository evidence reviewed

- Historical: attached_assets/WOW-MASTER-SPEC-v16-draft_(1)_1782567324335.md
- Current lifecycle authority: docs/wow/GENERATION_STATUS.md
- V17 probability/legacy overlay: docs/wow/LLP-V17-AUTHORITY-OVERLAY-2026-09-10.md
- Current-status pointer (must be refreshed from actual receipts): artifacts/wow-engine/V17_PRODUCTION_STATUS.md
- Action contracts: artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml and openapi.llp-team-engine.v17.yaml
- Historical agent guidance to reconcile separately: .agents/memory/llp-governance-reuse.md
- Incident/recovery audit trail: artifacts/wow-engine/v17/incident-ledger.json
- Current instructions and model/prop skill contracts must be reconciled against these authoritative backend contracts rather than assumed synced to a live Custom GPT editor.

### Authority order

1. Actual V17 runtime contracts, registered model capabilities, immutable Action receipts, and the globally controlling V17_TERMINAL_REDUCER govern real runtime behavior; a document cannot override observed failure.
2. Approved and merged V17 repository governance, calibrated model contracts, protected release requirements, and authenticated environment attestations govern their respective domains.
3. Independently accepted QA, release, deployment, and production verification receipts establish completion only for the exact scope and SHA attested.
4. The current approved V17 policy ledger describes intent; a draft PR or this proposed text has no authority to weaken or replace deployed controls.
5. Compatible V16/V15 materials remain historical, diagnostic and regression references. Where conflicting, they are non-controlling.
6. Research tips, Scout and third-party market data are evidence only; they have no sporting-probability or terminal authority.

## 2. Historical provision reconciliation

| Legacy provision | Disposition | V17 interpretation / required action |
|---|---|---|
| No fabricated validations, no action from narrative or recent hit rates alone | PRESERVE | No probability without the exact fitted controlling specialist and valid numeric package |
| Exact board, player, event, market, side, and timestamp validation | PRESERVE_AND_EXTEND | Canonical official IDs; exact prop period, line, threshold, direction, offer, settlement and current status; no adjacent-line substitution |
| No forced picks / valid no-play outcomes | PRESERVE_AND_EXTEND | Zero qualified candidates is valid; typed blocked/purged/research rows remain visible; no forced Top N |
| Source grades, contradiction checking and final freshness | ADAPT | Source family, evidence provenance, timestamps and material-change rerun are required; historical A/B/C/D automatic caps are not independently authoritative for V17 |
| Historical 6-checkpoint and universal cross-market-first gates | RETIRE_AS_GLOBAL_ORDER | Retain applicable safeguards under the sport/prop specialist, model input and downstream market gates; market price cannot precede sporting probability as a universal prerequisite |
| Mandatory price edge for LLP_PLAYABLE or LLP_APPROVED | RETIRE_FOR_PROBABILITY_LANE | Market/value/edge is a distinct optional/dependent workflow; missing odds must not erase valid sporting probability |
| Legacy LLP_APPROVED, LLP_PLAYABLE, LLP_WATCH, LLP_SCOUT, LLP_REJECT, LLP_CUT as terminal labels | ARCHIVE_FOR_V16_COMPAT | No legacy label confers V17 approval, rank eligibility or execution; preserve backend-native typed status and sole V17 terminal reduction |
| Fixed percentage win bands (52%, 54%, 57%, 60%) | ARCHIVE_UNLESS_RECERTIFIED | Not V17 probability, calibration, confidence or ranking thresholds absent explicit fitted-lane certification and tested versioned configuration |
| Fixed edge floors (1.5%, 2%, 2.5%, 3%) | ARCHIVE_UNLESS_RECERTIFIED | Only a currently governed downstream market/value contract may define edge thresholds; never use to cap sporting probability |
| Fixed line-decay rules, steam thresholds and universal WATCH downgrade | ADAPT | Use as possible evidence-freshness/contradiction alerts only where supported; any numeric change or publication ceiling needs the exact active specialist/gate rule; material inputs require model rerun |
| Daily bet count, unit/stake and bankroll limits | ARCHIVE_AS_EXECUTION_HISTORY | V17 does not place or approve bets. Keep session correlation, duplicated thesis and exposure *research* controls separate from model probabilities |
| Re-approval after news/line changes | PRESERVE_AS_REFRESH | Invalidate stale current presentation, preserve immutable prior prediction, score current exact thesis anew with a fresh receipt; no automatic upgrade |
| Calibration ledger with model probability, result, CLV, ROI, stake | SPLIT_AND_EXTEND | Independent immutable pregame forecast, exact official settlement/grading and optional economics/card ledgers; no price/stake requirement to retain a sporting forecast |
| ChatGPT strategy / Claude QA / Replit runtime chain | REPLACE | Conductor coordinates; Betting Intelligence owns research/intent/acceptance; one fitted specialist owns each probability; Engineering/AMRT implements; SIRT independently challenges; Release Authority and independent verification accept; Render/Supabase are runtime/persistence |
| One-size-fits-all sport or prop assumption | REPLACE | Sport- and market-specific registered specialist with exact capability status; NBA/NHL/NFL/MLB and newly added sports require independent route/acceptance evidence |
| Historical master spec declares V16 ACTIVE | HISTORICAL_ONLY | V17 is current generation according to docs/wow/GENERATION_STATUS.md; the old header must not determine runtime authority |

## 3. Current V17 non-negotiable controls

### GOV-01 — Safety and authority
- can_execute=false and DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true across all lanes and environments.
- V17_TERMINAL_REDUCER is sole global terminal disposition authority, not a probability model.
- No specialist, product manager, Scout, agent, legacy LLP validator, grader or release workflow may self-authorize a wager or override a stricter gate.

### GOV-02 — Single fitted probability owner
- Player/scalar props -> WOW_PROP_LANE -> exact sport/stat/period/line/direction specialist.
- Team/event outright winner/favorite/underdog/upset -> LLP_TEAM_BETTING_ENGINE -> exact sport/event specialist.
- Weather contracts -> certified Kalshi Weather specialist when applicable.
- Spreads, totals, derivative periods and compound props require their own supported exact route; an outright model is not a substitute.
- Scout, Discord tips, PropsMadness hit rates, sportsbooks, no-vig estimates, recent outcomes and generic LLM reasoning are research evidence, not fitted probabilities.

### GOV-03 — Conserve the complete candidate universe
- Union user, board, Scout, autonomous, backend and relevant specialist discovery where requested and permitted.
- Preserve provenance; deduplicate by verified canonical thesis, including exact event/market/period/line/direction/settlement.
- Reconcile every discovered source row to exactly one canonical scored, typed blocked, purged, superseded or duplicate mapping. Do not drop unsupported rows or stop after N candidates.

### GOV-04 — Exact identity, source and freshness
- Official event status, ID, start time, participants and settlement must match the exact selection.
- No started/final/postponed/canceled event in a pregame ranking.
- Material status, lineup, injury, goalie, quarterback, workload or weather update after the model timestamp invalidates *current publication* until rerun; do not rewrite the historic forecast.
- When the Action was never called, report invocation-not-attempted. When an Action transport fails, preserve transport status; neither implies model absence.

### GOV-05 — Native failure taxonomy
Preserve backend-native, narrow reasons such as:
- MODEL_INPUTS_INSUFFICIENT (candidate-specific missing or invalid input with a known route)
- MODEL_ROUTE_UNSUPPORTED (exact route is not declared)
- MODEL_UNAVAILABLE (genuine required model/adapter/artifact absence)
- MODEL_SCORER_FAILED (selected/invoked scorer failed)
- MODEL_OUTPUT_INVALID (malformed, impossible or invalid package)
- ACTION_TRANSPORT_TIMEOUT / ACTION_TRANSPORT_FAILURE / LIVE_GPT_ACTION_INVOCATION_BLOCKED
- EVENT_IDENTITY_UNRESOLVED, stale event/status and MARKET_DATA_UNOBTAINABLE when applicable.

Never normalize all failures to MODEL_UNAVAILABLE. Never claim backend health proves model scoring.

### GOV-06 — Sporting probability and market economics are separate
The model may complete sporting probability without a sportsbook price if the fitted model, its required inputs, failure paths, calibration, bounds and freshness all pass. Record independent market-role classification separately where favorite/upset leaderboard eligibility needs it. Missing downstream price blocks value/edge only; do not erase completed probability or lower bound.

### GOV-07 — Calibration, ranking and lower-bound integrity
- Require exact model family/version/artifact, immutable prediction identifier, calibrated probability, calibrated_lower_bound, numerical validity and final refresh for official rankings.
- Rank probability leaderboards by calibrated_lower_bound DESC, within the correct winner/upset cohort.
- A point estimate is not a lower bound. No universal manual haircut, proxy probability, narrative penalty or odds-based reconstruction.
- Explicitly mark missing probability receipts as not model-scored; no artificial published Top N.

### GOV-08 — Immutable forecast, settlement and learning
- Persist exact pregame prediction identity and timestamp when required; append/link official settlement separately.
- Match player/event/stat/period/threshold/direction/offer/settlement exactly before attributing model performance.
- Track cohort n, forecast mean versus observed, Brier, log loss, calibration bias, lower-bound reliability and counterexamples.
- Deduplicate repeated slips with one underlying thesis. Retrospective guesses never become pregame forecasts.
- Learning progression: L0 preserve; L1 log; L2 watch; L3 targeted hypothesis for repeated evidence or hard process error; L4 eligible only after replay, counterexamples, holdout/forward test and regression.

### GOV-09 — Minimal evidence-earned improvement
- No automatic model-weight, calibration, qualification threshold or probability adjustment from isolated wins, losses or commercial alerts.
- Compare incumbent and challenger on identical chronological cutoffs; guard against leakage and report previously correct picks harmed.
- Engineering implements; independent evaluators challenge and verify; promotion follows protected release, not a model's self-certification.
- Preserve valid behavior; patch root causes, not symptoms.

### GOV-10 — Exact-head release and product acceptance
- For every change record the objective ID, scope, owner, acceptance criteria, branch/PR, exact head SHA, required CI, independent QA/security/Release Authority evidence, merge SHA, Render deploy receipt, live runtime acceptance, rollback and closure proof.
- No duplicate or reclassified failure may bypass required gates. CI PASS != merged != deployed != independent acceptance.
- Separate BACKEND_RUNTIME, MODEL_CAPABILITY, REPOSITORY_GOVERNANCE and LIVE_GPT_EDITOR_SYNC. User-facing e2e scoring is a further product-acceptance surface.
- Operational disposition: COMPLETE only with exact independent proof; otherwise WORKING_NOT_COMPLETE, BLOCKED, FAILED, SAFE_HOLD or UNSUPPORTED as applicable.

### GOV-11 — Engineering and SIRT separation
- WOW_ECOSYSTEM_CONDUCTOR coordinates work, not probability.
- WOW Betting Intelligence owns discovery/quality and product acceptance definition, not merge or release self-certification.
- Engineering and AMRT diagnose/implement/repair through authorized branches and protected controls.
- SIRT independently challenges security, accuracy/calibration and production claims; SIRT never deploys.
- Independent QA/Release Authority verify exact head and live outcome; V17_TERMINAL_REDUCER alone decides model terminal disposition.

### GOV-12 — No invented monitoring or runtime state
- A session audit is not continuous monitoring. Schedules/health checks require authenticated task, cadence, heartbeat and receipts.
- A static status document is not current Render/Supabase evidence. When runtime is unreachable, report unknown/blocked without promoting a prior green report.
- No paid-provider dependence or quota waiver is implied: use permitted free/alternate research or engineering resources only without weakening data/model validity.

## 4. Ledger fields for each future governance decision

Each proposed governance change should record:

| Field | Requirement |
|---|---|
| ledger_id, objective_id, authored_at_utc | Stable identifiers, timestamp |
| original_rule, original_version, original_source | Immutable provenance and exact before-language |
| proposed_rule, affected_route, affected_files | Explicit delta and blast radius |
| classification | PRESERVE, ADAPT, RETIRE_FOR_V17, ARCHIVE, or REPLACE |
| risk_class, owner, independent_reviewer | Named accountability; no self-review |
| reason, evidence_receipts, counterexamples | Falsifiable source-backed justification |
| tests, replay_cohort_n, holdout, regressions | As applicable; no fabricated metrics |
| rollback_plan, deployment_scope | Reversible minimal change |
| exact_head_sha, PR, approval, merge_sha | Separate evidence for each stage |
| production_sha, receipt, editor_sync, acceptance | Independent verification; no implied parity |
| status | PROPOSED, WORKING_NOT_COMPLETE, BLOCKED, FAILED, SAFE_HOLD, UNSUPPORTED or COMPLETE with proof |

## 5. Known drift risks and remediation backlog

1. **Legacy agent memory authority collision (P0, documentation/routing risk):** .agents/memory/llp-governance-reuse.md still calls V16 gate_engine/llp_governance.py the canonical LLP compliance engine and says to use its effective_label as terminal. Restrict this instruction to V16 compatibility/classification until V17 routing and reducer precedence are proven; update via a separately reviewed protected-contract change if .agents/** trust controls require it. Do not delete historic function/tests just to remove naming drift.
2. **Historical master spec active header:** leave preserved in attached_assets; make current V17 authority discoverable from docs/wow/GENERATION_STATUS.md and this ledger instead of rewriting the historical source.
3. **Static production-status staleness:** artifacts/wow-engine/V17_PRODUCTION_STATUS.md carries September observations. Any November release/route/editor status must be established from new exact receipts, not inferred from the document's historical wording.
4. **NBA/NHL and other sport adapter availability:** treat unmerged PRs, route declarations, source receipts and CI as distinct from authenticated fitted scoring and calibrated/terminal model output; do not advertise model qualification based on registration alone.
5. **Failure-code registry completeness:** compare current runtime codes against the canonical registered taxonomy and backfill missing definitions with owner, condition and regression fixture. Do not invent aliases to hide native failures.
6. **Live editor parity:** repository instructions or an Action schema commit do not update the user's deployed Custom GPT editor. Require save/reload and fresh authenticated Action acceptance.

## 6. Independent acceptance tests for this ledger

Before closing this governance migration, independently demonstrate:

1. Legacy LLP_APPROVED/LLP_PLAYABLE never authorizes V17 model publication or any order.
2. A valid fitted sport probability without market price remains a valid probability; market edge alone is unavailable.
3. No fitted model/adapter -> MODEL_UNAVAILABLE; undeclared market/stat -> MODEL_ROUTE_UNSUPPORTED.
4. Valid model with incomplete candidate inputs -> MODEL_INPUTS_INSUFFICIENT.
5. Selected scorer failure -> MODEL_SCORER_FAILED, not Action failure or missing model.
6. Model output schema/normalization failure -> MODEL_OUTPUT_INVALID.
7. Action not attempted and Action transport failure preserve their distinct backend-native statuses.
8. Exact line/side/period/threshold differences are not merged or substituted.
9. A stale/start-of-game event is excluded from current pregame rankings without modifying its pregame history.
10. Current winner and upset cohorts rank by authenticated calibrated lower bounds only.
11. Scout/external consensus cannot create sporting probability or hide non-Scout candidates.
12. Source rows exactly reconcile, including non-ranked and duplicates.
13. Postmortem attribution requires exact immutable pregame receipt; duplicate slip copies count once.
14. Challenger evaluation protects incumbent correct cases and prohibits post-event feature leakage.
15. CI, independent acceptance, merged SHA, Render deploy SHA and GPT editor sync remain separate proofs.
16. can_execute=false and V17_TERMINAL_REDUCER remain invariant under all cases.

## 7. Completion and effective-state rule

**This proposed documentation is not itself an approved governance change.**

Engineering may implement the bounded documentation migration through an ordinary protected PR. Independent verification must check the conflicting legacy guidance and all relevant tests before acceptance. Production model logic, probabilities, thresholds, terminal reducer and order/execution controls remain unchanged.

The audit is COMPLETE as a document review once source-to-current dispositions are independently accepted. Runtime governance migration and any agent-memory repair remain WORKING_NOT_COMPLETE until their own exact-head and production/editor evidence is returned.
