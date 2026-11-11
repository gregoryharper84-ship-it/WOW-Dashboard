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
