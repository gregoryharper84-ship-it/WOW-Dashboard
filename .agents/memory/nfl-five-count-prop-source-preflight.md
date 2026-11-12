# NFL missing count-prop source preflight — 2026-11-11

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_SOURCE_COUNT_INVALID; NFL_PROP_SOURCE_COUNT_EXCEEDS_OPPORTUNITY; NFL_PROP_SOURCE_DUPLICATE_PLAYER_EVENT
- **mechanism:** A missing/nonfinite raw count, a count inconsistent with the matching opportunity, or a duplicate player/game source row can corrupt independent pregame target and history construction. A generic fillna(0) would hide this defect.
- **detection_point:** Frozen source-input validation, before any fitting or artifact creation. An offline candidate may not cross into serving without subsequent provenance and certification.
- **fix:** Strict source-column/count/identity validation; separate fit and holdout cohorts; all emitted artifacts carry candidate-only false publication/execution flags.
- **prevention_artifact:** artifacts/wow-engine/tests/test_nfl_missing_prop_candidates.py (15 RED→GREEN verified offline unit tests plus synthetic CLI exercise).
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/tree/engineering/nfl-five-count-candidates-20261111
- **production_evidence:** Not established; synthetic exercise only. Hosted exact-head CI/independent approval/deploy/live receipts still required.

## Follow-up source-integrity review (PR #2605)

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_SOURCE_SEASON_MISMATCH; NFL_PROP_SOURCE_DUPLICATE_PLAYER_EVENT; NFL_PROP_SOURCE_OUTPUT_COLLISION
- **mechanism:** Source bytes can drift between hash and a second parse; filename-only season checks accept mislabelled/empty inputs; repeated player-week rows produce same-week feature leakage; dropping observed zero opportunities suppresses low-tail outcomes; output aliases can overwrite immutable input.
- **detection_point:** Before offline candidate fitting or publication into any downstream training artifact.
- **fix:** Parse the same hashed BytesIO; validate every named year; enforce NFL REG Weeks 1–18 and one player/week; retain valid observed zero-outcome games; reject symlink/inode output collisions.
- **prevention artifact:** tests/test_nfl_missing_prop_candidates.py (24 local regression tests, 7 new RED→GREEN failures plus default-minimum/provenance checks). All candidate artifact authority remains false.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** Unverified; new review head requires exact-head CI and independent approval; no actual real-season/forward evaluation yet.

## Frozen source digest preflight (2026-11-11)
- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_SOURCE_DIGEST_MISMATCH
- **mechanism:** A self-computed provenance hash without comparison to a trusted digest allows modified-but-valid source rows to fit under an authentic filename.
- **detection_point:** Source loader before parsing or fitting.
- **fix:** Pin 2021-2025 nflverse release asset digests, compare identical read bytes in the research-only CLI and preserve inactive, not-publishable authority.
- **prevention_artifact:** tests/test_nfl_missing_prop_candidates.py with two negative-first release-digest tests. Original: 2 RED; patched: 27 targeted PASS.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; no actual release bytes fitted or independently certified, can_execute=false.

## Authoritative-hash attestation and unidentified source records (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_SOURCE_UNATTRIBUTED_COUNT_NONZERO_OR_MISSING; NFL_PROP_SOURCE_IDENTITY_INVALID; NFL_PROP_SOURCE_DIGEST_MISMATCH
- **mechanism:** A caller-selected matching SHA-256 was mislabeled as verification of the published nflverse release; real 2021 weekly source reportedly has 18 identity-blank REG rows, so unqualified all-row identity validation aborts all five research fits. Silently assigning identities or indiscriminately dropping rows would be a worse data-integrity error.
- **detection_point:** The source reader before fitting, when provenance claims and player-week identity completeness are observable.
- **fix:** Separate digest-match evidence from official release-hash verification. Conditionally quarantine only unnamed, non-QB/RB/WR/TE, all-zero modeled-stat records with a valid game/week and leave exact source-row pointers and SHA-256 audit receipts. Any nonzero/missing target, name, eligible role, or invalid event identity still fails closed. No source quarantine is independently adjudicated, and no model is publishable.
- **prevention artifact:** tests/test_nfl_missing_prop_candidates.py (new 18-row shape case, six negative identity/count/date variants, and caller-vs-authoritative digest flag check); new code remains unmerged and hosted review pending.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; no published real-source training, no model certification, can_execute=false.

## Missing offensive participant-game coverage — 2026-11-11

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_PARTICIPATION_SOURCE_DIGEST_MISMATCH; NFL_PROP_PARTICIPATION_CROSSWALK_CONFLICT; NFL_PROP_PARTICIPATION_SNAP_EMPTY
- **mechanism:** Weekly count-stat source contains recorded events, not necessarily every player who logged offensive snaps. Inner-only source history can omit participants who had zero recorded opportunities, biasing count targets and L5/L10 histories. An empty snap cohort can also produce a false 0/0 complete-diff success.
- **detection_point:** Historical source completeness audit, before fitted count-prop candidate evaluation and model promotion.
- **fix:** Offline research-only `audit_nfl_count_prop_participation.py` compares pinned 2021–2025 NFL summaries against pinned game-level snap records via PFR-to-GSIS player crosswalk. Never fill a missing stat with 0 or join by player name; report exact unmapped and missing game identities, and reject malformed/incomplete sources. Empty/offseason source is a typed HOLD.
- **prevention_artifact:** `tests/test_nfl_count_prop_participation_audit.py` includes missing eight-snap player-game, wrong game ID, crosswalk conflict, source digest, duplicate row, missing mapping and empty-source regressions. Tests require hosted exact-head validation; tool source CSVs unavailable in this environment.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None. Audit is offline, source rights separately reviewed; no certification or deployment authority, can_execute=false.

## Snap-field typed validation and cohort-scoped ID ambiguity — 2026-11-11

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_PARTICIPATION_SNAP_INVALID; NFL_PROP_PARTICIPATION_CROSSWALK_CONFLICT
- **mechanism:** A negative offensive snap count was rejected only under generic :scope despite a field-specific fail-closed test; GitHub rapid affected run 43086324528 showed 636 passed / 1 failed. The players release also contains historical PFR namesake aliases outside the actual 2021–2025 offensive participant keyset; global crosswalk uniqueness can incorrectly reject an otherwise unambiguous cohort.
- **detection_point:** During exact-head rapid regression and before joining the pinned source crosswalk to eligible offensive snap participants.
- **fix:** Validate negative `offense_snaps` under the field's exact typed failure, and restrict duplicate PFR/GSIS crosswalk validation to IDs observed in the relevant vetted snap-participant cohort, retaining strict HOLD on any active conflict.
- **prevention_artifact:** `test_invalid_or_missing_snap_counts_fail_closed`, `test_unrelated_historical_pfr_collision_does_not_block_a_valid_cohort`, `test_active_pfr_collision_remains_fail_closed`. First mechanism reproduced in hosted run 43086324528, second in a local pandas reproduction; fresh exact-head hosted CI verification pending.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; candidate trainer and audit remain offline and non-publishable; can_execute=false.

## Official source acquisition editable-hash failure (2026-11-11)

- date: 2026-11-11
- typed_reason: NFL_PROP_ACQUIRE_DIGEST_MISMATCH; NFL_PROP_ACQUIRE_NETWORK_UNAVAILABLE
- mechanism: A mutable caller manifest can replace the digest and byte size while still yielding a false verified label. The uploaded source kit reproduced this in 2 negative tests and raw DNS errors in a third.
- detection_point: Before any source acquisition success receipt, fitting or 2025 holdout.
- fix: Source digests are now pinned independently of caller input; repository acquisition tool uses reviewed static trainer and snap-audit pins with published byte sizes, and rejects tampered files, untrusted redirects and network failures.
- prevention_artifact: tests/test_acquire_nfl_count_prop_sources.py; standalone acquisition kit V2 was 10/10 local PASS, while repo exact-head CI requires a fresh run.
- link: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- production_evidence: None; raw data DNS unavailable in this runtime, no certified fit, can_execute=false.

