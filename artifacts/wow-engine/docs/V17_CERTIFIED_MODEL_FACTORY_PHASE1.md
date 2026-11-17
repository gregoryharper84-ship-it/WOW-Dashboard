# V17 Certified Model Factory — Phase 1 Implementation

**Contract:** Standardize the process, specialize the sporting mathematics, never automate independent approval.

## Existing authority, not duplicated

The factory is a research receipt adapter, not a new scheduler, fitted model,
certifier, deployment mechanism, or terminal publisher. It preserves:

- custom_gpt_identity=WOW_BETTING_ENGINE
- runtime_generation=V17_ACTIVE
- terminal_authority=V17_TERMINAL_REDUCER
- can_execute=false
- DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true

Existing source: model_source_entitlements.py; native NBA
basketball_model_maintenance.py; native NHL nhl_model_maintenance.py;
candidate_certification_evidence.py; multisport_team_event_calibration.py;
fitted_team_event_certification.py; registry audit and capability manifest.
Do not replace any with a generic fitter or market-implied probability.

## Eight stages

| Stage | Purpose | Acceptance authority |
|---|---|---|
| 1 | Acquire legally usable, source-hashed historical records | Native sport hydrator and entitlement |
| 2 | Rebuild pregame, point-in-time features | Native sport adapter and chronology checks |
| 3 | Fit genuine specialist candidate | Sport-owned fitted model family |
| 4 | Walk-forward/holdout validation | Scientific validation and frozen replay |
| 5 | Calibrate and assess conservative bounds | Sport-specific fitted calibrator contract |
| 6 | Prepare reproducible candidate evidence | Existing source review and replay path |
| 7 | Independent *model* certification and governed protected-source release | Certified-model scientific/source-rights contract for publication; owner policy #1830 and three native ruleset checks for routine source merge |
| 8 | Verify live Render, Action, Supabase, terminal/card path | Existing governed release/acceptance path, issue #1689 |

The new module only helps organize Stage 1–6 research evidence via existing
native maintainers. It cannot perform stages 7 or 8.

## Current exact recipes

- **NBA Winner:** call run_basketball_model_maintenance(db, sports=("NBA",)),
  accept only one SHADOW_EVIDENCE_UPDATED row underneath COMPLETE.
  This is not evidence of a production champion.
- **NHL Winner:** call run_nhl_model_maintenance(db, training_code_sha=SHA),
  require CANDIDATE_EVIDENCE_UPDATED with a complete
  NHL_CANDIDATE_PREFLIGHT_READY receipt, matching cohort counts,
  and a nonempty persisted D1 CANDIDATE identity/version,
  and research_screen_pass=true. A source review still marked
  REQUIRED must be completed independently before certification.
- All other sport/market keys remain unsupported until their exact
  specialist and evidence contract are added. A winner model never certifies
  spreads, totals, 1X2, field outcomes, or player props.

Each receipt is explicitly NOT_CERTIFIED, review NOT_PERFORMED,
production NOT_PERFORMED, rank_eligible=false,
probability_publishable=false, can_execute=false, with an
integrity SHA-256 over allowlisted summary metadata. A caller-declared
source SHA is not an exact-head attestation or GitHub/Render proof.

## Phase-1 tests and proof

Run from artifacts/wow-engine:

    python -m pytest -q test_v17_certified_model_factory.py

Focused isolated unit tests cover both sport paths, no cross-adapter
fallback, unsupported sport/market, bad SHA/seasons, typed blocker propagation,
failed NHL research screen, malformed/absent candidate preflight, persisted NHL candidate identity, NBA nested replay readiness, nested
promotion/publication flags, and exception sanitization. These are mocked
contract tests, not DB-backed replay, full CI, model certification or
production verification.

Before merging **inert Stage-A research source**, verify exact changed files,
provenance, native NBA/NHL and frozen-replay regressions, negative source and
chronology cases, and all three authenticated exact-head GitHub ruleset #24907422
required checks. Use owner-authorized protected merge under active policy #1830
and document rollback. Optional QA/Release App results remain genuine diagnostic
evidence rather than routine premerge prerequisites. Never forge reviews or check
results. Independently reviewed source-rights, candidate-bound replay,
sport-specific calibration and scientific certification are **separate mandatory
requirements for model promotion or numerical publication**, not achieved by
merging the research adapter.

**Next research action:** on an authorized validation DB, execute exact
sport-native maintenance for NBA Winner and NHL Winner; compare returned
candidate/source/calibrator hashes to frozen source records and retain
typed holds. When this same common adapter works with two distinct native
specialists, extend to NFL/NCAAF/NCAAB then other active sports. MLB new
development remains deferred during the owner-directed off-season priority.

**After authorized release:** inspect exact protected main and both Render
LIVE SHAs, exercise a real Action route, read the authoritative Supabase
prediction row and V17_TERMINAL_REDUCER receipt. State the scoreboard
delta: model_evaluated/eligible, rank eligible, and production SHA parity.
A green test or model factory receipt does not imply that any probability
was published.

Tracked program work: #455 for sport-specific certified specialists and
#1689 for independently accepted release/user-path evidence.
