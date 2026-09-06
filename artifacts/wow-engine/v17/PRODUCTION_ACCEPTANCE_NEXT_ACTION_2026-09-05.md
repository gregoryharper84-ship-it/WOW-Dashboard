# V17 Prop Family Production Acceptance — Next Action

The connected V17 ledger proves the following current state:

- `PITCHER_STRIKEOUTS`: evidence acquisition/hydration is proven and governed predictions exist.
- `1ST_INNING_PITCHES_THROWN`, `PITCHING_OUTS`, `STRIKES_THROWN`, `BALLS_THROWN`, `PLATE_APPEARANCES`: active certified artifacts exist, but no `wow_prop_evidence_snapshots` rows are currently present for these stat types.

Therefore the next production task is **not** to alter probability math or certification. It is to make the canonical acquisition path emit immutable `PROP_EVIDENCE_V1` rows for each exact supported stat type, then drive one real pregame row through `/score-pick-request`.

## Required acceptance sequence per family

1. Discover a real pregame prop with exact event/player/stat/line identity.
2. Resolve official event identity before start.
3. Hydrate the family-specific evidence contract.
4. Persist `wow_prop_evidence_snapshots` with `hydration_status=PASS` and no blockers.
5. Invoke canonical `/score-pick-request` exactly once for that row.
6. Require server-owned specialist and exact artifact resolution.
7. Require fitted distribution + MORE/PUSH/LESS package.
8. Require calibration and lower/upper bounds where the lane contract requires them.
9. Require immutable `wow_predictions.prediction_id`.
10. Preserve Phase-A money/final ceilings independently from sporting probability.
11. Preserve `can_execute=false`.

Do not classify registry-only or unit-test-only support as production proof.
