# V17 Schedule, Travel, Rest & Fatigue Research Context v1

Owner: LLP_TEAM_BETTING_ENGINE team/event research (#2076). The context is additive to v17.team_state_intelligence.paired_matchup_features via paired_schedule_fatigue_context. It is NOT inserted into existing numerical model vectors or a production decision route. No parallel predictor.

## Mechanism and invariants

Existing rest/back-to-back fields do not reconstruct venue/travel sequence and are insufficient proof of fatigue-model consumption. A research context is therefore generated from official-event identifiers, pre-freeze evidence, completed prior events, venue role and physical venue coordinates, and optional measured workload. A missing source, previous game, workload, venue, or completeness attestation is unknown, never zero. No learned effect size exists in this patch.

Input target: official_event_id, sport, event_start_utc, as_of_utc strictly earlier, participants (1+ stable IDs), roles map (home/away/neutral), optional venue {latitude, longitude, time_zone}, source_uri (HTTPS), source_payload_sha256, source_available_at.

Input prior events: same sport, different official_event_id, event_start_utc, status (FINAL/SCHEDULED/CANCELLED/POSTPONED), source receipt. FINAL also needs event_end_utc strictly after start and no later than freeze, roles, optional venue and workloads {participant ID: {value, unit}}. Never provide a source receipt from after freeze, even when it describes an old match. Cancelled/postponed/scheduled records are ignored for completed-game counts; a final reported before the event ended is invalid. history_completeness is an explicit caller attestation per participant and is not independent proof of a complete official slate.

Outputs: rest hours since prior completed event end; previous-day back-to-back; 4-/6-day inclusive venue-local density; consecutive road events; straight-line venue-to-venue miles; 7-day venue legs; local clock UTC offset change (not proof of actual time-zone crossing or overnight flights); observed 7-day homogeneous workload totals; two-participant differences only for comparable units. The context includes missing fields, an evidence fingerprint of the complete input, and an explicit research/partial status.

**Safety:** CAN_EXECUTE=False, terminal=V17_TERMINAL_REDUCER, probabilities=null, probability_publishable=false, model_feature_consumption_verified=false, source_authenticity_independently_verified=false. Source hash and URL syntax are checked, not externally fetched/attested. Even a complete context is research-only. This module never makes, ranks, certifies, submits or alters a pick.

## Sport-dependent hydration required before live evidence coverage

| Sport | Inputs beyond generic chronology, to be separately acquired/validated |
|---|---|
| NBA | Player minutes, overtime minutes, rotations, 3-in-4 and 4-in-6, altitude, consecutive road games |
| WNBA | Compressed schedule, rotation minutes, international duty and travel |
| NHL | Goalie workload and confirmations, overnight road legs, 3-in-4 |
| NFL | Short weeks, byes, 3-in-11 days, snap volume, international travel |
| NCAAF | Bye weeks, travel, short-week games, injury/roster workload |
| NCAAB | Tournament-day density, travel and cumulative rotation minutes |
| MLB | Consecutive game days, night-to-day turnarounds, bullpen pitches, catcher workload |
| SOCCER | Midweek/continental and international duty, minutes by player, time zones |
| TENNIS | Prior match duration, late finish, surface changes, travel, 3-/5-set exposure |
| PGA | Week-to-week itineraries, tee-wave conditions, walking and weather exposure |
| MMA | Training camp, short-notice bouts, weigh-in/weight cut, layoff or recovery |
| BOXING | Preparation duration, weight cuts, previous fight damage and layoff |
| CRICKET | Bowling balls/overs, consecutive matches, day-night shifts, format and travel |

Generic shared outputs are not a substitute for sport-specific fatigue models. Short-notice, jet lag, altitude, verified sleep, injury effects and true air travel are NOT derived from these event records.

## Promotion and checks

1. Prove each sport's event coverage, venue provenance, complete historical schedule and time-correct timestamps with external independent primary-source receipts.
2. Construct train/holdout comparisons with chronological splits, team/player/entity groupings, event and season regimes, and calibration/uncertainty. Model rest, travel, opponent rest, roster depth and play style interactions jointly to avoid double counting correlated density features. Include shift/lineup uncertainty.
3. Independently test challenger against existing fitted champion on Brier, log loss, calibration, tail and conditional performance by sport/market, including subgroup and away/road fatigue, and forward pregame frozen receipts. Negative controls must detect after-freeze injection, duplicate event identity, missing workload and score leakage.
4. Only through separately governed C-class fitted-model promotion and independent exact-head QA/Release acceptance may specific vector fields be consumed, with feature IDs and hash in an immutable model receipt. No probability or threshold change in this R research PR.
5. Inspect full-slate acquisition coverage, qualified rank-eligible rows, main/Render SHAs, health route, Supabase receipt persistence, and settle independent grades before claiming production completion.

Local synthetic checks are not verification of actual sources, live scoring, or calibration. No overnight travel or fatigue effect is presumed merely because an athlete has a compressed schedule.
