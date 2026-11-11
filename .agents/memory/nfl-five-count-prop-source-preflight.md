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