## Official redirect/midstream source-acquisition HOLD (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_ACQUIRE_REDIRECT_UNTRUSTED; NFL_PROP_ACQUIRE_NETWORK_UNAVAILABLE
- **mechanism:** Final-only redirect validation allows a request to reach a foreign host before rejection; interrupted response reads can leak raw OSError instead of typed network HOLD. This predicts unauthorized network contact and misleading diagnostic status while partial outputs are cleaned.
- **detection_point:** CodeRabbit negative-path source-acquisition review, before PR-scoped live-source observation was admitted to a GitHub runner.
- **fix:** Check HTTPS and approved host on each redirect before following; translate network read errors without swallowing disk-write failures; remove the unguarded urllib opener from the PR-scoped probe.
- **prevention_artifact:** tests/test_acquire_nfl_count_prop_sources.py — blocked 302 must never contact foreign host, allowed GitHub release 302 must complete, interrupted read must become typed HOLD and clean partial bytes. Isolated stdlib redirect simulations passed locally; targeted repo pytest remains unrun until hosted runner admission.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None. Runner was queued with zero jobs at observed c7552f0a; no actual real-source hash verification, fitted model or model activation. Safety unchanged: can_execute=false.

## Real frozen-source verification exposed participant-game selection gap (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_PARTICIPATION_SOURCE_DIGEST_MISMATCH; NFL_PROP_PARTICIPATION_CROSSWALK_CONFLICT; NFL_PROP_PARTICIPATION_SNAP_EMPTY (preflight codes); HOLD_UNRESOLVED_PARTICIPATION (audited research status)
- **mechanism:** Eleven authenticated source files can pass all digest and size checks while the player-count weekly source still fails population coverage against independently pinned positive offensive snap participants. Treating a missing count-stat record as a zero would introduce selection bias. Mac and hosted observations agree on 32,220 eligible participant-games, 28,178 matched, 3,994 mapped without player-stat rows, 48 without a PFR crosswalk identity. Whether a missing row represents zero opportunities or incomplete ingestion remains unproven.
- **detection_point:** Immediately after offline source acquisition and before independent fitting, train/calibration split or untouched 2025 holdout.
- **fix:** Keep the nonpublishable cohort HOLD; split diagnostic counts by season, position and observed snap band, independently separating mapped missing-stat records from unmapped identities. No zero imputation, fabricated GSIS identity, changed qualification threshold, registry wiring or execution permission.
- **prevention_artifact:** `scripts/audit_nfl_count_prop_participation.py` participation_breakdown, `tests/test_nfl_count_prop_participation_audit.py` per-stratum sum/unknown preservation tests, and PR-only `test_pr2605_observe_official_source_reconciliation` hosted receipt. Hosted rapid run 43098818705 at preceding head 5c19c835: 656 PASS, 1 diagnostic SKIP including source receipt; new head requires fresh CI.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605 and https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2020#issuecomment-6807280418
- **production_evidence:** None; verified source bytes are not fitted specialist or independent semantic approvals, can_execute=false, V17_TERMINAL_REDUCER.

## Pinned PBP event-presence classification — 2026-11-11

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_PBP_SOURCE_DIGEST_MISMATCH; NFL_PROP_PBP_EVENT_SCHEMA_INVALID; NFL_PROP_PBP_RECONCILIATION_DRIFT; HOLD_PBP_PRESENCE_ONLY
- **mechanism:** Frozen official weekly count-stat tables omit 3,994 mapped offensive-snap participant-games and 48 unmatched PFR identities. Season- and position-stratified evidence predicts a sparse weekly summary, but neither missing weekly rows nor missing play-by-play actor mentions establish verified count-zero outcomes. Blindly zero-filling risks label-selection bias and bad fitting.
- **detection_point:** Real-data source acquisition and canonical positive-offense-snap participation audit, before any candidate training/calibration/2025 untouched holdout.
- **fix:** Reuse the trusted approved-host downloader for the five released 2021–2025 PBP .csv.gz assets, SHA-256 + byte-size pin each exact archive, parse the verified same bytes in chunks, and distinguish present actor, absent actor, and absent game with explicit non-serving HOLD. Normalize known PFR-to-GSIS IDs using canonical rules and reject identity reconciliation drift. Never infer an absent event as zero.
- **prevention_artifact:** scripts/audit_nfl_count_prop_pbp_presence.py; 12 synthetic regression cases in tests/test_nfl_count_prop_pbp_presence.py; existing PR-branch-only rapid-runner source observation extended to attempt all five PBP archives and report a typed HOLD when unavailable. Hosted exact-head replay pending.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; no fitted specialist, promoted probability, independent semantic review, trust-root release authorization, production deploy or trade permission. can_execute=false, V17_TERMINAL_REDUCER.

## PBP positive-control follow-up (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** HOLD_PBP_PRESENCE_ONLY; NFL_PROP_PBP_RECONCILIATION_DRIFT
- **mechanism:** First hosted PBP receipt from 2021–2025 on PR #2605 head 95268d65 reported **3,994/3,994 mapped weekly-stat omissions without any actor appearance** across 1,359 PBP-covered games, with five gz digests / 95,753,616 bytes verified. This is plausible for sparse summary generation, but it could also arise if PBP actor IDs never match weekly-stat IDs. Prediction: source-aligned known weekly-stat participant-games will show many positive PBP actor matches.
- **detection_point:** Immediately after the first real PBP source-pair comparison, before interpreting any actor absence.
- **fix:** Add a positive-control population of canonical 28,178 known matched weekly-stat player-games; separately count PBP actor observed, not observed and game absent; emit `pbp_actor_id_alignment_observed`. A zero control cannot support a zero-stat hypothesis. No model/market inference.
- **prevention_artifact:** `test_no_positive_control_keeps_identity_alignment_unproven`, `test_observed_pbp_actor_still_does_not_authorize_zero_or_model` and existing hosted PBP source observation. New exact-head test and real positive-control receipt pending.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; evidence remains read-only, `can_execute=false`, V17_TERMINAL_REDUCER.

## Positive-opportunity-specific PBP identity sensitivity (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** HOLD_PBP_PRESENCE_ONLY; NFL_PROP_SOURCE_COUNT_INVALID
- **mechanism:** Head b7d49178 confirmed a positive-control match for 26,304 of 28,178 weekly-stat-present player-games, but 1,874 had no passer/receiver/rusher PBP actor. Some of those 1,874 may have zero modeled opportunities; comparing all stats rows alone cannot establish PBP sensitivity for genuinely positive modeled activity.
- **detection_point:** After real PBP 5/5 verified receipt and matched-player identity control, before constructing any zero-outcome labels.
- **fix:** Add a second positive-control stratum restricted to *source-verified positive attempts, targets or carries*; separately count actor observed/not observed/PBP game absent. Missing, nonfinite, fractional or negative opportunities remain a typed input failure, not a zero.
- **prevention_artifact:** scripts/audit_nfl_count_prop_pbp_presence.py, tests/test_nfl_count_prop_pbp_presence.py negative/positive controls, existing PR-only source receipt, exact-head hosted replay pending.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; no specialist certified, registry activated, or protected merge/deploy. can_execute=false and V17_TERMINAL_REDUCER.

