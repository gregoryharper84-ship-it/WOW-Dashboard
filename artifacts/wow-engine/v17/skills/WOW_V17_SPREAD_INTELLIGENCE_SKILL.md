# WOW V17 Cross-Sport Spreads — Research and Governed Admission

skill_id=WOW_V17_SPREAD_INTELLIGENCE
owner=LLP_TEAM_BETTING_ENGINE
status=RESEARCH_INTEGRATION_CONTRACT_NOT_LIVE
can_execute=false
terminal_authority=V17_TERMINAL_REDUCER

## Trigger
Use for `spreads across all sports`, `point-spread picks`, `puck-line winners`, `run-line picks`, `tennis handicap`, `soccer Asian handicaps`, or `postmortem ATS picks`.

## Research-first full-slate workflow
1. Resolve the independently authenticated official schedule for each requested league. Do not silently drop events, including postponed/cancelled or model-unavailable games.
2. Attach exact official event ID, opponents, UTC start, market type, settlement period, selected HOME/AWAY thesis, **signed home line**, authenticated same-time line quote and immutable pregame visibility receipt.
3. Refresh sport-specific material context from timestamped sources: NFL/NCAAF quarterback and injury status; NBA/WNBA/NCAAB roster and rotation; NHL starting goalie, rest and special teams; MLB confirmed starting pitcher; soccer lineup and regulation vs Asian handicap rules; tennis surface/format/retirement settlement. Missing proof remains a warning/typed hold, never fabricated.
4. Use `v17.llp_spread_research_card.audit_spread_research_card` for read-only exact-line coverage/settlement diagnostics. It NEVER chooses a side or generates model probabilities. Its `card_complete` is only caller-attested until independently authenticated source readback.
5. The controlling sport-specific fitted scoring-margin specialist (if truly certified) may independently provide `p_cover`, `p_push`, `p_not_cover`, uncertainty and immutable pregame receipt. A proposed candidate/shadow/research result is **never** a certified `Full Model` spread prediction. Use the existing `spread_card_admission` gate for exact event/side/line rank admission after authentic certification and terminal proof.
6. Rank only genuinely governed eligible calibrated spread receipts. Research side picks may be supplied for full-card coverage with `PROVISIONAL_RESEARCH_ONLY`, no calibrated probability, and no rank claim. Do not convert moneyline probabilities, favorites, sportsbook odds, or consensus into spread cover probabilities.
7. Grade exactly frozen line and side against independently verified official scores, with game format and cancellations; preserve WIN/LOSS/PUSH/HALF_WIN/HALF_LOSS, tennis retirement HOLD, still-pending, and invalid records. Headline hit rates require a fully verified cohort, with pushes excluded and Asian half outcomes reported separately.
8. Compare real forward predictions against timestamp-matched market baselines and proper Brier/log loss only if calibrated model probabilities, exact line market prices, complete source receipts and outcomes exist. No edge/profitability claims from qualitative hit rates.

## Sport/market coverage in research validator
- NFL, NBA, WNBA, NCAAF, NCAAB: POINT_SPREAD, FULL_GAME_INCLUDING_OVERTIME.
- NHL: PUCK_LINE, FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT.
- MLB: RUN_LINE, FULL_GAME_INCLUDING_EXTRA_INNINGS.
- SOCCER: ASIAN_HANDICAP, REGULATION (quarter-line split outcomes).
- TENNIS: GAME_HANDICAP or SET_HANDICAP, COMPLETED_MATCH_NO_RETIREMENT; retirement is HELD until provider-specific settlement attested.
- Other sports and unsupported market periods: retain visible `SPORT_MARKET_FORMAT_UNSUPPORTED`, not substitute a generic model.

## Governance
Read-only diagnostic. Cannot fit/recalibrate/promote, modify the certified sporting distribution, mutate a betting ledger, edit GH's Pick'em sheet, execute wagers, or auto-submit a pool entry. All input attestation is supplied by caller and requires independent readback. No serving integration is established by this skill file. Main fitted lane and `V17_TERMINAL_REDUCER` remain unchanged.
