# NFL missing count-prop source preflight — 2026-11-11

- **date:** 2026-11-11
- **typed_reason:** NFL_PROP_SOURCE_COUNT_INVALID; NFL_PROP_SOURCE_COUNT_EXCEEDS_OPPORTUNITY; NFL_PROP_SOURCE_DUPLICATE_PLAYER_EVENT
- **mechanism:** A missing/nonfinite raw count, a count inconsistent with the matching opportunity, or a duplicate player/game source row can corrupt independent pregame target and history construction. A generic fillna(0) would hide this defect.
- **detection_point:** Frozen source-input validation, before any fitting or artifact creation. An offline candidate may not cross into serving without subsequent provenance and certification.
- **fix:** Strict source-column/count/identity validation; separate fit and holdout cohorts; all emitted artifacts carry candidate-only false publication/execution flags.
- **prevention_artifact:** artifacts/wow-engine/tests/test_nfl_missing_prop_candidates.py (15 RED→GREEN verified offline unit tests plus synthetic CLI exercise).
- **link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/tree/engineering/nfl-five-count-candidates-20261111
- **production_evidence:** Not established; synthetic exercise only. Hosted exact-head CI/independent approval/deploy/live receipts still required.
