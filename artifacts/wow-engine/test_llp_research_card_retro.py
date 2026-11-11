"""Research-card retrospective must never manufacture fitted-model credit."""
from copy import deepcopy

import pytest

from v17.llp_research_card_retro import audit_research_card

START = '2026-11-08T22:00:00Z'
FREEZE = '2026-11-08T15:00:00Z'
FINAL = '2026-11-09T02:00:00Z'


def pick(sport='NFL', event_id='nfl-a', side='Home', role='PREFERRED', **updates):
    value = {
        'sport': sport, 'official_event_id': event_id,
        'home_team': 'Home', 'away_team': 'Away',
        'event_start_time_utc': START, 'selected_side': side,
        'settlement_basis': ('FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT' if sport == 'NHL'
                             else 'FULL_GAME_INCLUDING_OVERTIME'),
        'selection_role': role, 'immutable_pregame': True,
        'pregame_receipt_id': f'chat-prelock-{sport}-{event_id}-{role}',
        'pregame_receipt_verified': True,
        'frozen_at_utc': FREEZE, 'available_to_user_at_utc': FREEZE,
        'source_as_of_utc': '2026-11-08T14:30:00Z',
        'research_inputs': [
            {'factor':'TEAM_AVAILABILITY','available_to_user_at_utc':'2026-11-08T14:00:00Z'},
        ],
    }
    value.update(updates)
    return value


def result(sport='NFL', event_id='nfl-a', winner='Home', **updates):
    value = {
        'sport': sport, 'official_event_id': event_id,
        'home_team': 'Home', 'away_team': 'Away',
        'event_start_time_utc': START, 'winning_side': winner,
        'settlement_basis': ('FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT' if sport == 'NHL'
                             else 'FULL_GAME_INCLUDING_OVERTIME'),
        'final_status': 'FINAL', 'independently_verified': True,
        'settlement_receipt_id': f'official-final-{sport}-{event_id}',
        'settlement_source': 'OFFICIAL_LEAGUE', 'settled_at_utc': FINAL,
    }
    value.update(updates)
    return value


def manifest(*pairs):
    return [
        {'sport': sport, 'official_event_id': event_id, 'event_start_time_utc': START,
         'home_team': 'Home', 'away_team': 'Away'}
        for sport,event_id in pairs
    ]


def test_full_three_sport_card_and_upset_watch_grade_separately():
    picks = [pick(), pick(side='Away', role='UPSET_WATCH'),
             pick('NBA','nba-b','Home'), pick('NHL','nhl-c','Home')]
    results = [result(), result('NBA','nba-b','Away'), result('NHL','nhl-c','Home')]
    before = deepcopy((picks,results))
    report = audit_research_card(picks, results,
        official_slate=manifest(('NFL','nfl-a'),('NBA','nba-b'),('NHL','nhl-c')),
        official_slate_verified=True, official_slate_receipt_id='league-slate-snap-1')
    assert report['board_complete'] is True
    assert report['preferred'] == {'correct':2,'incorrect':1,'graded':3}
    assert report['upset_watch'] == {'correct':0,'incorrect':1,'graded':1}
    assert report['upset_pair_comparison'] == {'preferred_correct':1,'watch_correct':0,'matched_events':1}
    assert report['headline_accuracy'] == pytest.approx(2/3)
    assert [r['sport'] for r in report['by_sport']] == ['NBA','NFL','NHL']
    assert all(r['automatic_model_mutation'] is False for r in report['rows'])
    assert report['fitted_model_samples_added'] == 0
    assert report['can_execute'] is False
    assert (picks,results) == before


def test_unverified_pregame_receipt_cannot_grade_chat_hindsight():
    report = audit_research_card([pick(pregame_receipt_verified=False)], [result()],
        official_slate=manifest(('NFL','nfl-a')),
        official_slate_verified=True, official_slate_receipt_id='manifest-1')
    assert report['preferred']['graded'] == 0
    assert report['headline_accuracy'] is None
    assert 'PREGAME_RECEIPT_UNVERIFIED' in report['rows'][0]['blockers']
    assert report['board_complete'] is False


def test_game_started_before_pick_is_held():
    report = audit_research_card([pick(frozen_at_utc='2026-11-08T23:00:00Z')], [result()])
    assert report['rows'][0]['grade_status'] == 'HELD'
    assert 'PICK_NOT_FROZEN_PREGAME' in report['rows'][0]['blockers']


def test_postfreeze_evidence_cannot_be_used_in_frozen_replay():
    late_input = [{'factor':'IN_GAME_INJURY', 'available_to_user_at_utc':'2026-11-08T23:00:00Z'}]
    report = audit_research_card([pick(research_inputs=late_input)], [result()])
    assert 'HINDSIGHT_EVIDENCE_IN_FROZEN_PICK' in report['rows'][0]['blockers']
    assert report['preferred']['graded'] == 0


