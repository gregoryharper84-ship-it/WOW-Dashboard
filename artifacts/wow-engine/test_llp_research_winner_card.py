"""Full-slate human research picks must stay distinct from fitted model output."""
from copy import deepcopy

from v17.llp_research_winner_card import audit_research_winner_card

START = '2026-11-11T01:00:00Z'
ASOF = '2026-11-10T15:00:00Z'


def event(sport='NFL', identity='game-1'):
    return {'sport':sport,'official_event_id':identity,'home_team':'Home',
            'away_team':'Away','event_start_time_utc':START}


def pick(sport='NFL', identity='game-1', **changes):
    checks = {
        'NFL':('starting_qb','key_injuries','turnover_volatility'),
        'NBA':('star_availability','replacement_rotation','rest_travel','shooting_volatility'),
        'NHL':('starting_goalie','key_injuries','rest_travel','special_teams','ot_shootout'),
    }
    value = {'sport':sport,'official_event_id':identity,'selected_side':'Away',
             'selected_at_utc':ASOF,'reasoning':'Balanced pregame matchup research',
             'pregame_checks':{c:'KNOWN' for c in checks[sport]},
             'research_confidence':'MEDIUM',
             'research_sources':[
                 {'publisher':'league.com','source_type':'NON_MARKET','available_to_user_at_utc':ASOF},
                 {'publisher':'beatwriter.com','source_type':'NON_MARKET','available_to_user_at_utc':ASOF}],
             'model_status':'MODEL_UNAVAILABLE',
             'market_context':{'favorite':'Home','as_of_utc':ASOF},
             'market_disagreement_reviewed':True,
             'game_script':{
                 'primary_win_path':'Away controls matchups and avoids high-risk turnovers',
                 'opponent_win_path':'Home creates extra possessions and wins late',
                 'swing_factors':['starting personnel', 'pace and possession swing'],
                 'as_of_utc':ASOF,
             }}
    value.update(changes)
    return value


def audit(events, picks, verified=True):
    return audit_research_winner_card(events,picks,official_slate_verified=verified,
                                      official_slate_receipt_id='official-slate-1' if verified else None)


def test_all_three_sports_get_one_independent_research_winner_despite_model_hold():
    events=[event('NFL','n'),event('NBA','b'),event('NHL','h')]
    picks=[pick('NFL','n'),pick('NBA','b'),pick('NHL','h')]
    before=deepcopy((events,picks))
    result=audit(events,picks)
    assert result['official_games']==result['winner_picks']==3
    assert result['board_complete'] is True
    assert result['research_evidence_fully_reconciled'] is True
    assert all(r['selection_class']=='RESEARCH_PROVISIONAL' and r['model_status']=='MODEL_UNAVAILABLE' for r in result['rows'])
    assert all(r['governed_probability'] is None for r in result['rows'])
    assert result['fitted_model_samples_added']==0
    assert result['governed_model_modified'] is False
    assert result['can_execute'] is False
    assert (events,picks)==before


def test_uncertain_injury_requires_more_research_but_keeps_winner():
    checks={'star_availability':'CONTRADICTORY','replacement_rotation':'KNOWN','rest_travel':'KNOWN','shooting_volatility':'UNKNOWN'}
    result=audit([event('NBA')],[pick('NBA',pregame_checks=checks,research_confidence='HIGH')])
    row=result['rows'][0]
    assert row['selected_side']=='Away'
    assert result['board_complete'] is True
    assert result['research_evidence_fully_reconciled'] is False
    assert 'SCENARIO_REVIEW_REQUIRED' in row['evidence_warnings']
    assert 'HIGH_CONFIDENCE_UNSUPPORTED' in row['evidence_warnings']


def test_source_time_and_market_conflict_are_visible_without_fabricating_probabilities():
    r=audit([event()],[pick(research_sources=[{'publisher':'late.com','source_type':'NON_MARKET','available_to_user_at_utc':START}], market_disagreement_reviewed=False)])
    assert 'SOURCE_UNVERIFIED_OR_FUTURE' in r['rows'][0]['evidence_warnings']
    assert 'MARKET_CONFLICT_NOT_RECONCILED' in r['rows'][0]['evidence_warnings']
    assert r['rows'][0]['governed_lower_bound'] is None


def test_pass_is_invalid_and_missing_game_is_not_silently_omitted():
    r=audit([event('NFL','a'),event('NFL','b')],[pick('NFL','a',selected_side='PASS')])
    assert r['board_complete'] is False
    assert r['winner_picks']==0
    assert 'INVALID_OR_PASS_WINNER' in r['rows'][0]['selection_blockers']
    assert 'MISSING_OR_DUPLICATE_WINNER' in r['rows'][1]['selection_blockers']


