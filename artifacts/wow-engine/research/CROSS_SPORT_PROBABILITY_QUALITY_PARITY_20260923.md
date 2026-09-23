# Cross-sport team/event probability-quality parity audit — 2026-09-23

Issue: #781  
PR: #783  
Status: **PR_CREATED / MODEL-DEVELOPMENT WORK ACTIVE — NO CLASS C PROMOTION**

## Scope

The MLB probability-quality acceptance standard now applies as a governance/evidence contract to:

- MLB
- NFL
- NCAAF (`CFB` is an alias, not a second model authority)
- NBA
- WNBA
- NHL

Equal treatment means identical lifecycle/evidence requirements. It does **not** mean identical sporting models, coefficients, feature sets, distributions, dominance definitions or uncertainty mathematics.

## Shared required dimensions

Every lane must prove:

1. exact fitted sport specialist;
2. quantitative calibration health (Brier, log loss, ECE, calibration intercept/slope and max bin gap);
3. cohort reliability stored separately from event-specific uncertainty;
4. a certified event-specific uncertainty method;
5. immutable early-pregame model grading;
6. immutable final-pregame published grading;
7. sport-appropriate dominance/margin diagnostics;
8. chronological challenger selection;
9. genuinely untouched holdout evidence;
10. typed-failure preservation.

A stored `PASS` label, a registered route, sample count, generic uncertainty width or model import is insufficient.

## Important registry distinction

WOW currently has more model-development evidence than the production registry alone exposes. The `wow_d1_candidate_artifacts` / `wow_d1_training_rows` research registry contains current challengers for all requested non-MLB sports. Those rows are intentionally **CANDIDATE**, not certified probability authority.

Latest candidate state reproduced on 2026-09-23:

| Sport | Candidate family | Train / cal / test | Research screen | Publishable | Current interpretation |
|---|---|---:|---|---|---|
| NFL | `NFL_DYNAMIC_TEAM_STATE_LOGIT_V2` | 823 / 274 / 275 | **FAIL** | false | challenger rejected by current screen; incumbent NFL lane remains separate |
| NCAAF | `NCAAF_DYNAMIC_TEAM_STATE_LOGIT_V2` | 1839 / 613 / 613 | **PASS** | false | promising challenger; source review + full parity certification still required |
| NBA | `NBA_DYNAMIC_TEAM_STATE_LOGIT_V2` | 2833 / 944 / 945 | **PASS** | false | promising challenger; source review + full parity certification still required |
| WNBA | `WNBA_DYNAMIC_TEAM_STATE_LOGIT_V2` | 601 / 201 / 201 | **FAIL** | false | challenger not eligible to advance |
| NHL | `NHL_REGULAR_SEASON_LOGISTIC_V1` | 6128 / 1226 / 1226 | **FAIL** | false | calibrated candidate worsens proper scores; not eligible to advance |

All five remain:

- `lifecycle_state=CANDIDATE`
- `source_review_status=REQUIRED`
- `promoted=false`
- `active=false`
- `automatic_certification=false`
- `automatic_promotion=false`
- `probability_publishable=false`
- `can_execute=false`

A research-screen PASS therefore does not equal MLB-equivalent certification.

## Lane findings

### MLB

The mature governed lane remains champion-frozen while #777 / PR #778 investigates probability compression and calibration. It is the reference lifecycle, not a mathematical template for other sports.

### NFL

Production/validation evidence:

- fitted team/event artifacts: **10**;
- immutable event predictions: **33**;
- forward-shadow grades: **2**;
- forward calibration-health rows: **9**.

Latest incumbent health correctly reports:

- `graded_n=2`;
- `minimum_forward_required=100`;
- Brier `0.20349`;
- log loss `0.58824`;
- ECE `0.42302`;
- status `INSUFFICIENT_FORWARD_EVIDENCE`;
- recommendation `DO_NOT_CERTIFY_YET`;
- `can_execute=false`.

The separate dynamic team-state challenger also fails its research screen:

- calibrated Brier `0.25739` vs baseline `0.24991`;
- calibrated log loss `0.71967` vs baseline `0.69298`;
- ECE `0.13338` > screen limit `0.10`.

Correct disposition: preserve the existing NFL champion/research boundary, accumulate prospective evidence, and reject the weaker challenger. NFL still needs explicit final-pregame ledger, event-uncertainty semantics, dominance diagnostics and calibration intercept/slope evidence before full quality parity is established. The cross-sport quality overlay therefore must not reinterpret NFL's ordinary lane health as complete MLB-equivalent quality evidence.

