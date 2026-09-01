# WOW-PATCH-2026-09-01-MLB-1IP-FULL-MODEL-GOVERNED

Status: `ACTIVE` (governance decision only — does not itself certify a fitted
model artifact, activate live execution, or lift `can_execute=false`)

## Authority

This patch is issued by WOW v16 Clean Core project governance on 2026-09-01,
in response to a live-run finding that MLB `1ST_INNING_PITCHES_THROWN`
evidence-acquisition/orchestration incorrectly treated pending official
lineup confirmation as equivalent to no controlling specialist being
available.

This patch does **not** edit or supersede the immutable canonical skill
`docs/wow/contracts/canonical/wow-mlb-first-inning-pitch-count-expert-SKILL-v3.md`.
That document's `declared_status` remains `TEST_ONLY` exactly as originally
authored — this patch changes the project's authority status for the lane,
the same mechanism already used by
`WOW-PATCH-2026-08-01-1IP-EFFICIENCY-GAP-ENFORCE` and
`WOW-PATCH-2026-08-01-PITCH-COUNT-DIRECTIONAL-ASYMMETRY`, both of which layer
governance on top of the v3 skill without editing it.

## Promotion

```text
market_family = MLB_1IP_PITCH_COUNT (1ST_INNING_PITCHES_THROWN / FIRST_INNING_PITCHES_THROWN)
controlling_specialist = wow.mlb-first-inning-pitch-count-expert
supporting_specialist = wow.mlb-pitcher-failure-path-expert

lane_status: TEST_ONLY -> FULL_MODEL_GOVERNED
```

`FULL_MODEL_GOVERNED` means this lane runs the complete gate chain (Scout,
Research, controlling specialist, calibration, market economics, exposure,
final refresh, immutable write, reconciliation, terminal reducer) like any
other governed MLB prop lane. It does **not** mean a certified fitted-model
artifact exists yet — `public.wow_prop_fitted_model_artifacts` still has no
promoted, active row for `(MLB, 1ST_INNING_PITCHES_THROWN)`, and
`wow_prop_certified_model_artifact` will correctly keep returning
`PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND` until one is certified through the
existing, unmodified promotion process (`CANDIDATE -> SHADOW ->
PROSPECTIVE_CERTIFIED/CHAMPION`, real training data, real validation
metrics). This patch authorizes the orchestration semantics below; it does
not fabricate or bypass that certification gate.

The `MODEL_QUALIFIED_HOLD` ceiling from the v3 skill's TEST_ONLY lane
carries forward as the default maximum publication state for a row scored
under provisional (projected/reconstructed) lineup evidence — see below.
This is a continuation of an existing ceiling, not a new restriction.

## Root-cause finding this patch addresses

Evidence-acquisition/orchestration code (`pick_request_runtime.py`) has one
generic fallback: any acquisition/evidence-completeness code without an
explicit `terminal_label` in its detail payload resolves to
`terminal_label=MODEL_UNAVAILABLE` via `prop_terminal_reducer_v2.py`'s
`EVIDENCE_BLOCKERS` / `model_evaluated=False` branch. This is correct in
general (mandatory evidence genuinely missing before any model evaluation
*should* read as unavailable), but it does not distinguish "the official
lineup has not posted yet" from "no reconstruction was ever attempted." A
1IP row where the starter is confirmed and an approved projected lineup
exists was falling into this same generic fallback with no chance to run
the specialist on the projected data.

## Binding orchestration semantics

```text
official_lineup_status == CONFIRMED
  -> lineup_evidence_state = OFFICIAL_CONFIRMED
  -> standard uncertainty

official_lineup_status == TBD
and starter_status == CONFIRMED
and an approved projected/reconstructed top four with sufficient
    batter-level P/PA and event-tree inputs can be assembled
  -> lineup_evidence_state = PROJECTED_OR_RECONSTRUCTED
  -> specialist_run = REQUIRED
  -> uncertainty_widening = ACTIVE
  -> maximum_ceiling = MODEL_QUALIFIED_HOLD
  -> final_refresh_required = true

official_lineup_status == TBD
and mandatory specialist inputs (starter status, or a projected top four
    with sufficient batter-level inputs) remain unobtainable after all
    approved acquisition/reconstruction attempts
  -> REJECT_DATA_QUALITY / an explicit input-failure blocker
  -> NOT MODEL_UNAVAILABLE

starter changed/stale after evidence was captured
  -> SLATE_PURGE, reason=STARTER_CHANGED
  -> row-local only; sibling rows in the same batch are unaffected

the controlling specialist's own code/service genuinely fails to execute
  -> MODEL_UNAVAILABLE

a completed sporting probability exists but market/money evidence
(payout, settlement, price) is missing or unresolved
  -> the completed model output is preserved
  -> MODEL_QUALIFIED_HOLD or a stricter upstream ceiling, never erased
     back to MODEL_UNAVAILABLE
```

Hard invariant:

```text
MLB_1IP: PROVISIONAL_EVIDENCE != MODEL_UNAVAILABLE

MODEL_UNAVAILABLE is permitted only when:
1. the controlling specialist genuinely fails to execute, or
2. mandatory specialist inputs remain unobtainable after all approved
   acquisition/reconstruction paths.
```

## What this patch does not authorize

```text
does_not_fabricate_fitted_parameters = true
does_not_fabricate_calibration = true
does_not_fabricate_lineup_or_batter_evidence = true
does_not_certify_a_model_artifact = true
does_not_change_can_execute = true   # remains false
does_not_change_market_price_availability_coupling = true  # market
    price availability remains independent of sporting-probability
    availability; neither erases the other
does_not_weaken_batter_by_batter_event_tree_requirement = true
does_not_weaken_bidirectional_more_less_audit = true
does_not_weaken_fourth_batter_dependence_requirement = true
does_not_weaken_dynamic_calibration_or_final_refresh_requirement = true
```

## Implementation reference

`artifacts/wow-engine/mlb_1ip_specialist.py` implements this patch's
orchestration semantics (lineup-state classification, uncertainty widening,
terminal-label assignment) on the production entrypoint, reusing the
existing Monte Carlo event-tree method from
`artifacts/flask-scoring-api/gate_engine/mlb/ip1_event_tree.py` (ported, not
re-derived) and the mandatory Scout/Research barrier
(`agent_runtime.runner_scout_research.execute_envelope`, the same primitive
used by the v17 team-event convergence work). See that module's docstring
and `test_mlb_1ip_specialist.py` for the full behavioral contract and
regression coverage.

```text
can_execute = false
```
