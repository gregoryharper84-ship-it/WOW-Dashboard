# Joint game-state pregame chronology and DST fold

- date: 2026-11-12
- typed_reason: SOURCE_TIME_INTEGRITY (learning classification; no newly emitted runtime failure code)
- mechanism: a timezone-aware datetime compared against another with the same ZoneInfo instance is ordered by wall time in Python, ignoring differing fold values. A captured instant at 01:15 with fold=1 could therefore pass an event-start 01:45 with fold=0 even though capture happened 30 minutes after the start.
- detection_point: source intake / research-only game-state projection preflight, before accepting any fitted outcome sample as point-in-time eligible.
- observed_red: local Python reproduction of America/Los_Angeles 2020-11-01 yielded old comparison True while UTC comparison False. CodeRabbit review on PR #2639 independently identified the same mechanism. Red was observed with the original comparison; not a production incident.
- fix: normalize each timezone-aware timestamp to UTC before strict capture-before-start comparison. Leave publication authority unchanged.
- prevention_artifact: artifacts/wow-engine/v17/test_joint_game_state_shadow.py :: test_dst_fold_late_capture_rejected_even_with_same_tzinfo and :: test_dst_fold_earlier_capture_is_accepted.
- link: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2639
- release_state: research branch only; exact-head CI and independent Class C acceptance required before any protected merge. No production deployment or scoreboard gain claimed.