### NCAAF / CFB

Legacy NCAAF-specific validation tables currently show:

- training games: **3,844**;
- source snapshots: **200**;
- training features: **0**;
- event feature snapshots: **0**;
- pregame evidence: **0**;
- fitted model artifacts: **0**;
- calibrator artifacts: **0**;
- predictions/outcomes: **0 / 0**.

That older pipeline is stalled between corpus ingestion and feature/model construction.

However, the newer D1 dynamic team-state research lane has a current NCAAF challenger that passes its research screen:

- train/cal/test: `1839 / 613 / 613`;
- calibrated Brier `0.18234` vs baseline `0.23680`;
- calibrated log loss `0.53994` vs baseline `0.66660`;
- ECE `0.05538`;
- market features used: false;
- source review still required;
- not active/publishable.

Correct disposition: do not describe NCAAF as having no research model; describe it as having **no certified governed winner authority yet**. The passing candidate must still satisfy the new parity diagnostics, source review, bridge/certification, immutable grading and event-uncertainty requirements.

### NBA

Persisted historical training corpus:

- **4,804** settled games;
- zero provenance-invalid rows in the audit;
- current legacy corpus ends `2023-04-02` and is therefore intentionally rejected by the 400-day freshness gate;
- zero persisted artifacts/calibrators in the legacy basketball-specialist tables.

The current D1 dynamic team-state challenger nevertheless exists and passes its research screen:

- train/cal/test: `2833 / 944 / 945`;
- calibrated Brier `0.23140` vs baseline `0.24491`;
- calibrated log loss `0.65431` vs baseline `0.68294`;
- ECE `0.03360`;
- market features used: false;
- source review required;
- not active/publishable.

The legacy basketball-maintenance path was also found to contain an acquisition defect: a transport-successful BallDontLie response with zero usable/new settled rows did not trigger the ESPN fallback, allowing the corpus to remain stale. PR #783 patches that decision and preserves successful-but-no-op hydration receipts when replay is blocked. The 400-day freshness requirement is unchanged.

Correct disposition: NBA has a promising Class C challenger and a repaired Class B hydration path, but no champion replacement until the shared quality contract and source/certification requirements pass.

### WNBA

Persisted historical training corpus:

- **1,041** settled games;
- zero provenance-invalid rows in the audit;
- current legacy corpus ends `2022-09-18` and is intentionally stale;
- zero persisted artifacts/calibrators in the legacy basketball-specialist tables.

The current D1 team-state challenger **fails**:

- calibrated Brier `0.24857` vs baseline `0.24675`;
- calibrated log loss `0.69936` vs baseline `0.68664`;
- ECE `0.12852` > `0.10` screen limit.

The shared request-dependent calibration path previously accepted an artifact that merely declared `health_status=PASS` and `certification_status=PASS`, provided its basic fields were numerically well formed. PR #783 now fail-closes WNBA binary calibration unless the artifact also carries:

- `quantitative_quality_status=PASS`;
- a versioned + SHA-256 identified quality policy;
- log loss;
- ECE;
- calibration intercept;
- calibration slope;
- maximum calibration-bin gap;
- explicit passed checks for Brier, log loss, ECE, intercept, slope and max-bin gap.

A legacy PASS-labeled artifact without this receipt raises `CalibrationArtifactInvalid` and is mapped by the bridge to typed `MODEL_INPUTS_INSUFFICIENT`, preserving the original scorer/error semantics. It is **not** converted to `MODEL_UNAVAILABLE`.

The same basketball hydration repair used for NBA applies to WNBA, but fresh data alone does not make the failing challenger acceptable.

Correct disposition: **challenger rejected / model improvement required**, and the request-dependent publication path is now explicitly blocked from treating an unquantified PASS label as certification evidence.

### NHL

NHL has substantial D1 research evidence:

- source events: **6,560**;
- D1 training rows: **6,128**;
- candidate artifacts: **16**.

Latest `NHL_REGULAR_SEASON_LOGISTIC_V1` candidate:

