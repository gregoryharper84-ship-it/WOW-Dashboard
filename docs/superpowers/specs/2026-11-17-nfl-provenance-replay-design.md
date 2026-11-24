# NFL provenance-first retrospective challenger — design

**Problem:** The merged `opponent_scenario_bridge` correctly refuses to invent fitted game-state distributions. The existing 2026 NFL prior-feature data are largely retrospectively materialized, so treating a historical challenger replay as prospectively frozen is scientifically false.

**Scope:** Add a pure, auditable experiment that fits a deterministic research baseline from established prior-only NFL P2 features (2021–24), calibrates on 2025, evaluates on 2026, pairs only timestamp-valid incumbent receipts, reports clustered uncertainty, and labels pre-event row status without any production output.

**Inputs:** JSON export of the existing `wow_nfl_pregame_feature_rows` with exact kickoff and provenance, plus a map of one selected incumbent snapshot per canonical event. Verify source digest syntax, prior games strictly earlier, schema/finite inputs and selected snapshots predating actual kickoff. Date-only/current-season historical backfills remain `RETROSPECTIVE_PRIOR_ONLY` or `PREGAME_TIMESTAMP_UNVERIFIED`.

**Non-goals:** No duplicate training lane, no score-state probabilities from moneyline odds, no player-prop tail invention, no automatic capture/credential access, no schema mutation, no model promotion, no wagering, and no claim of independent scientific approval.

**Outputs:** Deterministic research JSON with fit/cal/holdout sizes, paired Brier/log loss/accuracy, by-week bootstrap uncertainty, data-lineage counts and `certification_eligible=false`. Preserve incumbent and challenger separately and do not replace immutable pregame receipts.

**Risk:** One sample of only 10 weeks and many retrospectively locked rows cannot validate positive lift even when point estimates improve. Evaluation requires independent evidence and fresh prospective snapshots before production release.
