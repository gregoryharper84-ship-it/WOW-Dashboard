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