## PBP source semantics: exact event identity and receipt precision (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_PBP_EVENT_SCHEMA_INVALID; NFL_PROP_PBP_SOURCE_MISSING; NFL_PROP_PBP_SOURCE_DIGEST_MISMATCH; HOLD_PBP_EVIDENCE_INCOMPLETE
- **mechanism:** In the first PBP verification, game IDs were joined without requiring the embedded season/week to agree with the row; a mismatched week could create a false actor match. A duplicate default-path verification helper emitted generic acquisition codes before PBP-specific source failures. A successful download followed by schema rejection understated already-verified bytes. Finally, all-zero weekly-stat players could set the observed actor-ID alignment flag without positive modeled opportunities.
- **detection_point:** CodeRabbit exact-head source review, before treating an absent actor as source evidence or sharing a PBP hold receipt.
- **fix:** Require the event game_id season/week to equal row season/week before adding actor evidence; rely on the parser's existing independent digest/symlink checks on default path; record the acquired digest receipt before parsing; require a confirmed positive-opportunity actor for an alignment-observed status.
- **prevention_artifact:** tests/test_nfl_count_prop_pbp_presence.py mismatched week/game ID and all-zero opportunity negatives; test_acquire_nfl_count_prop_sources.py PR-only receipt. Hosted CI at next exact head required, no new workflow.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None, all outcomes research-only. can_execute=false, V17_TERMINAL_REDUCER.

## Exact per-stat PBP flag source-comparison probe (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_PBP_FLAG_INVALID; NFL_PROP_PBP_EVENT_DUPLICATE; HOLD_PBP_STAT_SIGNALS_NOT_SETTLEMENT
- **mechanism:** The initial actor-presence control aligned all 26,283 weekly-stat rows with positive attempts/targets/carries, but identity presence does not establish per-stat settlement semantics. nflfastR documents that pass_attempt includes sacks, so naïvely summing flags could miscount official QB attempts; spikes, two-point attempts, no-play penalties and rushing classifications also require rule-constrained reconciliation.
- **detection_point:** Before converting sparse weekly-stat omissions into any zero-count event or using a PBP-derived target in offline fitted research.
- **fix:** Count only candidate PBP signals at exact game/play/player keys with predeclared sack/spike/two-point/no-play exclusions, compare only to existing official weekly source counts, preserve per-stat disagreement evidence and positive-weekly/zero-weekly distinctions. No empty player-game zero is manufactured.
- **prevention_artifact:** scripts/audit_nfl_count_prop_pbp_stats.py; tests/test_nfl_count_prop_pbp_stats.py negative duplicate/flag/sack/spike/two-point/no-play and noncertifying comparison; PR-scoped hosted observation receipt. Exact-head CI and real-data validation pending.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; fitted model, calibration, semantic certification and protected release remain HOLD, can_execute=false, V17_TERMINAL_REDUCER.

## PBP administrative-play source misclassification — 2026-11-11

- **date:** 2026-11-11
- **typed_reason:** HOLD_PBP_STAT_SIGNALS_NOT_SETTLEMENT
- **mechanism:** Exact actual PBP-vs-weekly source audit run 43125527946 showed 302/3,123 positive QB-attempt player-games undercounted and 1,234/11,141 positive carry player-games undercounted, with zero PBP-overcount cases; completions (3,068), passing TDs (2,166), receptions (19,154) and targets (21,330) matched their positive weekly rows without a single discrepancy. nflfastR references show passing EPA includes play_type qb_spike while rushing EPA includes qb_kneel; official player carries include kneels. This predicts the 302 and 1,234 lower groups will shrink when correctly attributed spikes and kneels are included in the provisional event comparison.
- **detection_point:** Independent six-stat event-level positive-weekly comparison, before using any missing weekly count as training/settlement label.
- **fix:** Include pass_attempt=1, sack=0, qb_spike=1 + play_type=qb_spike in passing count diagnostics and include rush_attempt=1 + play_type=qb_kneel in carry diagnostics. Retain strict two-point exclusion, invalid/inconsistent events, no-play rejection, and zero-imputation prohibition. Emit observed source spike/kneel attribution counts for causal verification.
- **prevention_artifact:** tests/test_nfl_count_prop_pbp_stats.py synthetic QB spike/kneel positive regressions, plus existing exact-head hosted real-source skip comparison. Not a model or settlement rule certification.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; source-research only, can_execute=false and V17_TERMINAL_REDUCER.

## Source-quality diagnostic dimension registration failure (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** HOLD_PBP_STAT_EVIDENCE_INCOMPLETE
- **mechanism:** Exact-head hosted rapid run 43126220596 passed 690 tests and verified all 16 PBP/original assets, but PBP source comparison returned `KeyError:'administrative_play_rows'`: newly added spike/kneel evidence keyed a third quality dimension while the receipt aggregator accepted only two. This predicts one missing dictionary key and an otherwise healthy source parse, not model or source unavailability.
- **detection_point:** First real-source skip receipt after introducing a new diagnostic quality dimension, before comparing administrative-play deltas.
- **fix:** Aggregate observed quality dimensions dynamically in the existing research receipt, preserving all totals without changing data/label logic.
- **prevention_artifact:** `test_quality_aggregation_retains_new_administrative_play_dimensions` protects newly observed dimensions across multiple pinned source files; hosted exact-head replay required.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None, research-only; can_execute=false, V17_TERMINAL_REDUCER.

## PBP source validation follow-up — actor attribution, normalized identity, malformed flags (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_PBP_ATTRIBUTION_INCOMPLETE; NFL_PROP_PBP_EVENT_DUPLICATE; NFL_PROP_PBP_FLAG_INVALID; NFL_PROP_PBP_EVENT_SCHEMA_INVALID
- **mechanism:** Unattributed pass/rush flags can disappear before the actor-only filter, whitespace-variant game/play IDs can double-count one play, nonnumeric flag text can be coerced to missing, and a missing play type can silently reduce the counted candidate. Each predicts a misleading lower PBP number even with a complete frozen source.
- **detection_point:** CodeRabbit review of the offline event comparator after first six-stat source comparison, before zero-label reconciliation.
- **fix:** Fail closed on relevant actorless flags with source count, compare normalized game/play keys, reject nonnumeric flag tokens distinct from null, and reject flagged opportunities without play_type while retaining an explicit no_play exclusion.
- **prevention_artifact:** tests/test_nfl_count_prop_pbp_stats.py actorless flagged and no_play controls, whitespace duplicate, nonnumeric and missing play-type rejection. Hosted exact-head replay pending; no direct probability/threshold changes.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None. Research-only P/C; no merge, deploy, certification or execution. can_execute=false; V17_TERMINAL_REDUCER.

## Residual per-stat source forensic receipt and missing-ID quarantine evidence (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** HOLD_PBP_STAT_DISAGREEMENT_UNRESOLVED; NFL_PROP_SOURCE_IDENTITY_INVALID
- **mechanism:** After 345 verified spikes and 2,085 verified kneels restored the correct candidate counting semantics, two positive weekly source rows still diverged by exactly one event: 2025_02_SEA_PIT QB passer `00-0023459` attempts 33 vs PBP candidate 32, and 2024_15_MIA_HOU rusher `00-0033854` carries 1 vs PBP 0. Their cause cannot be inferred from aggregate parity. Separately, the trainer quarantines source rows with absent IDs; the exact per-year real-source exception count must be visible before a nonauthor semantic reviewer can assess it.
- **detection_point:** Real pinned PBP-vs-weekly six-count comparison and CodeRabbit source-identity review, before offline label reconstruction.
- **fix:** Add a bounded read-only exact-event review for the two residuals, recording original play-type distributions and excluded flagged candidate plays without replacing missing events. Publish frozen source unattributed-identity quarantine counts and examples, while explicitly marking independent semantic approval false.
- **prevention_artifact:** tests/test_nfl_count_prop_pbp_stats.py residual no_play exclusion and unsupported-stat negative checks; existing PR-only official-source receipt. New exact-head hosted validation required.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None, no fitted candidate accepted or production serving route; can_execute=false, V17_TERMINAL_REDUCER.

