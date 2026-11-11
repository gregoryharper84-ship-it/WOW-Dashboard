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
