# V28 basketball release: negative checks after PR #2425 review

- date: 2026-11-04
- typed_reason: NBA_CURRENT_SEASON_SETTLED_DATA_MISSING; WNBA_TEAM_PROVIDER_CROSSWALK_UNVERIFIED; CALIBRATION_ARTIFACT_FINGERPRINT_INVALID (prevented)
- mechanism: The default NBA freshness guard returned early when hydration failed, allowing stale June history to be replayed as COMPLETE; WNBA official ESPN IDs were compared to unverified BallDontLie numeric history; the shared legacy fingerprint was accidentally switched to a new canonical serializer, changing hashes for unchanged NHL/WNBA artifacts.
- detection_point: Source PR #2386 exact-head negative CI before App adoption should have detected all three; Greptile on App PR #2425 did instead.
- fix: Treat absent NBA hydration readback as unverified during Nov–Jun; reject unmatched WNBA provider ID namespaces before feature assembly; restore the original fingerprint helper and use calibration_payload_sha256 explicitly for new NBA database-bound artifacts.
- prevention_artifact: test_nba_current_season_hydration_failure_is_not_successful_replay; test_wnba_bdl_team_ids_cannot_be_misread_as_espn_history; test_legacy_cross_sport_fingerprints_keep_original_serialization.
- link: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2425
- status: SOURCE_REPAIR_PROPOSED; tests, exact-head checks, independent C/P review, protected merge and production readback remain separate.