## No-play residual penalty context retained as evidence (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** HOLD_PBP_STAT_DISAGREEMENT_UNRESOLVED
- **mechanism:** Exact-head hosted run 43127994809 verified 698 tests and identified two one-play residual PBP differences: pass_attempts 33 vs 32 for player GSIS 00-0023459 in 2025_02_SEA_PIT, `no_play` at play_id=3686; carries 1 vs 0 for GSIS 00-0033854 in 2024_15_MIA_HOU, `no_play` at play_id=2274. The source's PBP play_type alone cannot establish official penalty/void settlement. The same verified source revealed 90 all-zero identity-quarantined source rows, exactly 18 in each of 2021–2025, all still pending independent semantic adjudication.
- **detection_point:** Residual positive-weekly event trace after PBP spike/kneel source-definition corrections and before candidate count-label construction.
- **fix:** Preserve original flag/penalty type/team/description excerpt (bounded) in the two pinned-source residual receipts without assigning an extra play. Repair canonical failure-code table row continuity. No change to candidate probability fit, qualification, model routing or source labels.
- **prevention_artifact:** `test_residual_penalty_context_is_bounded_and_never_a_stat_fix`, and existing `test_residual_play_type_evidence_never_corrects_weekly_counts`. Hosted exact-head checks required.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605 ; https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/43127994809
- **production_evidence:** None; independent source-rights and SIRT semantic acceptance absent, no fit/certification/activation or deploy, can_execute=false and V17_TERMINAL_REDUCER.

## PBP actor attribution and flag completeness refinements (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_PBP_ATTRIBUTION_INCOMPLETE; NFL_PROP_PBP_FLAG_INVALID; HOLD_PBP_STAT_SIGNALS_NOT_SETTLEMENT
- **mechanism:** Code review found whitespace-only spike/kneel actors could be miscounted as attributed, an otherwise-valid spike could create a receiver target through a shared passing mask, actorless invalid flags were discarded before validation, and residual inspector reported flagged sacks as eligible although the candidate source rejected them.
- **detection_point:** CodeRabbit review of exact-head PBP stats source audit, before accepting six-stat source reconciliation or a no-play penalty explanation.
- **fix:** Use nonblank exact GSIS IDs for actor attribution, validate raw opportunity flags ahead of the actor filter, restrict receiver signals to nonspike passes, and mirror exact candidate mask in residual excludes. Do not alter inferred labels, existing source pins, fitted probability or qualification.
- **prevention_artifact:** Four negative/positive checks in tests/test_nfl_count_prop_pbp_stats.py; hosted exact-head replay required.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; P/C research only, 0 new certified lanes, can_execute=false, V17_TERMINAL_REDUCER.

## NFL missing PFR→GSIS exact-source diagnosis (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** HOLD_UNRESOLVED_PARTICIPATION
- **mechanism:** The verified five-season snap-positive cohort has 48 PFR identity-unmatched player-games. This can happen because the PFR ID is absent from pinned players.csv or because a PFR source row exists but its GSIS ID is blank; these predict different source repair tasks. Unmapped game count alone cannot show whether failures are concentrated in a few players.
- **detection_point:** Immediately following the verified 48 player-game crosswalk gap receipt, before mapping identity into any model target or event.
- **fix:** Add a read-only partition of unmatched player-games and distinct PFR IDs into PFR-not-in-pinned-players versus PFR-present-without-valid-GSIS, based on the same pinned source. Preserve all unmatched rows UNKNOWN and bound review examples.
- **prevention_artifact:** test_missing_pfr_record_never_becomes_a_gsis_guess, test_present_pfr_without_gsis_is_separate_unknown_identity, test_repeated_pfr_gap_counts_games_and_distinct_ids_separately. Existing PR-only rapid regression will observe exact source categories.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None. No inferred identity, zero count, fitted probability or protected merge; can_execute=false, V17_TERMINAL_REDUCER.

## Four-source-player case roster for independent identity adjudication (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** HOLD_UNRESOLVED_PARTICIPATION
- **mechanism:** Hosted run 43132684562 confirmed 48 unmatched player-games are exactly four distinct PFR IDs absent from pinned players.csv, zero cases with PFR present and GSIS missing. This predicts repeated weekly appearances by a small historical roster set, not 48 distinct identities.
- **detection_point:** First exact-source crosswalk cause partition, before seeking any supplemental roster evidence or inserting a GSIS ID.
- **fix:** Enumerate bounded per-PFR source profiles with observed seasons, offensive positions, player-game count, maximum offensive snaps and a game ID for independently authorized historical roster matching. No name/position heuristic becomes a player identity.
- **prevention_artifact:** Existing synthetic crosswalk fixtures assert observed player profiles and repeats; original rapid PR-only source observation reports all four records. Hosted exact-head replay pending.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None. No PFR-to-GSIS mapping made, any remaining untrusted supplemental roster source is research only. can_execute=false, V17_TERMINAL_REDUCER.

## Published-checksum 2025 season-roster research identity probe (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** HOLD_ROSTER_CROSSWALK_INDEPENDENT_REVIEW; NFL_PROP_ROSTER_DIGEST_MISMATCH; NFL_PROP_ROSTER_CROSSWALK_CONFLICT
- **mechanism:** The source audit isolated 48 PFR-only snap-positive player-games to 4 PFR codes absent from pinned players.csv: CartNa00 (4 RB games 2025), ShenJo00 (7 TE games 2024), WhitCo05 (22 WR games 2021/22/24/25), WillRo08 (15 TE games 2023/24). Exact historical season rosters are a candidate bridge because they publish PFR and GSIS IDs, but only roster_2025.csv currently supplies an official SHA-256 digest in GitHub release metadata; 2021–24 assets lack published SHA values. A name/position/club match cannot certify GSIS.
- **detection_point:** Immediately after isolating four source-missing PFR identities, before PBP event join or pretrained candidate labels.
- **fix:** Add one pinned 2025 roster asset read-only audit using the existing HTTPS host allowlist and official 2025 SHA+size. Enumerate exact PFR↔GSIS pairs, reject ambiguous/malformed IDs, preserve HOLD, never apply candidate pairs to snaps/stats/model.
- **prevention_artifact:** scripts/audit_nfl_count_prop_roster_crosswalk.py; tests/test_nfl_count_prop_roster_crosswalk.py, PR-only original rapid-runner observational receipt. Exact head hosted verification and independent semantic reviewer still required.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; no source-rights clearance, model certification, protected merge, deploy or execution. can_execute=false, V17_TERMINAL_REDUCER.

## NFL count-stat missing weekly-row historical-selection exposure (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** HOLD_UNRESOLVED_PARTICIPATION
- **mechanism:** Despite exact PBP actor parity, 3,994 snap-positive participants lack weekly stats rows. The five candidate builders require 10 earlier observed weekly games for L10/L5 inputs; training only observed stats omits a selected population of sparse opportunities. This predicts that a subset of 3,994 omissions would fall after 10 earlier recorded games and could bias model outcomes if treated as complete coverage.
- **detection_point:** Before fitting any offline specialist, and before treating a sparse source's label selection as a valid chronological target population.
- **fix:** Report route-specific *upper bounds* on missing player-games with >=10 earlier observed weekly records, using strict earlier season/week ordering (future weeks excluded) and the route's eligible positions. Do not infer missing zero counts or claim true feature eligibility across position changes.
- **prevention_artifact:** test_missing_label_history_uses_earlier_rows_without_zero_imputation with 9-versus-10 positive control, and test_future_source_rows_never_count_toward_a_missing_pregame_label. Existing PR-only full 16-file pinned-source observation will report five research-only counts.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None. No probability fit, label imputation, protected merge or deploy; can_execute=false, V17_TERMINAL_REDUCER.

