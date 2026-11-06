# Daily prop zero-row cause separation — 2026-11-06

- date: 2026-11-06
- typed_reason: PROP_ACQUISITION_NO_ELIGIBLE_EVENTS:MLB
- mechanism: The Daily snapshot runner invoked the MLB producer even at `max_props=0`, then used `true_zero_upstream` to emit a missing-snapshot blocker for both request-bound zero and a successfully empty MLB official-probable-pitchers schedule.
- detection_point: At the first request with `max_props=0`, or the first out-of-season MLB slate with a successful zero-attempt acquisition receipt. The lane reason and global blocker should have been compared before publication of the run response.
- fix: Do not call acquisition for a zero prop limit. Require a positive bound and a real acquisition receipt before upstream-empty inference. Emit the MLB-specific no-eligible-events reason only for an identified successfully completed MLB producer; leave unknown, failed and persisted-but-unreadable states fail-closed.
- prevention_artifact: `artifacts/wow-engine/v17/test_daily_snapshot_runtime.py` — five regression tests covering MLB no-event, requested-zero props, requested-zero moneyline, invalid timezone and mixed-lane zero-props/positive-moneyline. Review found two additional mechanisms: `_schedule_pitchers` swallows an invalid IANA timezone as an empty candidate list, and a mixed request with no moneyline rows could drop the empty-run blocker. Both were removed by explicit input and mixed-lane guards.
- link: https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2496
- verification: 54 affected tests passed at `c30dd7fca76a2094809a9c51d3db7e03dfdfa031`. Red test-first SHA `c61925a2` was superseded before CI completed; do not count as an executed red run. Production route/Supabase/live SHA are not yet verified.
- status: MOVING — protected exact-head independent review and production acceptance remain required.

## Promotion
The lesson is enforced mechanically by the three regression tests rather than new prose-only rules. No scoring probability, calibration, certification, market order or execution authority changes.