@pytest.mark.parametrize("missing", [None, [], ()])
def test_missing_or_empty_research_inputs_must_hold(missing):
    row = pick()
    if missing is None:
        row.pop("research_inputs")
    else:
        row["research_inputs"] = missing
    report = audit_research_card(
        [row], [result()], official_slate=manifest(("NFL", "nfl-a")),
        official_slate_verified=True, official_slate_receipt_id="manifest-1",
    )
    assert report["rows"][0]["grade_status"] == "HELD"
    assert "RESEARCH_INPUTS_INVALID" in report["rows"][0]["blockers"]
    assert report["preferred"]["graded"] == 0
    assert report["board_complete"] is False
    assert report["headline_accuracy"] is None


def test_unverified_or_unsettled_result_is_held():
    report = audit_research_card([pick()], [result(independently_verified=False)])
    assert report['preferred']['graded'] == 0
    assert 'OFFICIAL_FINAL_UNVERIFIED' in report['rows'][0]['blockers']
    report2 = audit_research_card([pick()], [result(final_status='IN_PROGRESS')])
    assert 'OFFICIAL_FINAL_NOT_FINAL' in report2['rows'][0]['blockers']


def test_incomplete_manifest_suppresses_headline_accuracy():
    report = audit_research_card([pick()], [result()],
        official_slate=manifest(('NFL','nfl-a'),('NFL','nfl-b')),
        official_slate_verified=True, official_slate_receipt_id='manifest-1')
    assert report['preferred'] == {'correct':1,'incorrect':0,'graded':1}
    assert report['headline_accuracy'] is None
    assert 'OFFICIAL_SLATE_PREFERRED_PICK_MISSING' in report['board_blockers']
    assert report['board_complete'] is False


def test_missing_verified_manifest_suppresses_headline_despite_correct_partial_grade():
    report = audit_research_card([pick()], [result()])
    assert report['preferred']['correct'] == 1
    assert report['headline_accuracy'] is None
    assert 'OFFICIAL_SLATE_NOT_VERIFIED' in report['board_blockers']


def test_duplicate_preferred_does_not_double_count_and_holds():
    report = audit_research_card([pick(), pick()], [result()],
        official_slate=manifest(('NFL','nfl-a')),
        official_slate_verified=True, official_slate_receipt_id='manifest-1')
    assert report['preferred']['graded'] == 0
    assert all(r['grade_status']=='HELD' for r in report['rows'])
    assert 'DUPLICATE_CARD_ROLE_FOR_EVENT' in report['board_blockers']


def test_postgame_causal_hypothesis_is_not_retrospective_model_evidence():
    report = audit_research_card([pick('NHL','nhl-c','Home')],
        [result('NHL','nhl-c','Away')],
        official_slate=manifest(('NHL','nhl-c')),
        official_slate_verified=True, official_slate_receipt_id='manifest-1')
    assert report['rows'][0]['grade_status'] == 'LOSS'
    assert report['learning_hypotheses'][0]['sport'] == 'NHL'
    assert 'GOALIE_REST_TRAVEL_SPECIAL_TEAMS' in report['learning_hypotheses'][0]['questions']
    assert report['learning_hypotheses'][0]['finding_status'] == 'HYPOTHESIS_NOT_VALIDATED'
    assert report['automatic_parameter_mutation'] is False


def test_away_team_or_kickoff_identity_mismatch_holds():
    report = audit_research_card([pick()], [result(away_team='Another')])
    assert 'OFFICIAL_EVENT_IDENTITY_CONFLICT' in report['rows'][0]['blockers']
    report2 = audit_research_card([pick()], [result(event_start_time_utc='2026-11-08T23:00:00Z')])
    assert 'OFFICIAL_EVENT_IDENTITY_CONFLICT' in report2['rows'][0]['blockers']


def test_no_fitted_probabilities_or_automatic_upset_flip_in_report():
    p=pick();p['market_implied_probability']=0.92
    report = audit_research_card([p,pick(role='UPSET_WATCH',side='Away')],[result()])
    assert 'market_implied_probability' not in str(report)
    assert 'calibrated_probability' not in str(report)
    assert report['automatic_upset_flip'] is False
    assert report['model_certification_impact'] is False
    assert report['gh_submitted_card_graded'] is False