## Independent 2025 weekly-roster pin and PFR coverage contrast (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** HOLD_ROSTER_CROSSWALK_INDEPENDENT_REVIEW; HOLD_ROSTER_CROSSWALK_SOURCE_UNAVAILABLE
- **mechanism:** Exact-head rapid run 43134300583 verified the season-level 2025 roster (1,010,039 bytes, published SHA), but found 0/4 exact PFR targets. The roster may not cover these individuals or may have missing PFR columns; one no-match cannot resolve a historical identity gap. An independent 2025 weekly-roster asset is available with its own official digest and 15,385,661 bytes.
- **detection_point:** Zero exact PFR-ID pairs after first pinned season roster, before assigning any inferred GSIS/zero count.
- **fix:** Reuse the same official-host digest-checked reader to inspect the 2025 weekly roster with exact published SHA `c2f7a1ffebe06058400af1989d1cd2900cc5c9659f084623708a06d4e28de35b`, report PFR field coverage and independently enumerate exact candidate pairs. Never mutate canonical crosswalk, fit output, or publish probability.
- **prevention_artifact:** Three tests for separate weekly published digest, wrong checksum, and sparse source PFR coverage; existing PR-only hosted source receipt, no new workflow/gate.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None. Historical 2021–24 roster files remain unverified because upstream GitHub release metadata lacks published hashes. can_execute=false, V17_TERMINAL_REDUCER.

## Direct roster inspector historical-season spoofing (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_ROSTER_SCHEMA_INVALID
- **mechanism:** `read_pinned_2025_roster` validates season before returning, but `inspect_roster_id_candidates` can be called independently on arbitrary input. Without its own guard a 2024 row could be presented as a 2025 exact pair, despite no pinned 2024 asset.
- **detection_point:** CodeRabbit direct-call negative review after frozen 2025 season/weekly roster probe was added; should have been found when exposing a public read-only helper.
- **fix:** Require every direct-inspector roster row to have exact non-null season 2025. Fail closed using existing NFL_PROP_ROSTER_SCHEMA_INVALID; no new gate or status code.
- **prevention_artifact:** Three parameterized negative tests for season 2024/2026/null in tests/test_nfl_count_prop_roster_crosswalk.py. Hosted exact-head replay required.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; no roster candidate accepted or applied, can_execute=false, V17_TERMINAL_REDUCER.

## Historical 2021–24 roster source evidence without published hashes (2026-11-11)

- **date:** 2026-11-11
- **typed_reason:** HOLD_HISTORICAL_ROSTER_UNPUBLISHED_DIGEST; NFL_PROP_ROSTER_TWO_READ_DRIFT; NFL_PROP_ROSTER_SOURCE_SIZE_MISMATCH
- **mechanism:** Both 2025 release-pinned season and weekly roster files showed 0/4 exact PFR candidates despite 2,196 and 35,113 roster rows with populated PFR identifiers respectively. Historical 2021–24 nflverse season roster assets may contain missing active roster PFR-to-GSIS pairs, but their GitHub release metadata lists public file sizes without SHA-256 digests, so they cannot satisfy the existing trusted-source pin.
- **detection_point:** After published-checksum 2025 roster sources supplied no exact PFR pairs for the four unmatched PFR identities.
- **fix:** Add a separate observation-only two-read source probe for archived seasons with predeclared published byte sizes, strict HTTPS release host, exact byte-for-byte/hash concordance across reads, and season-column validation. Any pair found remains provisional/unapproved, never joined into fitted model data; no no-hash route to protected publication.
- **prevention_artifact:** Test for two identical source reads and mandatory HOLD, same-size drift rejection, size/year refusal, and direct season-scope check; existing PR-only source observation. Exact-head CI and independent SIRT review required.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None. 4 unresolved historical PFR IDs and 3,994 missing weekly count-stat rows retain UNKNOWN; can_execute=false and V17_TERMINAL_REDUCER.


## Observed L10 window vs positive-snap participation gap — 2026-11-12