- train/cal/test: `6128 / 1226 / 1226` persisted counts (validation metric train split reports 3676 after internal split);
- raw Brier `0.24767`;
- baseline Brier `0.24956`;
- calibrated Brier **worsens** to `0.25087`;
- raw log loss `0.68865`;
- baseline log loss `0.69228`;
- calibrated log loss **worsens** to `0.69594`;
- ECE `0.03458`;
- research screen **FAIL**;
- historical reconstruction true;
- archived pregame snapshot claimed false.

This is exactly why parity cannot mean merely checking ECE or forcing certification: the calibration mapping degrades proper scores.

PR #783 applies the same strict quantitative binary-calibration receipt contract to NHL as WNBA. A simple PASS label cannot make an NHL package publication/rank eligible without the required quantitative metrics/checks.

Correct disposition: **challenger rejected / calibration and model-resolution experiment required**. Preserve current non-publishable candidate state.

## Repair in PR #783

`v17/team_event_probability_quality_parity.py` adds a same-shaped, fail-closed quality contract for all six sports. It is installed after operational readiness and before outer sport-parity capture.

The overlay:

- never computes a sporting probability;
- never promotes a model;
- never changes a coefficient;
- exposes exact parity blockers instead of manufacturing capability;
- normalizes `CFB -> NCAAF`;
- requires Brier, log loss, ECE, calibration intercept/slope and maximum bin gap rather than trusting a PASS label;
- blocks test-set selection leakage;
- distinguishes cohort reliability from event uncertainty;
- distinguishes early from final-pregame grading;
- requires dominance/margin diagnostics and untouched temporal evidence;
- preserves `V17_TERMINAL_REDUCER` and `can_execute=false`.

The PR additionally contains two active Class B reliability/governance repairs:

1. **NBA/WNBA historical acquisition recovery** — BallDontLie HTTP-success-but-empty/no-op evidence can fall back to newer ESPN settled rows without weakening provenance or the 400-day training-corpus freshness guard. Successful hydration receipts are preserved even when replay subsequently blocks.
2. **WNBA/NHL quantitative calibration enforcement** — request-supplied calibration artifacts cannot become governed solely from stored PASS labels. Missing quantitative evidence fails closed with typed `MODEL_INPUTS_INSUFFICIENT` blockers.

No new WNBA/NHL probability coefficients or calibration mapping are generated by this enforcement patch; it only raises the evidence required before an existing mapping may be treated as governed.

## Model-development work still required

- **NFL:** continue prospective grading; retain `INSUFFICIENT_FORWARD_EVIDENCE`; reject the current weaker team-state challenger; add full parity diagnostics and ledgers.
- **NCAAF/CFB:** extend the passing D1 challenger with calibration slope/intercept/max-bin diagnostics, complete source review, governed winner bridge, event uncertainty and immutable prospective grading before any promotion review.
- **NBA:** extend the passing D1 challenger with the same diagnostics; refresh legacy corpus through the repaired hydration path; complete source review/bridge/prospective grading.
- **WNBA:** refresh historical evidence through the repaired acquisition path, then build/replay stronger sport-specific challengers because the current team-state model fails proper scores and ECE.
- **NHL:** challenger/calibration redesign is required because current calibration worsens Brier/log loss; add prospective immutable pregame evidence before certification.

No sport may borrow MLB coefficients, probabilities, thresholds or distribution assumptions to satisfy parity.

## Terminal status

- Cross-sport governance parity slice: **PR_CREATED (#783)**.
- NBA/WNBA acquisition defect: **PR_CREATED (#783), targeted CI verified on the hydration/maintenance repair**.
- WNBA/NHL PASS-label calibration defect: **PR_CREATED (#783), typed fail-closed tests added; full PR CI required**.
- NBA D1 challenger: **EXPERIMENT_CREATED / PASSING RESEARCH SCREEN / NOT CERTIFIED**.
- NCAAF D1 challenger: **EXPERIMENT_CREATED / PASSING RESEARCH SCREEN / NOT CERTIFIED**.
- NFL dynamic challenger: **EXPERIMENT_CREATED / RESEARCH SCREEN FAILED**; incumbent lane remains fail closed on insufficient forward evidence.
- WNBA challenger: **EXPERIMENT_CREATED / RESEARCH SCREEN FAILED**.
- NHL challenger: **EXPERIMENT_CREATED / RESEARCH SCREEN FAILED**.
- Full #781 closure: **UNFINISHED** until every lane satisfies the shared quality evidence contract with its own certified sport-specific artifacts and prospective evidence.