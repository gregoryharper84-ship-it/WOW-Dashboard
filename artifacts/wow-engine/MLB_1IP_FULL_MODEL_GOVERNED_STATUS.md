# MLB 1IP — FULL_MODEL_GOVERNED implementation status

As of: 2026-09-01

Governance: `docs/wow/contracts/canonical/WOW-PATCH-2026-09-01-MLB-1IP-FULL-MODEL-GOVERNED.md`
(registry entry: `WOW-PATCH-2026-09-01-MLB-1IP-FULL-MODEL-GOVERNED`, `docs/wow/contracts/registry.yaml`).
The immutable v3 skill (`WOW-MLB-FIRST-INNING-PITCH-COUNT-EXPERT-SKILL-V3`)
is unchanged and stays `declared_status=TEST_ONLY` per this registry's own
promotion rules; this patch layers project authority on top of it, the same
mechanism `WOW-PATCH-2026-08-01-1IP-EFFICIENCY-GAP-ENFORCE` and
`WOW-PATCH-2026-08-01-PITCH-COUNT-DIRECTIONAL-ASYMMETRY` already use.

## What is implemented

```text
artifacts/wow-engine/mlb_1ip_specialist.py
  classify_lineup_evidence(): OFFICIAL_CONFIRMED / PROJECTED_OR_RECONSTRUCTED
    (FULL or PARTIAL_SUFFICIENT) / INSUFFICIENT_TO_RECONSTRUCT
  simulate_1ip_event_tree(): batter-by-batter Monte Carlo event tree, ported
    from artifacts/flask-scoring-api/gate_engine/mlb/ip1_event_tree.py
    (commit 35bcfa3), extended to report P_BF_3/4/GE_5,
    P_MORE_GIVEN_BF_3/GE_4, P_MORE, P_LESS, fourth_batter_dependence_share,
    projection mean/median/std, and a Wilson-interval lower/upper bound --
    all fields wow_1ip_contract_status requires.
  score_mlb_1ip(): applies uncertainty widening (1.15x std for a full
    projected top four, 1.30x for partial-but-sufficient) when lineup is
    not officially confirmed, holds every provisional result at
    MODEL_QUALIFIED_HOLD with final_refresh_required=True, and preserves a
    completed result under a missing-market-evidence blocker rather than
    erasing it.
  starter_changed(): row-local staleness check for SLATE_PURGE.

artifacts/wow-engine/pick_request_runtime.py
  New RawPropEvidence.lineup_evidence (Optional[dict], default None) --
  additive, does not change any existing stat type's schema or behavior.
  New dedicated branch for canonical_stat == 1ST_INNING_PITCHES_THROWN,
  inserted after the existing (unmodified) specialist-routing and
  certified-artifact-route checks:
    1. mandatory Scout -> Research barrier, driven synchronously in-process
       against agent_runtime.runner_scout_research.execute_envelope -- the
       same primitive the v17 team-event convergence work
       (v17/team_event_request_runtime.py) already reuses. Not a second
       Scout/Research implementation.
    2. starter_changed() row-local SLATE_PURGE check.
    3. score_mlb_1ip() with lineup_evidence extracted from the row.
  A generic-code fallback that would otherwise default to MODEL_UNAVAILABLE
  (pick_request_runtime.py's _terminal()) is avoided here by score_mlb_1ip
  always supplying an explicit terminal_label ("REJECT_DATA_QUALITY" or
  "MODEL_QUALIFIED_HOLD"), which _terminal() already knows to read from its
  detail payload -- no reducer or terminal-label vocabulary changed.

docs/wow/contracts/canonical/WOW-PATCH-2026-09-01-MLB-1IP-FULL-MODEL-GOVERNED.md
docs/wow/contracts/registry.yaml (+1 entry)
docs/wow/contracts/README.md (+1 index row)
```

## What this does NOT implement (and why)

```text
NO_LIVE_ACQUISITION_LAYER
  There is no Baseball Savant / MLB Stats API client in artifacts/wow-engine
  computing pitcher_bf_distribution or a projected top four from live data
  (the legacy artifacts/flask-scoring-api/gate_engine/mlb/savant_1ip_ledger.py
  does this, but is not deployed -- see render.yaml, which lists only three
  services, all rooted at artifacts/wow-engine). score_mlb_1ip's contract
  begins once that evidence exists as input; building a live acquisition
  client is separate, larger, undone work.

NO_CERTIFIED_MODEL_ARTIFACT
  public.wow_prop_fitted_model_artifacts has no promoted, active row for
  (MLB, 1ST_INNING_PITCHES_THROWN). api_prod_market._prop_route_artifact()
  / wow_prop_certified_model_artifact are unmodified and still correctly
  return PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND for this route, so in the
  real deployed system a 1IP pick-request row is held at that existing gate
  before ever reaching mlb_1ip_specialist.py. The integration tests mock a
  hypothetical future certified artifact specifically to prove the
  orchestration wiring; this status file does not claim otherwise.

NO_PROPS_FINAL_REFRESH_RUNNER
  final_refresh_required=True is set correctly on every provisional-lineup
  result, but there is no scheduled job that watches for that flag and
  re-invokes score_mlb_1ip once the official lineup posts (the TEAM/EVENT
  side has this via wow_run_event_final_refresh; props do not). A test
  proves the underlying primitive is rerun-safe (re-scoring with confirmed
  evidence correctly drops the flag and updates lineup_evidence_state), but
  the scheduler/runner itself is unbuilt.

UNCALIBRATED_UNCERTAINTY_WIDENING
  The 1.15x/1.30x std-widening multipliers are explicit, documented
  orchestration defaults chosen for auditability, not a fitted or
  calibrated adjustment -- calibration_method is honestly labeled
  "UNCALIBRATED_INTERVAL_WIDENING_V1" and probability_publishable is always
  False. A real calibrated haircut is only available once a certified
  artifact exists.
```

## Tests and results

```text
artifacts/wow-engine/test_mlb_1ip_specialist.py            15 passed
artifacts/wow-engine/test_pick_request_1ip_integration.py   8 passed
Full artifacts/wow-engine suite                            628 passed, 3 skipped, 0 failed
  (605 -> 628: +23, exactly the new tests above; no other test changed)
artifacts/flask-scoring-api/gate_engine/tests/test_contract_registry.py
                                                             31 passed
```

## can_execute / ceiling status

```text
CAN_EXECUTE = false                          (unchanged, checked in every test)
MLB_1IP_LANE_STATUS = FULL_MODEL_GOVERNED    (orchestration semantics; see patch)
MLB_1IP_PUBLICATION_CEILING = MODEL_QUALIFIED_HOLD  (unchanged from TEST_ONLY lane)
MLB_1IP_CERTIFIED_ARTIFACT = NOT_FOUND       (real gate, unmodified, unfabricated)
V17_CUTOVER_ALLOWED = false
```