- **date:** 2026-11-12
- **typed_reason:** HOLD_UNRESOLVED_PARTICIPATION (existing; no new runtime failure code).
- **mechanism:** The offline count-model feature builder uses the ten most recent *recorded weekly stat rows*. Verified official snap-positive participant games can be absent from that source. A valid observed game following ten recorded prior rows can therefore have a hidden positive-snap game inside its L10 chronological window. This predicts that previous-stat feature histories are potentially selection-biased even where the current target row exists, separate from missing target labels.
- **detection_point:** In source cohort reconciliation before any 2021–23 fit, 2024 calibration or untouched 2025 holdout, not after fitted-score evaluation.
- **fix:** Add a read-only, route/season-scoped audit comparing the most recent ten recorded eligible-role source events against **mapped** snap-positive player-games lacking source rows. Count affected *observed* feature windows and keep missing counts/identities UNKNOWN. Do not infer zero values, rebuild features, change model fit rules, lower source thresholds, attach markets or publish probabilities. Unmapped identities and cross-role histories stay outside the positive assertion.
- **prevention_artifact:** \`artifacts/wow-engine/tests/test_nfl_count_prop_participation_audit.py\` (eight boundary/negative cases); offline standalone equivalent 12/12 synthetic PASS. Existing PR-only rapid runner prints bounded per-route/by-season real-source receipt after 11 pinned hashes, but until it executes at the fresh head its source findings remain **unverified**.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None. No new fitted model, certified specialist, source-rights approval, independent SIRT acceptance, production merge/deployment, user Action or wager. \`can_execute=false\`, \`V17_TERMINAL_REDUCER\`.

## Embedded game year/week mismatch in L10 feature audit — 2026-11-12
- **date:** 2026-11-12
- **typed_reason:** NFL_PROP_PARTICIPATION_SNAP_INVALID (existing typed input failure).
- **mechanism:** An otherwise syntactically valid `season`/`week` pair could disagree with the embedded `game_id` year/week. The L10 history audit then ordered a mislabeled event by the wrong instant and could report false missing-game exposure or omit a true gap.
- **detection_point:** CodeRabbit exact-head review on PR #2605 after the new source receipt; must have been caught at the same input-to-event-index preflight.
- **fix:** Require the full canonical `YYYY_WW_AWAY_HOME` game identity and exact `season/week` prefix agreement in both weekly-stat and mapped-missing inputs; fail closed instead of guessing dates or repairing IDs.
- **prevention_artifact:** four negative-first RED→GREEN cases for source-year disagreement and missing team names, each on both source frames; standalone audit test suite 16/16 PASS. New hosted exact-head review still required.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None. Observed 2021–2025 L10 exposure remains research-only; no model activation, zero imputation, source rights clearance, or protected release. `can_execute=false`.


## Frozen candidate split identity-count sensitivity — 2026-11-12
- **date:** 2026-11-12
- **typed_reason:** NFL_PROP_PARTICIPATION_SNAP_INVALID (reused for malformed or internally inconsistent split receipts; current negative paths have subcode \`cohort_*\`).
- **mechanism:** Historical source weekly-stat rows omit official snap-positive participant-games, so an ordinary count of L10 feature rows cannot establish a clean pregame history. A later train/cal/holdout partition could satisfy minimum row counts but still include known contaminated history windows. This predicts affected eligible-source counts even where the current game has a recorded target.
- **detection_point:** After SHA-pinned source/snap reconciliation and before any fitted specialist training, not on post-hoc holdout metrics.
- **fix:** On the existing read-only audit, compute exact 2021–23 TRAIN, 2024 CALIBRATION, 2025 HOLDOUT **identity counts only** for unflagged L10 windows. Validate all five route labels, five declared years, integer nonnegative counts, exposed <= eligible, and sum parity with original source audit; fail closed on discrepancy. Compare each count with the current 1000/300/300 offline candidate row minima without modifying them or labeling the source certified. Never read holdout outcome values, fit a model, impute missing counts, or substitute an unverified PFR/GSIS ID.
- **prevention_artifact:** \`tests/test_nfl_count_prop_participation_audit.py\` (12 added exact-boundary and malformed-receipt cases). Existing PR-only trusted runner adds \`known_gap_excluded_candidate_splits\` to the signed-source observational receipt; **hosted exact-head results not yet observed**, so no new empirical claim.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; no valid source-rights clearance, no source completeness certification, no trained or calibrated probability, no change to qualification/routing/threshold, no production deployment or persisted terminal receipt. \`can_execute=false\`, \`V17_TERMINAL_REDUCER\`.

## Mutable official players.csv release asset drift — 2026-11-12

- **date:** 2026-11-12
- **typed_reason:** `NFL_PROP_ACQUIRE_DIGEST_MISMATCH` (existing canonical code; no new status class).
- **mechanism:** GitHub release tag `players` replaced the same-name `players.csv` artifact at 2026-11-12T16:32:05Z, shortly before exact-head rapid regression `43260224970` tried acquiring the source. Frozen WOW SHA `da58c285c9013d65bb517d687095e0970f3a4bb148bb81f6ed2aec2bf398b72e` and 7,306,971 bytes conflict with the new published SHA `f90a9d0009b8e5d50353b0013971b466534b25cc2f98b7ad14d39361356975d7` and 7,308,461 bytes. The other ten pinned stats_player/snap_counts assets still match their upstream metadata. A mutable release URL is not an immutable dataset identifier.
- **detection_point:** Before transferring large source archives in the existing PR-only research runner; source asset metadata is cheap enough to inspect before downloading ~54 MB of unrelated matching files.
- **observed_red:** Exact-head rapid regression `43260224970` reported `731 passed, 1 skipped`, with `NFL_PROP_ACQUIRE_DIGEST_MISMATCH:players.csv:verified_prior_assets=10/11`. This is a source-data HOLD, not green real-source audit or failing code test.
- **fix:** Research-only early release-metadata pinset preflight, comparing exact `name`, published SHA-256 and byte-size with frozen code-owned pins across 11 assets before transfer. Existing downloaded-byte hash check remains mandatory and unchanged; no repinning, no historical source substitute.
- **prevention_artifact:** `scripts/acquire_nfl_count_prop_sources.py:verify_official_release_metadata` and four negative variants (size, digest, absent and duplicate asset), plus incomplete-metadata HOLD in `tests/test_acquire_nfl_count_prop_sources.py`; existing PR-only runner observes release metadata, no new workflow or production fetch.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; source rights and lineage for replacement asset unreviewed, 3,994 missing weekly player-games and 48 unmapped identities remain UNKNOWN, fitted calibration and promotion HOLD, `can_execute=false` and `V17_TERMINAL_REDUCER`.


## NFL source-stat anonymous weekly row lineage and replacement PFR shadow (2026-11-12)

- **date:** 2026-11-12
- **typed_reason:** HOLD_ROSTER_CROSSWALK_INDEPENDENT_REVIEW (existing code; source semantics/rights still unresolved).
- **mechanism:** The upstream nflverse-pbp publisher uses nflfastR::calculate_stats(season,"week","player"). In nflfastR source at commit f367d0a4ba7d3a472351865467eba3a9c53a14f6, it groups raw playstats by season/week/player_id without dropping NA identities and picks dplyr::first(game_id) at the weekly group. This is consistent with one anonymous row per regular-season week (18 in each of 2021-2025), but the group's game_id is NOT an independently proven player-game identity and cannot certify those records as absent players or zero opportunities. Existing 90-row quarantine remains non-serving and independently unreviewed.
- **detection_point:** at the weekly source quarantine/semantic preflight before any player-event label or PFR/GSIS crosswalk adoption.
- **fix:** Preserve the existing quarantine and code-owned frozen players.csv pin, and add ONE PR-scoped, read-only exact-ID shadow observation of the replacement published 2026-11-12 16:32:05 UTC (sha256 f90a9d0009b8e5d50353b0013971b466534b25cc2f98b7ad14d39361356975d7, size 7,308,461). Existing approved-host/downloader hash checks verify the exact bytes, then report only candidate mappings for the four predeclared PFR keys, never auto-join them. Any candidate remains HOLD_ROSTER_CROSSWALK_INDEPENDENT_REVIEW and must be separately corroborated. No fallback source or new download workflow.
- **prevention_artifact:** Existing fail-closed source-pair and duplicate-ID tests plus the guarded test_pr2605_shadow_replaced_players_exact_pfr_pairs in tests/test_acquire_nfl_count_prop_sources.py. No source pin, model rule, probability threshold, or gate is weakened.
- **links:** https://github.com/nflverse/nflverse-pbp/blob/master/R/update_stats.R ; https://github.com/nflverse/nflfastR/blob/f367d0a4ba7d3a472351865467eba3a9c53a14f6/R/calculate_stats.R ; https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **independent_review_pending:** nflverse-players upstream PFR mapping is name-based with internal unknown-rights input and its manual override file does not list any of the four missing PFR IDs; not an approved substitute. Rights, identity, player participation and 2025 holdout labels remain unresolved.
- **production_evidence:** none; can_execute=false, terminal=V17_TERMINAL_REDUCER, no new model certification or source rights clearance.


## Verified FTN 2025 player-on-play source alternative — 2026-11-12

- **date:** 2026-11-12
- **typed_reason:** HOLD_FTN_OFFENSE_PRESENCE_ONLY; input errors registered in docs/failure_codes.md.
- **mechanism:** PBP ball actor fields identify only credited passing/rushing/receiving events. FTN via nflverse participation lists GSIS IDs on offensive plays. On-field evidence can identify a narrower missing weekly-stat coverage class, but cannot prove absent count values are zero.
- **detection_point:** 2025 source-observation reconciliation, before any model label, calibration or source identity promotion.
- **fix:** SHA-256/size-pinned FTN file reader in existing PBP research audit with CC-BY-SA-4.0 attribution, source schema and HTTPS-host validation. PR #2605 rapid-run-only research compares verified 2025 weekly stats, snap counts and replacement players shadow against exact on-field play participation. No new production workflow, collector, source registry, zero imputation, model or threshold.
- **prevention_artifact:** tests/test_nfl_count_prop_pbp_presence.py synthetic digest, malformed game/role, redirect and identity-negative tests; tests/test_acquire_nfl_count_prop_sources.py PR-scoped pinned-source observation. CI and actual data outcome UNVERIFIED at entry.
- **source/license:** https://github.com/nflverse/nflverse-data/releases/tag/pbp_participation ; https://nflreadr.nflverse.com/reference/load_participation.html ; CC-BY-SA-4.0 FTN Data via nflverse; 2025 published digest 59069adfee7b0f464befba8a5e8be331e523633cc6a7ab403d37bcbcdfbe66ac. Independent data adaptation/training rights signoff still required.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** Not verified; source research only, can_execute=false, V17_TERMINAL_REDUCER.

## Archived nflverse player metadata recovery candidate — 2026-11-12

- **date:** 2026-11-12
- **typed_reason:** `NFL_PROP_ACQUIRE_DIGEST_MISMATCH` (reuse canonical code; no new queue).
- **mechanism:** The live `nflverse-data` players.csv release is mutable and its 2026-11-12 replacement broke the original WOW frozen CSV pin. The separate official `nflverse-data-archives` dated release `archive-2026-11-05` (release id 435763302) carries seven RDS artifacts with publisher-reported size/SHA-256 metadata: players.rds, pfr_rosters.rds and stats_player_week_2021..2025.rds. A historical RDS archive is a *different serialization and possibly different data vintage*; publisher metadata cannot prove equivalence with frozen players.csv bytes or complete player-game target labels.
- **detection_point:** Existing 11-asset metadata preflight and source-identity crosswalk, before any fitting or calibration; the old pinned players.csv remained unavailable after official asset replacement.
- **fix:** Added metadata-only `observe_official_archive_metadata` to the existing acquisition module, with a *fixed* dated tag, release id and seven expected (asset id, byte-size, SHA-256) triplets. It never downloads/loads an archive or repins one of the 11 frozen CSV sources. Returns only `HOLD_ARCHIVE_RDS_NOT_EQUIVALENT_TO_FROZEN_CSV` with all training, licensing, model and publication permissions false. Any mutation, missing/duplicated archive asset or changed metadata yields an existing canonical typed HOLD. Added nine negative cases, non-authorizing success case and PR-rapid-runner-only official GitHub metadata observation.
- **prevention_artifact:** `scripts/acquire_nfl_count_prop_sources.py:observe_official_archive_metadata` and `tests/test_acquire_nfl_count_prop_sources.py`. Permanent test preserves the old players.csv pin and verifies archive metadata alone never authorizes training; no new CI workflow or runtime dependency.
- **source:** https://github.com/nflverse/nflverse-data-archives/releases/tag/archive-2026-11-05 (independent official GitHub metadata, observed 2026-11-12). Original users' source-rights review, PFR identity bridge, 3,994 weekly participation gaps, 48 unmapped player-games and 90 anonymous source rows remain unresolved. The archive is only a comparison candidate, not a replacement or independent source-rights approval.
- **verification:** Official archive metadata was read via GitHub API and seven selected hashes recorded. New synthetic/hosted exact-head tests are pending; RDS raw bytes not acquired or independently verified, and no historical source alignment/holdout evaluation was performed. 0 certified model and 0 qualified-row delta; no production verification for this candidate-only PR.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **safety:** `custom_gpt_identity=WOW_BETTING_ENGINE`; `runtime_generation=V17_ACTIVE`; `terminal_authority=V17_TERMINAL_REDUCER`; `can_execute=false`; `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.

