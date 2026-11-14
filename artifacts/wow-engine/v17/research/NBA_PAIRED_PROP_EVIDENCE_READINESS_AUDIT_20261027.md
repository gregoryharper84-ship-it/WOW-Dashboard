# NBA paired-prop research — authenticated data readiness correction
Date: 2026-10-27 America/Chicago
Status: READ-ONLY AUDIT; RESEARCH_ONLY; NOT MODEL_CERTIFIED
Repository: gregoryharper84-ship-it/WOW-Dashboard
Target: PR #2196 and existing NBA candidate lane #1988; universal prop forward-evidence #491
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false

## Critical correction

An earlier inventory of the connected **wow-engine-validation** project used schema-list row *estimates* which showed 0 for several populated tables. **That was incorrect.** Subsequent explicit read-only SQL `COUNT(*)` queries on the same project returned the precise counts below. Never infer an empty table from the metadata row estimate; explicit SQL counts are authoritative for this point-in-time observation. This correction supersedes the PR's earlier claim of no NBA training games and no general prop ledger.

| Live read-only SQL scope | Rows | Interpretation |
| --- | ---: | --- |
| public.wow_nba_training_games | 8,919 | NBA **team game** results/history, 7 seasons, 2019-10-22 to 2026-06-13. Not pregame player props |
| public.wow_nba_pregame_feature_rows | 8,747 | NBA **team-event** pregame feature rows, not player/offer model receipts |
| public.wow_predictions | 4,625 | Prop predictions across MLB 4,282 + NFL 343; **NBA 0** |
| public.wow_outcomes | 4,145 | Prop settlements linked to MLB 4,122 + NFL 23; **NBA 0** |
| public.wow_prop_fitted_model_artifacts where sport='NBA' | 8 | Research NBA candidate artifacts, all `CANDIDATE` / unpromoted / inactive / not publishable |
| public.wow_prop_evidence_snapshots where sport='NBA' | 0 | No NBA evidence in this particular table |
| public.wow_v17_daily_run_row_detail where lane='PROPS' | 390 | MLB 355 + NFL 35; **NBA 0** |
| public.wow_fantasy_score_source_cache where sport='NBA' | 1 | Research-only raw source cache: SportsDataverse ESPN NBA player boxscore 2026 CSV, HTTP 200, 17,729,581 bytes |
 
These counts were returned by read-only `SELECT COUNT(*)`, grouped table queries, and source cache metadata reads. They are **not** a statement about all possible independent deployments, every database in the ecosystem, or coverage after this audit.

## NBA artifact scope observed

Eight NBA candidate rows: POINTS, REBOUNDS, ASSISTS, PRA, POINTS_ASSISTS, POINTS_REBOUNDS, REBOUNDS_ASSISTS and FANTASY_SCORE. Seven P/R/A-related rows use `NBA_PLAYER_PROP_EMPIRICAL_RESIDUAL_JOINT_V1`; Fantasy Score has a separate candidate family. Every returned row is `CANDIDATE`, `candidate_research_active=true`, `promoted=false`, `active=false`, `probability_publishable=false`.

This is evidence that research candidate infrastructure exists. It is **not** evidence of independently calibrated exact-line joint-prop forecasts. The raw 2026 ESPN-derived boxscore CSV supplies observed player results, not frozen pregame model probabilities or actual sportsbook prices. Outcome-only rows cannot substitute for immutable prediction receipts.

## Minimal existing-source integration path (do not clone another model)

1. **Historical observation**: authenticate source hash, rows, player/team/event IDs and timestamp in the cached 2026 player boxscore. Normalize PTS/REB/AST to *one official game-player observation*. Preserve source/retrieval provenance and distinguish postgame `actual` from pregame `forecast`.
2. **Pregame evidence/forward collection**: reuse existing NBA P/R/A research candidate (v17/nba_scalar_fitted_candidate.py) and exact-route forward-evidence lane. For each pregame event capture both exact prop offers, literal thresholds/directions/platform settlement IDs, model version, all model inputs/cutoffs, actual pregame candidate joint forecast, and contemporaneous calibrated leg probabilities if available. **The currently uncali­brated candidate may accumulate research evidence only**, not governed rankable probability.
3. **Settlement**: route each exact immutable offer to the authoritative official player/game outcome *after* event start. Keep DNP/void/push semantics; do not backfill missing pregame probabilities from settled stats.
4. **Joint pair replay**: only feed `nba_joint_thesis_replay.py` with complete frozen, authenticated two-leg records. Its comparator `p1*p2` is a research benchmark, not an official probability. Without complete pregame numeric packages, return `DATA_UNOBTAINABLE` for model comparison rather than producing a score.
5. **Card pricing**: separate later study on AB/AC/BC/ABC ticket structure. Require four exact timestamped prices including correlation adjustments; raw player boxes cannot produce sportsbook EV.
6. **Certification**: fitted-calibration and heldout exact-line joint quality remain Class C and require independent governance. No migration, no production write, no model promotion, no wager/execution.

## Precise remaining missing evidence

- **NBA player-prop immutable pregame prediction/outcome pairs** in the inspected governed ledger: none at audit time.
- **Exact pregame joint-thesis forecasts** with model provenance and calibration that this research replay can compare: not established.
- **Exact published four-ticket sportsbook prices** for the third-party strategy: not established.
- Historic observed game outcomes are available and can support a separate shadow research cohort, but may **not** be dressed up as a retroactive winning model.

## Recommended ownership

- P0 existing NBA source lane #1988: capture the missing forward exact-line NBA props and official outcomes with no duplicate model.
- Universal forward collection #491: test persistence and exact player+game identity of immutable prop evidence.
- Post-STANDARD_OPERATOR Class C #1224: independently research whether matchup-conditioned joint-prop probabilities outperform naive products before certification.
- PR #2196: remain read-only and research-only; accept historical records only when they already existed *before* the event.

Do not weaken `DEPENDENCE_UNQUANTIFIED_SAME_EVENT` or common-hinge exposure requirements to make the four-ticket proposal pass. `V17_TERMINAL_REDUCER` is the sole terminal authority; `can_execute=false`.
