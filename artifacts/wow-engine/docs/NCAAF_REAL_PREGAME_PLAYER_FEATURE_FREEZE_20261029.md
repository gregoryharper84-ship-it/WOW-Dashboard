# WOW V17 NCAAF — verified first genuinely forward player-level source freezes

Date: October 29, 2026. Supabase validation environment only. This is **research**, not betting/scoring publication.

## Actual database execution and acceptance

Reused already normalized, source-hashed CFBD player-game outcomes from the 2026 season. For a **different, upcoming** official game, the database captures a new transaction-time `feature_as_of` before the game's latest observed official kickoff, and freezes the last four or five prior player receiving-yard results. It does not treat historical 2023–2025 retrospective result downloads as historical point-in-time pregame features.

Private table `wow_prop_history.ncaaf_forward_pregame_player_features` was independently created and read back. The accompanying `ncaaf_forward_pregame_player_feature_freeze_v1.sql` is source-controlled. Its private invoker-only function `freeze_ncaaf_future_player_form(official_event_id,stat_type)` was actually installed and successfully invoked for the second game, generating a compact non-executable receipt.

| Official game ID | Teams | Most recently observed kickoff (UTC) | Actual frozen feature_as_of (UTC) | Candidate prior-player-form records |
| --- | --- | --- | --- | ---: |
| `401856842` | Texas Tech vs Arizona | 2026-10-31 16:00 | 2026-10-29 19:08:59 | 17 |
| `401856844` | UCF vs Baylor | 2026-10-30 23:30 | 2026-10-29 19:14:20 | 18 |
| **Total** | Two future games | | | **35** |

All 35 were confirmed individually source-hashed, at least four genuine previous-game outcomes, and timed prior to the *planned* kickoff. Independent database audit returned **zero source-hash mismatches, zero timing violations, zero rows with current-roster, exact sportsbook line, model training, probability publication or execution authority**. All are actual observed-before-event **research features**; missing current roster verification and future kickoff revisions remain independent holds.

**Security:** `wow_prop_history` is a private schema, RLS ON, with neither anon nor authenticated read or function execute; only service-role research inserts/reads and function execute. No GitHub bearer or Supabase secret is added to any file, Action or workflow. The function uses the database's own transaction timestamp, not a caller-supplied timestamp, and refuses already-resulted games.

## Reason for this approach

The NCAAF 205,144 historical result rows have excellent identity consistency but were backfilled on October 29. They could not honestly demonstrate a historical feature observed before kickoff. The future-game source freezes are the first clean path to a **forward true player-level point-in-time observation**, while still keeping current participation unknown, predictions unfit/unscored and calibration unauthorized.

This is an input-evidence milestone toward [P0 three-sport model demonstrations](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2285), not one of the three completed fitted/calibrated model acceptance receipts.

Next: verify contemporaneous roster/availability, fit/validate route-specific NCAAF receiving-yards challenger with honest untouched chronology, use an independently sourced exact pregame line, perform an authentic canonical V17 scorer Action and obtain real terminal receipt. No probabilities are inferred from recent hit rates or market lines, and `can_execute=false`.

Parallel research continues for PropsMadness NFL/NBA/MLB/WNBA subject to account-data reuse rights and for separate independently licensed NHL acquisition.