## FTN source-error and identity-control negative reporting correction — 2026-11-12

- **date:** 2026-11-12
- **typed_reason:** `HOLD_FTN_PARTICIPATION_SOURCE_OR_IDENTITY` (existing canonical research HOLD; redirect and network cases reuse existing `NFL_PROP_FTN_PARTICIPATION_REDIRECT_UNTRUSTED` / `NFL_PROP_FTN_PARTICIPATION_NETWORK_UNAVAILABLE`).
- **mechanism:** The shared trusted downloader may reject a redirect *before* returning a stream, yielding a generic acquire error instead of FTN-scoped failure; a mid-stream socket reset similarly escaped without its typed network failure. Worse, the FTN shadow summarized "not observed on offense" counts even if there were no positively matched GSIS/opportunity rows to establish identity alignment, predicting false absence conclusions.
- **detection_point:** At the approved-host acquisition boundary, at the stream read site, and before any negative-presence report is formed.
- **fix:** Translate only the precise shared redirect ValueError, preserve all unrelated ValueErrors, map stream read OSError/TimeoutError to existing FTN network-unavailable code; require `pbp_actor_id_alignment_observed is True` and at least one exact positive opportunity control before constructing the FTN research absence breakdown. If not, use existing identity HOLD and omit the misleading counts. This **never** treats non-presence as a zero count.
- **prevention_artifact:** `tests/test_nfl_count_prop_pbp_presence.py` now keeps pre-stream redirect, unrelated ValueError, partial-stream read, five insufficient-positive-control and one valid-positive-control negative-path cases. `tests/test_acquire_nfl_count_prop_sources.py` invokes the guard before the PR-only FTN summary.
- **verification:** Exact-head hosted regression and independent source semantics still pending at the time of this entry. No model fit, no data-rights clearance, no source repin or P/C merge.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None. `can_execute=false`; `V17_TERMINAL_REDUCER`; model coverage/qualification delta +0.

## Correlated PBP absence and independent on-field source — 2026-11-12

- **date:** 2026-11-12
- **typed_reason:** `HOLD_PBP_STAT_SIGNALS_NOT_SETTLEMENT` (existing typed code; no new failure classification).
- **mechanism:** The 2025 weekly nflverse stat file and candidate PBP play flags share nflfastR processing. Their simultaneous absence is **not independent corroboration** of zero statistical opportunities. A separate FTN on-field source shows the held players participated; the source PBP can still lack attributed receiver targets (749 flagged 2025 candidates without receiver IDs), so silence is not a settled zero.
- **detection_point:** At entire missing-stat cohort audit, before converting any absent weekly player-game to a source label or pregame L10 feature.
- **observed_artifact:** PR #2605 rapid regression [#43279265915](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/43279265915), exact SHA `63aa9b92ad770030cefc57af099ff9bf2da6c311`: **800 tests passed, 12 skipped, zero failed**. SHA-verified FTN participation 2025 includes 689/689 of 689 mapped positive-snap missing-weekly-stat player-games; separate SHA-verified 2025 PBP returns 0 positive flags for 10 missing QB cases (attempts, completions, passing TDs), 0 for 679 missing RB/WR/TE cases (receptions, targets), and 0 for all 689 on carries. Route groups overlap and cannot be summed.
- **fix/prevention artifact:** Existing research-only PBP presence classifier optionally compares all missing rows to the pinned PBP positive counters per role and emits explicit `without_positive_pbp_flags_STILL_UNKNOWN`, `missing_count_labels_promoted=0`. Permanent positive/negative/absent-game synthetic tests protect the no-zero outcome. 2025 held cases remain UNKNOWN until independently licensed settlement semantics establish any true zero.
- **license/rights:** FTN participation is CC BY-SA 4.0 (FTN Data via nflverse). nflverse publisher says derived models/apps must honor share-alike. NFL gamebooks restrict reuse outside media without permission. No official gamebook data was ingested, scraped into training or promoted.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605 ; https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/1563
- **production_evidence:** No new fitted specialist, qualified pick, deployment, Supabase row or live-route check. `V17_TERMINAL_REDUCER`, `can_execute=false`, `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`.

## Anonymous weekly source game-id chronology guard — 2026-11-12

- **date:** 2026-11-12
- **typed_reason:** NFL_PROP_SOURCE_IDENTITY_INVALID (existing canonical source-identity failure).
- **mechanism:** The all-zero anonymous-row quarantine checked only that `game_id` was nonempty while validating `season` and `week` independently. An all-zero row with `2022_01_BAL_KC` in a 2021 Week 1 CSV, or a malformed `2021_01_UNATTR`, was silently classified as a harmless anonymous source artifact. This predicts a misleading quarantine evidence ledger and an unreviewable source-lineage exclusion.
- **detection_point:** Frozen weekly-source preflight before source-row quarantine, not after a model is fitted.
- **fix:** Restrict quarantinable anonymous REG entries to the exact canonical `YYYY_WW_AWAY_HOME` pattern matching the named source season and row week. Fail closed with existing `NFL_PROP_SOURCE_IDENTITY_INVALID:game_id` on malformed/contradictory identities. Never assign an anonymous player, infer a zero from missing weekly data, or change publication flags.
- **prevention_artifact:** `tests/test_nfl_missing_prop_candidates.py::test_unattributed_possible_participant_fails_closed` now includes four malformed/stale event-id negative variants and a valid canonical event fixture. Synthetic original-branch guard accepted all four malformed examples (RED); local proposed regex 5/5 synthetic controls PASS (GREEN). Exact-head hosted CI and independent semantic acceptance remain separate/unverified at authoring.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; no model, source-rights authorization, P/C merge, certified route or deployment. `can_execute=false`, `V17_TERMINAL_REDUCER`.

