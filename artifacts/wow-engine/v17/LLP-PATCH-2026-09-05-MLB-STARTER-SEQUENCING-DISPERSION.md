# LLP-PATCH-2026-09-05-MLB-STARTER-SEQUENCING-DISPERSION

```text
PATCH_VERSION=v17
STATUS=BACKEND_CONTRACT_INSTALLED_CHALLENGER_NOT_PROMOTED
APPLIES_TO=LLP_TEAM_BETTING_ENGINE / MLB full-game outright winner lane
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```

## Purpose

Promote MLB outright-win modeling from mean-run-centric scoring toward a fitted joint run-distribution model that explicitly owns starter outcome dispersion, offensive sequencing/run clustering, starter × offense interaction, bullpen handoff uncertainty, and candidate-specific calibrated uncertainty.

## Governing placement

```text
canonical event identity/status
→ critical starter + projected lineup lock
→ MLB feature acquisition
→ MLB starter dispersion layer
→ offensive sequencing layer
→ starter × offense cluster-interaction layer
→ bullpen handoff layer
→ full-game run-distribution simulation
→ raw independent win probability
→ canonical dynamic calibration
→ probability claim audit
→ event governor
→ probability leaderboard
→ final refresh
→ V17 terminal reduction
```

No post-hoc universal haircut is permitted. Sportsbook price, implied probability, external projections, and recent realized scoring may not substitute for the controlling fitted sporting model.

## Promotion boundary

The contract is installed additively. It does **not** certify or promote a fitted model by itself.

The current V2D champion remains untouched until an exact artifact exists with:

```text
model_family=MLB_STARTER_SEQUENCING_DISPERSION_V17
feature_schema_version=MLB_MSD_V17_FEATURES_V1
active=true
promoted=true
lifecycle_state=PROSPECTIVE_CERTIFIED|CHAMPION
```

Until then the new capability reports:

```text
model_capability=MODEL_UNAVAILABLE
reason=EXACT_FITTED_MSD_V17_ARTIFACT_NOT_CERTIFIED
```

This is intentional fail-closed behavior; it prevents the existing mean-run/shared-NB scorer from being relabeled as the new model.

## Mandatory feature acquisition

Starter: identity, handedness, confirmation timestamp, expected pitch-count/innings distribution, xwOBA/xSLG/xBA allowed, K%, BB%, whiff%, chase%, PutAway%, barrel%, hard-hit%, GB%, FB%, HR/contact profile, velocity/movement flags, rest/workload, times-through-order splits when supported.

Offense: projected batting order, batter event probabilities (K/BB/1B/2B/3B/HR/out by relevant split/pitch type when supported), contact-quality distribution, platoon composition, bench/late-substitution availability.

Context: projected-lineup handedness/availability, park/weather state, bullpen availability and leverage workload.

Selected artifact + missing/stale required field => `MODEL_INPUTS_INSUFFICIENT`, with every missing field named.

## Mandatory fitted outputs

Starter dispersion includes expected runs/variance, 0–1/2–3/4–5/6+ buckets, innings distribution, third-time-through, early hook, and catastrophic-start probability.

Sequencing includes offense expected runs/variance, 0–2/3–4/5+ buckets, 3+ run-inning probability, scoreless-first-5 probability, opponent-starter exit-before-5 probability, and sequencing concentration.

Interaction includes cluster interaction, multi-run-inning-before-bullpen, 4+/6+ starter runs conditional on lineup, favorite catastrophic failure, and underdog breakthrough probability.

Bullpen includes expected runs/variance, availability, leverage arms, 3+ run probability, handoff risk, and manager hook policy.

Full-game simulation must publish home/away run distributions, score-margin distribution, raw home/away win probabilities, and explicit favorite-loss/upset path probabilities. Two-outcome raw home + raw away must equal 1 within numerical tolerance.

## Dynamic calibration

Required outputs:

```text
raw_probability
calibrated_probability
lower_bound
upper_bound
calibration_method
calibration_version
model_version
source_snapshot_id
model_timestamp
```

Uncertainty must be candidate-specific and expand with starter catastrophic tail, lineup uncertainty, bullpen variance, sequencing dispersion, and clustered-run interaction risk. Official probability ranking uses governed calibrated lower bound where required.

## Failure semantics

```text
no exact fitted artifact                         -> MODEL_UNAVAILABLE
artifact selected + missing/stale required input -> MODEL_INPUTS_INSUFFICIENT
artifact invoked + scorer failure                -> preserve MODEL_SCORER_FAILED/typed scorer status
malformed/nonfinite/inconsistent package          -> MODEL_OUTPUT_INVALID
material participant/context change              -> MODEL_RERUN_REQUIRED
sporting probability complete + odds failure      -> preserve probability; block market/value only
```

## Material changes requiring a full rerun

Probable-starter scratch/change, material projected-lineup change, meaningful park/weather change, material bullpen-availability change, or event identity/status change invalidate the prior package until a rerun completes.

## Audit fields

The backend audit table persists model component versions plus `p_starter_4plus`, `p_starter_6plus`, `p_three_plus_run_inning`, `p_scoreless_first_5`, `catastrophic_failure_probability`, `sequencing_concentration_index`, `handoff_risk`, `calibration_width`, feature snapshot timestamp, and participant snapshot timestamp.

## Challenger acceptance

Promotion is calibration-first on strict out-of-sample/walk-forward MLB data: Brier score, log loss, calibration slope/intercept, reliability buckets, 4+ starter-run calibration, 3+ run-inning calibration, favorite upset rate by catastrophic-risk decile, starter-quality-tier discrimination/calibration, and late starter/lineup-change performance.

Win rate or ROI alone can never promote the challenger.

## Regression suite

The repository test module `v17/test_mlb_starter_sequencing_dispersion_contract.py` encodes RT-MSD-001 through RT-MSD-018 at the contract/audit layer. Behavioral promotion still requires real fitted challenger outputs and walk-forward evidence; synthetic contract tests are not model certification.