def test_synthetic_nov8_shape_keeps_21_of_26_out_of_model_certification():
    # Synthetic data with the retrospective's reported 13/7/6 shape.
    # These are NOT authentic historical pregame or settlement receipts.
    per_sport = {'NFL': (13, 12), 'NBA': (7, 5), 'NHL': (6, 4)}
    card, finals, schedule = [], [], []
    for sport, (total, wins) in per_sport.items():
        for n in range(total):
            game = f'{sport.lower()}-{n}'
            card.append(pick(sport, game))
            finals.append(result(sport, game, winner='Home' if n < wins else 'Away'))
            schedule.extend(manifest((sport, game)))
    # Research upset alerts for three separate NFL events: all wrong.
    card.extend(pick('NFL', f'nfl-{n}', side='Away', role='UPSET_WATCH') for n in range(3))
    audit = audit_research_card(card, finals, official_slate=schedule,
        official_slate_verified=True, official_slate_receipt_id='synthetic-fixture-only')
    assert audit['board_complete'] is True
    assert audit['preferred'] == {'correct':21, 'incorrect':5, 'graded':26}
    assert audit['upset_watch'] == {'correct':0, 'incorrect':3, 'graded':3}
    assert audit['upset_pair_comparison']['preferred_correct'] == 3
    assert {x['sport']: x['preferred']['correct'] for x in audit['by_sport']} == {'NFL':12,'NBA':5,'NHL':4}
    assert audit['fitted_model_samples_added'] == 0
    assert audit['gh_submitted_card_graded'] is False


def test_upset_watch_same_side_as_primary_is_held_not_double_counted():
    report = audit_research_card([pick(), pick(role='UPSET_WATCH')], [result()],
        official_slate=manifest(('NFL','nfl-a')),
        official_slate_verified=True, official_slate_receipt_id='manifest-1')
    assert report['upset_watch']['graded'] == 0
    assert report['upset_pair_comparison']['matched_events'] == 0
    assert 'UPSET_WATCH_NOT_ALTERNATIVE' in report['rows'][1]['blockers']


def test_reused_pregame_receipt_across_events_cannot_grade_two_games():
    first = pick()
    second = pick('NBA', 'nba-b', pregame_receipt_id=first['pregame_receipt_id'])
    report = audit_research_card([first, second],
        [result(), result('NBA', 'nba-b')])
    assert report['preferred']['graded'] == 0
    assert 'DUPLICATE_PREGAME_RECEIPT_ID' in report['board_blockers']


def test_reused_official_settlement_receipt_across_events_cannot_grade_two_games():
    first = result()
    second = result('NHL', 'nhl-c', settlement_receipt_id=first['settlement_receipt_id'])
    report = audit_research_card([pick(), pick('NHL', 'nhl-c')], [first, second])
    assert report['preferred']['graded'] == 0
    assert 'DUPLICATE_OFFICIAL_SETTLEMENT_RECEIPT_ID' in report['board_blockers']


def test_research_final_rejects_period_or_regulation_only_settlement():
    for sport in ("NFL", "NBA", "NHL"):
        initial = pick(sport=sport)
        for invalid in ("FIRST_HALF", "FIRST_PERIOD", "REGULATION", None):
            bad = result(sport=sport, settlement_basis=invalid)
            report = audit_research_card(
                [initial], [bad],
                official_slate=manifest((sport, "nfl-a")),
                official_slate_verified=True, official_slate_receipt_id="official-slate")
            assert report["rows"][0]["grade_status"] == "HELD"
            assert "FULL_GAME_SETTLEMENT_BASIS_UNVERIFIED" in report["rows"][0]["blockers"]
            assert report["headline_accuracy"] is None


def test_research_pick_requires_same_full_game_basis_as_official_final():
    forecast = pick(sport="NHL", settlement_basis="REGULATION")
    report = audit_research_card([forecast], [result(sport="NHL")],
        official_slate=manifest(("NHL", "nfl-a")),
        official_slate_verified=True, official_slate_receipt_id="official-slate")
    assert report["rows"][0]["grade_status"] == "HELD"
    assert "FULL_GAME_SETTLEMENT_BASIS_UNVERIFIED" in report["rows"][0]["blockers"]


def test_missing_manifest_game_is_a_visible_ungraded_held_row():
    report = audit_research_card([pick()], [result()],
        official_slate=manifest(("NFL", "nfl-a"), ("NFL", "nfl-b")),
        official_slate_verified=True, official_slate_receipt_id="official-slate")
    missing = [x for x in report["rows"] if x["official_event_id"] == "nfl-b"]
    assert len(missing) == 1
    assert missing[0]["selection_role"] == "PREFERRED"
    assert missing[0]["grade_status"] == "HELD"
    assert missing[0]["selected_side"] is None
    assert "OFFICIAL_SLATE_PREFERRED_PICK_MISSING" in missing[0]["blockers"]
    assert report["preferred"]["graded"] == 1
    assert report["headline_accuracy"] is None