## Mechanical promotion: sparse manual player overrides are not full identity coverage — 2026-11-12

- **date:** 2026-11-12
- **typed_reason:** NFL_PROP_ROSTER_SCHEMA_INVALID (existing, malformed manual upstream pfr_id); HOLD_ROSTER_CROSSWALK_INDEPENDENT_REVIEW remains unresolved.
- **mechanism:** nflverse-players manual overwrite JSON is a sparse correction list, not the full PFR/GSIS universe. Missing a PFR code shows no explicit correction, not player absence. Guessed mappings would hide 48 unresolved player-games. An unhashable malformed upstream ID previously produced an untyped TypeError.
- **detection_point:** Identity-only source triage before training cohort joins.
- **observed_artifact:** nflverse/nflverse-players/inst/players_manual_overwrite.json Git blob 8c8985361e7e8a5871e112fc482adb0525f5e302: 4,351 correction objects, 0 exact PFR entries for CartNa00, ShenJo00, WhitCo05, WillRo08. Not full player coverage, label evidence, or data-use clearance. Full players RDS remains unadjudicated.
- **fix:** Added research-only inspect_manual_pfr_overrides to the existing roster crosswalk inspector. All flags remain non-authorizing. Reproduced malformed list-valued PFR ID TypeError (RED); replaced with canonical NFL_PROP_ROSTER_SCHEMA_INVALID (GREEN).
- **prevention_artifact:** tests/test_nfl_count_prop_roster_crosswalk.py covers no-manual-match-not-absence, exact candidate-only, conflicting pairs, malformed target/GSIS/PFR, and non-dict source. Exact-head hosted regression remains pending.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None; zero certified specialists or qualified rows. No P/C merge, source substitution, model fitting, zero-stat imputation or execution. can_execute=false.

## Reciprocal GSIS collision must hold in roster identity research — 2026-11-12

- **date:** 2026-11-12
- **typed_reason:** NFL_PROP_ROSTER_CROSSWALK_CONFLICT (existing canonical IDENTITY HOLD).
- **mechanism:** An exact-PFR-to-GSIS candidate inspector rejected one PFR code mapping to multiple GSIS IDs but failed to detect two PFR codes sharing one GSIS ID. This predicted an apparently valid pair for both reviewed PFR codes, even if one conflicting PFR was outside the target list. The upstream nflverse-players PFR release algorithm uses successive name/rookie-year, name, and first-last joins (R/players_pfr.R, Git blob c9c3fe0eb0ae91010dc946baf150ce502eec2228), so even a source pair requires independent identity validation.
- **detection_point:** Before archived identity evidence is prepared for independent source-semantics review, not during training or model promotion.
- **fix:** Inspect all nonblank, structurally valid source PFR/GSIS pairs before returning candidate identities; fail closed on any GSIS key associated with more than one PFR code, including unrequested aliases. Existing output retains candidate_crosswalk_applied=false, source_rights_independently_approved=false and can_execute=false.
- **prevention_artifact:** tests/test_nfl_count_prop_roster_crosswalk.py::test_two_distinct_pfr_aliases_to_same_gsis_fail_closed and ::test_candidate_gsis_collision_with_unrequested_pfr_also_fails_closed. The prior exact-method local reproduction raised no exception (RED); reciprocal-uniqueness fix passed 2/2 local probes (GREEN). Exact-head hosted suite remains to be observed.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** No protected merge, certified fitted specialist, qualified row, source repin, entitlement approval or wager execution; live core remains can_execute=false.


## Verified weekly anonymous source shape independent of mutable crosswalk — 2026-11-12
- **date:** 2026-11-12
- **typed_reason:** HOLD_ANONYMOUS_WEEKLY_SOURCE_SEMANTICS (SOURCE, existing model-publication HOLD preserved).
- **mechanism:** Full 11-asset NFL source acquisition correctly stops when publisher-replaced `players.csv` fails the frozen SHA. That early gate prevents contemporary PR-scoped readback of the five *unchanged* SHA-pinned weekly-stat CSVs, leaving the 90 anonymous group origin unadjudicated even though the original weekly files can be checked separately. Upstream nflfastR source groups weekly player stats by season/week/player_id and emits `first(game_id)`; with an NA player ID, a canonical-looking game ID can merely represent a multi-play aggregate.
- **detection_point:** Offline source-provenance observation before source semantic review, not training, calibration, ranking or publication.
- **fix:** Add a PR-rapid-runner-only diagnostic using existing `acquire_one` to SHA/size-verify precisely five frozen stats_player CSVs without requiring the drifted players crosswalk; feed them through the same frozen weekly-source loader and summarize hash-checked anonymous quarantine rows. Emit an explicit research HOLD with per-season counts and shape, not an identity/rights/label clearance. Leave the original 11-asset fail-closed acquisition unchanged.
- **prevention_artifact:** `tests/test_nfl_missing_prop_candidates.py` adds 6 tests verifying strict synthetic provenance and nonauthorization; observed rapid-run RED at `84f1176`: 6 failed specifically due missing source-observation function, 798 passed, 3 research skips. Subsequent GREEN/exact-head CI at final implementation unverified at this ledger entry. `tests/test_acquire_nfl_count_prop_sources.py` adds PR-only real-byte source observation.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** None. No source pin rewrite, crosswalk promotion, missing label imputation, model fit, certification, protected merge or deployment. `can_execute=false`, `V17_TERMINAL_REDUCER`.


## NFL public candidate source-admission prefit boundary — 2026-11-12
- **date:** 2026-11-12
- **typed_reason:** NFL_PROP_FITTING_SOURCE_ADMISSION_HOLD (SOURCE, WOW_PROP_LANE); NFL_PROP_SOURCE_IDENTITY_INVALID for lowercase/padded eligible-role token.
- **mechanism:** The public offline candidate CLI executed `build_candidate` immediately after `load_source_files`, even when source provenance explicitly said independent rights and identity review were false. A digest-matching synthetic CSV reproduced fitting before admission; independently authored lowercase `position_token` could evade the observer's exact-case role check. Negative tests for season and week also checked stale hashes instead of semantic contradictions.
- **detection_point:** Before the first candidate model-fit invocation, after frozen-source verification and before output file creation.
- **fix:** Require frozen release integrity, independent quarantine/identity review, independent source-rights approval and explicit candidate training authorization at the CLI's prefit boundary. All absent flags fail closed as `NFL_PROP_FITTING_SOURCE_ADMISSION_HOLD`. Normalize observer role tokens with `strip().upper()` and recompute test audit digests after semantic-only mutations. Do not change the Poisson specialists, thresholds, fitted parameters or runtime routing.
- **prevention_artifact:** `artifacts/wow-engine/tests/test_nfl_missing_prop_candidates.py`: RED [rapid 43301885274](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/43301885274) reproduced 2 target failures, 806 pass, 4 intentional source SKIPs; GREEN [rapid 43302498970](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/43302498970) 808 pass, 4 intentional SKIPs, 0 failures on `e569adc3`. Registered canonical failure in `artifacts/wow-engine/docs/failure_codes.md`.
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2605
- **production_evidence:** No protected merge, deploy or certified NFL route, no independently cleared label corpus; existing `V17_TERMINAL_REDUCER`, `can_execute=false`, `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true` unchanged. CI at subsequent ledger commit must be rechecked at exact head.
