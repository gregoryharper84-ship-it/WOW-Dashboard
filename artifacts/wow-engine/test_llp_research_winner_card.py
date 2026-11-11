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
                 'primary_favored_side':'Away',
                 'opponent_favored_side':'Home',
                 'contradictions_reviewed':True,
                 'scenarios':[
                     {'scenario_id':'baseline','path':'Away controls pace and half-court efficiency',
                      'favored_side':'Away','swing_factors':['pace'],'as_of_utc':ASOF},
                     {'scenario_id':'personnel','path':'Away uses bench depth to maintain late lead',
                      'favored_side':'Away','swing_factors':['replacement production'],'as_of_utc':ASOF},
                     {'scenario_id':'upset','path':'Home gains extra transition opportunities',
                      'favored_side':'Home','swing_factors':['turnovers'],'as_of_utc':ASOF},
                 ],
             },
             'matchup_media':{
                 'headlines':[
                     {'headline_id':'h1','publisher':'league.com',
                      'title':'Team Away seeks to control tempo','url':'https://league.com/previews/1',
                      'source_receipt_id':'research-source-h1','published_at_utc':ASOF,
                      'available_to_user_at_utc':ASOF,'stance':'SUPPORTS_PICK'},
                     {'headline_id':'h2','publisher':'beatwriter.com',
                      'title':'Home coaches identify transition as a key','url':'https://beatwriter.com/previews/2',
                      'source_receipt_id':'research-source-h2','published_at_utc':ASOF,
                      'available_to_user_at_utc':ASOF,'stance':'CHALLENGES_PICK'},
                 ],
                 'storylines':[
                     {'claim':'Away wants fewer high-variance possessions',
                      'headline_ids':['h1'],'stance':'SUPPORTS_PICK'},
                     {'claim':'Home seeks a faster transition game',
                      'headline_ids':['h2'],'stance':'CHALLENGES_PICK'},
                 ],
                 'counter_narrative':{'claim':'Home can win by speeding the pace',
                                      'headline_ids':['h2']},
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
    assert result['research_evidence_fully_reconciled'] is False
    assert all(r['selection_class']=='RESEARCH_PROVISIONAL' and r['model_status_declared']=='MODEL_UNAVAILABLE' and r['model_status']=='UNVERIFIED_CALLER_DECLARATION' for r in result['rows'])
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
    assert r['rows'][0]['model_status_declared']=='MODEL_UNAVAILABLE'
    assert r['rows'][0]['model_status']=='UNVERIFIED_CALLER_DECLARATION'
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
    assert r['research_evidence_fully_reconciled'] is False


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


def test_game_script_robustness_is_unweighted_not_probability():
    r=audit([event('NBA')],[pick('NBA')])
    s=r['rows'][0]['script_robustness']
    assert s['assessment']=='SELECTED_SIDE_FAVORED_IN_MORE_DOCUMENTED_SCRIPTS'
    assert (s['scenario_count'],s['favored_selected_count'],s['favored_opponent_count'])==(3,2,1)
    assert s['basis']=='UNWEIGHTED_RESEARCH_SCENARIOS_ONLY'
    assert s['probability'] is None
    assert r['rows'][0]['selected_side']=='Away'
    assert r['research_evidence_fully_reconciled'] is False


def test_script_sensitive_even_when_research_winner_stays_selected():
    p=pick('NFL')
    p['game_script']['scenarios'][1]['favored_side']='Home'
    r=audit([event('NFL')],[p])
    assert r['rows'][0]['script_robustness']['assessment']=='SCRIPT_SENSITIVE_OR_CONTRADICTORY'
    assert r['rows'][0]['selected_side']=='Away'


def test_future_or_weighted_scenario_does_not_create_a_model_probability():
    p=pick()
    p['game_script']['scenarios'][0]['as_of_utc']=START
    p['game_script']['scenarios'][1]['scenario_probability']=0.95
    r=audit([event()],[p])
    row=r['rows'][0]
    assert 'SCENARIO_FUTURE_OR_TIME_UNVERIFIED' in row['evidence_warnings']
    assert 'SCENARIO_UNCERTIFIED_NUMERIC_WEIGHT' in row['evidence_warnings']
    assert row['script_robustness']['assessment']=='NOT_EVALUATED'
    assert row['script_robustness']['probability'] is None
    assert row['governed_probability'] is None
    assert row['selected_side']=='Away'
    assert r['research_evidence_fully_reconciled'] is False


def test_headlines_storylines_counter_narrative_preserve_source_links():
    r=audit([event('NHL')],[pick('NHL')])
    row=r['rows'][0]
    assert len(row['media_headlines'])==2
    assert row['media_headlines'][0]['url']=='https://league.com/previews/1'
    assert len(row['media_storylines'])==2
    assert row['media_counter_narrative']['headline_ids']==['h2']
    assert row['media_evidence_status']=='STRUCTURAL_PREGAME_PROVENANCE_ONLY'
    assert row['governed_lower_bound'] is None


def test_late_fake_headline_and_unsourced_narrative_flagged():
    p=pick()
    p['matchup_media']['headlines'][1]['available_to_user_at_utc']=START
    r=audit([event()],[p])
    row=r['rows'][0]
    assert 'MEDIA_HEADLINE_FUTURE_OR_TIME_UNVERIFIED' in row['evidence_warnings']
    assert 'STORYLINE_INVALID_OR_UNSOURCED' in row['evidence_warnings']
    assert 'COUNTER_NARRATIVE_UNSOURCED' in row['evidence_warnings']
    assert len(row['media_headlines'])==1
    assert len(row['media_storylines'])==1
    assert row['selected_side']=='Away'
    assert r['research_evidence_fully_reconciled'] is False


def test_missing_media_keeps_research_pick_but_downgrades_quality():
    r=audit([event()],[pick(matchup_media=None,research_confidence='HIGH')])
    row=r['rows'][0]
    assert r['board_complete'] is True
    assert r['research_evidence_fully_reconciled'] is False
    assert 'MATCHUP_MEDIA_NOT_REVIEWED' in row['evidence_warnings']
    assert 'HIGH_CONFIDENCE_UNSUPPORTED' in row['evidence_warnings']
    assert row['selected_side']=='Away'


def test_unverified_media_cannot_sneak_unsupported_links_into_summary():
    p=pick()
    p['matchup_media']['headlines'][0]['url']='javascript:alert(1)'
    p['matchup_media']['headlines'][1]['source_receipt_id']=''
    r=audit([event()],[p])
    row=r['rows'][0]
    assert row['media_headlines']==[]
    assert 'MEDIA_HEADLINE_IDENTITY_OR_PROVENANCE_INVALID' in row['evidence_warnings']
    assert 'NO_ADMISSIBLE_PREGAME_HEADLINES' in row['evidence_warnings']
    assert row['media_counter_narrative'] is None
    assert r['board_complete'] is True


def test_counter_narrative_must_cite_an_opposing_headline():
    p=pick()
    p['matchup_media']['counter_narrative']['headline_ids']=['h1']
    r=audit([event()],[p])
    assert r['rows'][0]['media_counter_narrative'] is None
    assert 'COUNTER_NARRATIVE_UNSOURCED' in r['rows'][0]['evidence_warnings']
    assert r['rows'][0]['selected_side']=='Away'


def test_news_storyline_stance_requires_matching_reported_viewpoint():
    p=pick()
    p['matchup_media']['storylines'][0]['stance']='CHALLENGES_PICK'
    r=audit([event()],[p])
    assert 'STORYLINE_STANCE_NOT_SUPPORTED_BY_SOURCE' in r['rows'][0]['evidence_warnings']
    assert len(r['rows'][0]['media_storylines'])==1
    assert r['rows'][0]['governed_probability'] is None


def test_media_one_publisher_and_oversized_script_are_diagnostic_only():
    p=pick()
    p['matchup_media']['headlines'][1]['publisher']='league.com'
    p['game_script']['scenarios'] *= 4
    r=audit([event()],[p])
    w=r['rows'][0]['evidence_warnings']
    assert 'MEDIA_SOURCE_DIVERSITY_INSUFFICIENT' in w
    assert 'SCENARIO_LIMIT_TRUNCATED' in w
    assert r['board_complete'] is True
    assert r['rows'][0]['selected_side']=='Away'
    assert r['rows'][0]['script_robustness']['probability'] is None


def test_slate_attestation_rejects_truthy_nonbooleans():
    for forged in ('false', 'True', 1, ['verified']):
        result = audit_research_winner_card(
            [event()], [pick()], official_slate_verified=forged,
            official_slate_receipt_id='looks-authentic')
        assert result['board_complete'] is False
        assert 'OFFICIAL_SLATE_UNVERIFIED' in result['board_blockers']


def test_late_pick_never_exposes_postkickoff_press_or_game_scripts():
    late = pick(selected_at_utc='2026-11-11T01:10:00Z', research_confidence='HIGH')
    for headline in late['matchup_media']['headlines']:
        headline['published_at_utc'] = '2026-11-11T01:05:00Z'
        headline['available_to_user_at_utc'] = '2026-11-11T01:05:00Z'
    late['game_script']['as_of_utc'] = '2026-11-11T01:05:00Z'
    for scenario in late['game_script']['scenarios']:
        scenario['as_of_utc'] = '2026-11-11T01:05:00Z'
    row = audit([event()], [late])['rows'][0]
    assert row['selected_side'] is None
    assert row['selection_class'] == 'RESEARCH_INVALID'
    assert 'RESEARCH_PICK_NOT_PREGAME' in row['selection_blockers']
    assert row['media_headlines'] == []
    assert row['media_storylines'] == []
    assert row['media_counter_narrative'] is None
    assert row['script_robustness']['scenario_count'] == 0
    assert row['source_publishers_verified'] == 0
    assert row['game_script_primary_win_path'] is None
    assert row['media_evidence_status'] == 'NOT_ADMISSIBLE_INVALID_SELECTION'
    assert row['model_status'] == 'UNKNOWN'


def test_unapproved_game_script_likelihood_aliases_are_warned_separately():
    for alias in ('prob', 'odds', 'weights', 'win_pct', 'scenario_likelihood'):
        p = pick()
        p['game_script'][alias] = 0.99
        row = audit([event()], [p])['rows'][0]
        assert 'UNVERIFIED_GAME_SCRIPT_PROBABILITY' in row['evidence_warnings']
        assert row['game_script_verified_pregame'] is False
        assert row['game_script_primary_win_path'] is None
        assert row['governed_probability'] is None
        assert row['selected_side'] == 'Away'


def test_contradictory_script_sides_require_explicit_manual_review():
    for mutation in (
        {'opponent_favored_side':'Away'},
        {'primary_favored_side':'Home'},
        {'contradictions_reviewed':False},
    ):
        p = pick()
        p['game_script'].update(mutation)
        row = audit([event()], [p])['rows'][0]
        assert row['game_script_verified_pregame'] is False
        assert row['game_script_primary_win_path'] is None
        assert row['selected_side'] == 'Away'
        assert any(w.startswith('GAME_SCRIPT_') for w in row['evidence_warnings'])


def test_missing_or_wrong_event_identity_never_exposes_research_evidence():
    invalid = event()
    invalid['away_team'] = 'Home'
    row = audit([invalid], [pick()])['rows'][0]
    assert 'OFFICIAL_EVENT_IDENTITY_INVALID' in row['selection_blockers']
    assert row['media_headlines'] == []
    assert row['source_publishers_verified'] == 0
    assert row['selected_side'] is None

def test_duplicate_official_event_identity_cannot_expose_media_or_scripts():
    original = event("NBA", "identity-collision")
    collision = dict(original, away_team="DifferentAway")
    p = pick("NBA", "identity-collision", selected_side="Home")
    r = audit([original, collision], [p])
    assert r["board_complete"] is False
    assert "DUPLICATE_OFFICIAL_EVENT" in r["board_blockers"]
    assert len(r["rows"]) == 2
    for row in r["rows"]:
        assert "OFFICIAL_EVENT_IDENTITY_CONFLICT" in row["selection_blockers"]
        assert row["selection_class"] == "RESEARCH_INVALID"
        assert row["selected_side"] is None
        assert row["source_publishers_verified"] == 0
        assert row["media_headlines"] == []
        assert row["media_storylines"] == []
        assert row["game_script_verified_pregame"] is False
        assert row["governed_probability"] is None


def test_publishers_are_declared_not_independently_proven_by_this_helper():
    r = audit([event()], [pick()])
    row = r["rows"][0]
    assert r["board_complete"] is True
    assert r["research_evidence_fully_reconciled"] is False
    assert row["source_publishers_declared"] == 2
    assert row["source_publishers_verified"] == 0
    assert "NONMARKET_PUBLISHER_PROVENANCE_NOT_INDEPENDENTLY_VERIFIED" in row["evidence_warnings"]


def test_old_market_context_is_not_reconciled():
    p = pick()
    p["market_context"]["as_of_utc"] = "2026-10-01T12:00:00Z"
    r = audit([event()], [p])
    assert r["board_complete"] is True
    assert r["research_evidence_fully_reconciled"] is False
    assert "MARKET_CONTEXT_STALE_FOR_RECONCILIATION" in r["rows"][0]["evidence_warnings"]
    assert r["rows"][0]["governed_probability"] is None


def test_market_favorite_must_belong_to_the_current_game_even_if_disagreement_reviewed():
    p = pick(market_disagreement_reviewed=True)
    p["market_context"]["favorite"] = "Third Team"
    r = audit([event()], [p])
    assert "MARKET_FAVORITE_OUTSIDE_MATCHUP" in r["rows"][0]["evidence_warnings"]
    assert r["research_evidence_fully_reconciled"] is False
    assert r["rows"][0]["selected_side"] == "Away"


def test_caller_claimed_certified_status_never_becomes_verified_model_status():
    row = audit([event()], [pick(model_status="CERTIFIED")])["rows"][0]
    assert row["model_status_declared"] == "CERTIFIED"
    assert row["model_status"] == "UNVERIFIED_CALLER_DECLARATION"
    assert row["model_status_provenance"] == "CALLER_DECLARED_UNVERIFIED"
    assert row["governed_probability"] is None
    assert row["governed_lower_bound"] is None
