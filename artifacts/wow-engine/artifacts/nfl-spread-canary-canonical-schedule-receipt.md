# NFL spread production canary acquisition repair

Terminal target: `FIXED_AND_VERIFIED` after protected merge, Render deploy, and production canary replay.

- Affected lane: NFL point-spread forward shadow canary.
- Expected: backend-owned discovery returns an exact future canonical NFL event and the existing spread forward shadow returns a normalized `p_cover/p_push/p_not_cover` triplet.
- Observed: workflow run `36496740723`, job `109177966941`, returned `NFL_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE`; backend ESPN schedule acquisition was HTTP 403 before model invocation.
- Root cause: the canary used an external ESPN scoreboard even though the NFL specialist already owns a frozen canonical nflverse schedule snapshot in `wow_nfl_source_snapshots`/`wow-nfl-raw`.
- Change class: Class B identity/acquisition reliability only.
- Minimal fix: discover the first future unscored game from the same governed canonical schedule snapshot used by `resolve_nfl_team_event_evidence`; pass its canonical `game_id`, teams, and start time into the unchanged fitted spread forward lane.
- Probability math/calibration/artifact: unchanged.
- Publication/rank/execution authority: unchanged (`false`).
- Terminal authority: `V17_TERMINAL_REDUCER`.
