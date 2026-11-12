# V28 NBA current-season labels and calibrator RPC digest integrity (2026-11-04)

- date: 2026-11-04
- typed_reason: ESPN_RECENT_WINDOW_GAP_NOT_COVERED (wrong-season false trigger); NBA_CERTIFIED_CALIBRATOR_INVALID (flat-row bypass prevented)
- mechanism: NBA SportsDataverse assets are labeled by season ending year. At 2026-11-02 the former year >= clock.year condition falsely treated completed 2026 as current, tried an ESPN recent supplement, and raised a gap error before processing 2027. Separately the calibrator resolver passed a flat legacy RPC row through without recomputing the canonical payload digest, despite production's authenticated nested JSONB contract.
- detection_point: The first source unit tests and immutable-source review before Engineering App adoption should have exercised October-November NBA season transitions and flat-row negative calibration payloads; CodeRabbit on App PR #2428 found both after initial owner approval.
- fix: Compare NBA season labels against clock.year + 1 from October, retaining calendar-year behavior for WNBA and all existing 14-day gap/provenance checks. Require nested artifact_payload Mapping with no flat outer artifact_type; reuse exact bound digest checks and reject legacy flat RPC rows with NBA_CERTIFIED_CALIBRATOR_INVALID.
- prevention_artifact: test_nba_completed_season_is_not_recent_espn_supplement_target; current 2027 source failure, duplicate ID, and entitlement tests; test_flat_artifact_row_cannot_bypass_verified_payload_digest; existing nested RPC digest positive/negative tests.
- link: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2386
- evidence: test-only hydration commit 3ce8dbf6, 1 failed / 130 passed at CI 41871026623; repaired season head 81293a5a, 131 passed at CI 41871819667. Source calibrator code repair ac91a593 remains subject to exact-head CI.
- release: no App replay is approved after code changes; old App PR #2428 was closed unmerged; independent QA/Release Authority and production SHA/readback remain unverified.
- can_execute: false