def test_timestamps_duplicate_or_extra_events_fail_closed():
    assert audit([event()],[pick(selected_at_utc=START)])['board_complete'] is False
    assert audit([event()],[pick(),pick()])['board_complete'] is False
    assert audit([event()],[pick(),pick('NBA','other')])['board_complete'] is False
    assert audit([event(),event()],[pick()])['board_complete'] is False


def test_duplicate_selection_does_not_expose_last_pick_evidence():
    first = pick()
    second = pick(
        research_confidence="HIGH", model_status="CERTIFIED",
        research_sources=[
            {"publisher":"bogus1.example","source_type":"NON_MARKET","available_to_user_at_utc":ASOF},
            {"publisher":"bogus2.example","source_type":"NON_MARKET","available_to_user_at_utc":ASOF},
        ],
    )
    report = audit([event()], [first, second])
    row = report["rows"][0]
    assert report["board_complete"] is False
    assert "DUPLICATE_RESEARCH_SELECTION" in report["board_blockers"]
    assert "MISSING_OR_DUPLICATE_WINNER" in row["selection_blockers"]
    assert row["selected_side"] is None
    assert row["selection_class"] == "RESEARCH_INVALID"
    assert row["source_publishers_verified"] == 0
    assert row["model_status"] == "UNKNOWN"


def test_unverified_slate_must_not_claim_full_board():
    r=audit([event()],[pick()],verified=False)
    assert r['all_sides_selected'] is True
    assert r['board_complete'] is False
    assert 'OFFICIAL_SLATE_UNVERIFIED' in r['board_blockers']


def test_market_prices_are_never_spoofed_as_certified_sporting_probabilities():
    p=pick(model_probability=.89,market_probability=.77,calibrated_lower_bound=.82)
    r=audit([event()],[p])
    assert r['rows'][0]['model_status']=='MODEL_UNAVAILABLE'
    assert r['rows'][0]['governed_probability'] is None
    assert '.89' not in str(r)


def test_game_scripts_are_pregame_evidence_and_do_not_change_winner():
    r=audit([event('NBA')],[pick('NBA')])
    row=r['rows'][0]
    assert row['game_script_verified_pregame'] is True
    assert 'controls matchups' in row['game_script_primary_win_path']
    assert 'extra possessions' in row['game_script_opponent_win_path']
    assert len(row['game_script_swing_factors']) == 2
    assert row['selected_side']=='Away'
    assert row['governed_probability'] is None
    assert r['research_evidence_fully_reconciled'] is True


def test_missing_or_incomplete_game_script_warns_without_discretionary_pass():
    missing=audit([event('NHL')],[pick('NHL',game_script=None)])
    row=missing['rows'][0]
    assert row['selected_side']=='Away'
    assert row['game_script_verified_pregame'] is False
    assert 'GAME_SCRIPT_NOT_REVIEWED' in row['evidence_warnings']
    assert missing['board_complete'] is True
    assert missing['research_evidence_fully_reconciled'] is False

    incomplete=audit([event()],[pick(game_script={
        'primary_win_path':'Same',
        'opponent_win_path':'Same',
        'swing_factors':[],
        'as_of_utc':ASOF,
    })])
    row=incomplete['rows'][0]
    assert 'GAME_SCRIPT_SCENARIOS_INCOMPLETE' in row['evidence_warnings']
    assert 'GAME_SCRIPT_SWING_FACTORS_MISSING' in row['evidence_warnings']
    assert row['game_script_primary_win_path'] is None


def test_late_script_or_probability_claim_never_becomes_fitted_output():
    late=pick(game_script={
        'primary_win_path':'Away dominates',
        'opponent_win_path':'Home scores late',
        'swing_factors':['turnovers'],
        'as_of_utc':START,
        'win_probability':0.8,
    }, research_confidence='HIGH')
    row=audit([event()],[late])['rows'][0]
    assert 'GAME_SCRIPT_TIME_UNVERIFIED_OR_FUTURE' in row['evidence_warnings']
    assert 'UNVERIFIED_GAME_SCRIPT_PROBABILITY' in row['evidence_warnings']
    assert 'HIGH_CONFIDENCE_UNSUPPORTED' in row['evidence_warnings']
    assert row['game_script_primary_win_path'] is None
    assert row['governed_probability'] is None
    assert row['governed_lower_bound'] is None
