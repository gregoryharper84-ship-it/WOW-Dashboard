# NFLverse close proxy — encoded game identity preflight (2026-11-12)

- date: 2026-11-12
- typed_reason: `SPREAD_NFLVERSE_CLOSE_PROXY_TEAM_IDENTITY_MISMATCH`
- mechanism: historical close-proxy binding matched `game_id` but did not check that the source row's participants matched the AWAY_HOME team abbreviations encoded in the canonical ID. A corrupted or misjoined row would be counted as exact-line coverage and could distort held-out spread Brier/log-loss.
- detection_point: at `nflverse_close_proxy` row normalization, before any `evaluate_historical_close_proxy` metrics; should have been known when the original source binder was added.
- observed_red: PR #2628 test-only SHA `756e660badcdf7c120eef7460a2f5553359db8e9`; GitHub rapid affected regression 2 failed / 37 passed, including `Failed: DID NOT RAISE` and a wrong-game evidence-set assertion. No claim that actual nflverse published a mismatched record.
- fix: compare canonical `YYYY_WW_AWAY_HOME` tokens to explicit row `away_team`/`home_team`; reject discrepancy with typed failure and preserve valid rows.
- prevention_artifact: `artifacts/wow-engine/tests/test_v17_spread_certification_replay.py` (red-then-green and row-scoped coverage regression); `artifacts/wow-engine/docs/failure_codes.md`.
- rights_boundary: `nflverse/nflverse-data` schedules release credits `nflverse/nfldata` as the original dataset. Its upstream repo has no visible LICENSE file in root. Source and upstream rights review remain required for any licensed training/persistent acquisition; this offline repair is not permission to scrape or train.
- link: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2628
- authority: research-only close proxies, no new sporting-probability authority, `V17_TERMINAL_REDUCER` unchanged, `can_execute=false`.
