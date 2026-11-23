"""Contract tests for research-only scenario-to-usage bridge."""
from copy import deepcopy
from math import isnan
from shadow_bridge import evaluate_scenario_outcomes, evaluate_player_usage

HASH = 'a'*64

def snapshot(**changes):
    s = dict(
        sport='NFL', official_event_id='nfl-2026-101', event_start_utc='2026-11-19T01:20:00Z',
        snapshot_at_utc='2026-11-18T19:00:00Z', scenario_fit_cutoff_utc='2026-11-17T18:00:00Z',
        scenario_model_artifact_sha256=HASH, feature_manifest_sha256='b'*64,
        scenario_receipt_id='frozen-scenarios-1', favorite_side='HOME', outcome_space='TWO_WAY_FULL_GAME_OVERTIME',
        scenarios=[
            dict(scenario_id='home-leads', weight=.35, outcome='HOME', home_game_state='LEADING', away_game_state='TRAILING'),
            dict(scenario_id='home-trails-wins', weight=.10, outcome='HOME', home_game_state='TRAILING', away_game_state='LEADING'),
            dict(scenario_id='away-leads', weight=.45, outcome='AWAY', home_game_state='TRAILING', away_game_state='LEADING'),
            dict(scenario_id='away-trails-wins', weight=.10, outcome='AWAY', home_game_state='LEADING', away_game_state='TRAILING'),
        ],
    )
    s.update(changes)
    return s


def player(**changes):
    p = dict(
        sport='NFL', official_event_id='nfl-2026-101', market_id='henry-rush-att',
        player_id='DERRICK_HENRY', exact_line=18.5,
        prop_fit_artifact_sha256='c'*64, prop_fit_cutoff_utc='2026-11-17T18:00:00Z',
        prop_source_manifest_sha256='d'*64, settlement_basis='FULL_GAME_OVERTIME',
        scenario_counts=[
            dict(scenario_id='home-leads',expected_count=24,count_variance=8,p_over_exact_line=.78),
            dict(scenario_id='home-trails-wins',expected_count=11,count_variance=6,p_over_exact_line=.12),
            dict(scenario_id='away-leads',expected_count=11,count_variance=6,p_over_exact_line=.12),
            dict(scenario_id='away-trails-wins',expected_count=24,count_variance=8,p_over_exact_line=.78),
        ],
    )
    p.update(changes)
    return p


def test_mixture_is_not_ml_price_proxy():
    r=evaluate_scenario_outcomes(snapshot())
    assert r['status']=='OPPONENT_SCENARIO_SHADOW'
    assert r['home_win_probability']==.45
    assert r['away_win_probability']==.55
    assert r['winner_research_argmax']=='AWAY'
    assert r['opponent_upset_paths']==['away-leads','away-trails-wins']
    assert r['can_execute'] is False and r['probability_publishable'] is False
    assert r['terminal_authority']=='V17_TERMINAL_REDUCER'
    assert r['joint_model_certified'] is False


def test_trailing_game_state_may_still_win():
    r=evaluate_scenario_outcomes(snapshot())
    assert r['home_win_probability']==.45  # includes trailing home-win path


def test_player_mixture_links_to_same_scenarios():
    r=evaluate_player_usage(snapshot(),player())
    assert r['status']=='PLAYER_USAGE_SHADOW'
    assert abs(r['expected_count']-(24*.45+11*.55))<1e-10
    assert abs(r['p_over_exact_line']-(.78*.45+.12*.55))<1e-10
    assert r['variance']>0
    assert r['game_script_source']=='FROZEN_FITTED_TEAM_EVENT_SCENARIOS'
    assert r['ranking_eligible'] is False


def test_higher_trailing_probability_changes_usage_without_hard_veto():
    s=snapshot(); s['scenarios'][0]['weight']=.15; s['scenarios'][2]['weight']=.65
    a=evaluate_player_usage(snapshot(),player())
    b=evaluate_player_usage(s,player())
    assert b['expected_count']<a['expected_count']
    assert b['can_execute'] is False


def test_duplicate_scenario_rejected():
    s=snapshot();s['scenarios'][1]['scenario_id']='home-leads'
    assert evaluate_scenario_outcomes(s)['status']=='SCENARIO_INPUT_UNAVAILABLE'


def test_weights_must_be_normalized_not_repaired():
    s=snapshot();s['scenarios'][0]['weight']=.7
    assert evaluate_scenario_outcomes(s)['status']=='SCENARIO_INPUT_UNAVAILABLE'


def test_nan_negative_and_bool_weights_rejected():
    for invalid in [float('nan'),-0.1,True,float('inf')]:
        s=snapshot();s['scenarios'][0]['weight']=invalid
        assert evaluate_scenario_outcomes(s)['status']=='SCENARIO_INPUT_UNAVAILABLE'


def test_invalid_identity_and_snapshot_timing_rejected():
    for change in [dict(official_event_id=''),dict(snapshot_at_utc='2026-11-19T01:21:00Z'),
                   dict(scenario_fit_cutoff_utc='2026-11-18T20:00:00Z'),
                   dict(scenario_model_artifact_sha256='bad')]:
        assert evaluate_scenario_outcomes(snapshot(**change))['status']=='SCENARIO_INPUT_UNAVAILABLE'


def test_outcome_space_tie_not_silently_resolved():
    s=snapshot();s['scenarios'][0]['outcome']='DRAW'
    assert evaluate_scenario_outcomes(s)['status']=='SCENARIO_INPUT_UNAVAILABLE'
    s=snapshot(outcome_space='THREE_WAY_REGULATION');s['scenarios'][0]['outcome']='DRAW'
    r=evaluate_scenario_outcomes(s)
    assert r['status']=='OPPONENT_SCENARIO_SHADOW' and r['draw_probability']==.35


def test_player_sport_event_and_settlement_mismatch_rejected():
    for change in [dict(official_event_id='wrong'),dict(sport='NBA'),dict(settlement_basis='REGULATION_ONLY'),
                   dict(prop_fit_cutoff_utc='2026-11-18T22:00:00Z')]:
        assert evaluate_player_usage(snapshot(),player(**change))['status']=='PLAYER_EVIDENCE_UNAVAILABLE'


def test_missing_player_scenarios_are_not_filled():
    p=player();p['scenario_counts'].pop()
    assert evaluate_player_usage(snapshot(),p)['status']=='PLAYER_EVIDENCE_UNAVAILABLE'


def test_invalid_player_count_probability_is_not_fabricated():
    for bad in [float('nan'),1.1,-.1,None]:
        p=player();p['scenario_counts'][0]['p_over_exact_line']=bad
        assert evaluate_player_usage(snapshot(),p)['status']=='PLAYER_EVIDENCE_UNAVAILABLE'


def test_same_source_input_not_modified():
    s,p=snapshot(),player();before=(deepcopy(s),deepcopy(p))
    evaluate_player_usage(s,p)
    assert (s,p)==before


def test_unavailable_model_never_outputs_candidate_probability():
    r=evaluate_scenario_outcomes(snapshot(scenario_receipt_id=''))
    assert r['home_win_probability'] is None and r['winner_research_argmax'] is None


def test_malformed_market_basis_never_crashes():
    for invalid in [[], {}, 42, None]:
        r=evaluate_scenario_outcomes(snapshot(outcome_space=invalid))
        assert r['status']=='SCENARIO_INPUT_UNAVAILABLE'


def test_duplicate_trimmed_scenario_identity_is_not_accepted():
    s=snapshot();s['scenarios'][1]['scenario_id']='home-leads '
    assert evaluate_scenario_outcomes(s)['status']=='SCENARIO_INPUT_UNAVAILABLE'


def test_player_fit_after_kickoff_not_allowed():
    p=player(prop_fit_cutoff_utc='2026-11-19T02:00:00Z')
    assert evaluate_player_usage(snapshot(),p)['status']=='PLAYER_EVIDENCE_UNAVAILABLE'


def test_unhashable_outcome_and_state_rejected():
    for field in ('outcome','home_game_state','away_game_state'):
        s=snapshot();s['scenarios'][0][field]=[]
        assert evaluate_scenario_outcomes(s)['status']=='SCENARIO_INPUT_UNAVAILABLE'


def test_huge_integer_weight_and_player_count_rejected():
    s=snapshot();s['scenarios'][0]['weight']=10**5000
    assert evaluate_scenario_outcomes(s)['status']=='SCENARIO_INPUT_UNAVAILABLE'
    p=player();p['scenario_counts'][0]['expected_count']=10**5000
    assert evaluate_player_usage(snapshot(),p)['status']=='PLAYER_EVIDENCE_UNAVAILABLE'


def test_scenario_event_favorite_side_and_phase_cannot_be_inferred():
    s=snapshot(favorite_side=None)
    assert evaluate_scenario_outcomes(s)['status']=='SCENARIO_INPUT_UNAVAILABLE'


def test_utc_boundary_overflow_fails_closed_in_both_evaluators():
    for bad in ('0001-01-01T00:00:00+01:00', '9999-12-31T23:59:59-01:00'):
        for field in ('snapshot_at_utc', 'event_start_utc', 'scenario_fit_cutoff_utc'):
            s = snapshot(**{field: bad})
            assert evaluate_scenario_outcomes(s)['status'] == 'SCENARIO_INPUT_UNAVAILABLE'
            assert evaluate_player_usage(s, player())['status'] == 'PLAYER_EVIDENCE_UNAVAILABLE'
        assert evaluate_player_usage(
            snapshot(), player(prop_fit_cutoff_utc=bad)
        )['status'] == 'PLAYER_EVIDENCE_UNAVAILABLE'


def test_extreme_finite_player_moments_fail_closed():
    p = player()
    p['scenario_counts'][0]['expected_count'] = 1e200
    assert evaluate_player_usage(snapshot(), p)['status'] == 'PLAYER_EVIDENCE_UNAVAILABLE'

    p = player()
    for item in p['scenario_counts']:
        item['count_variance'] = 1.7976931348623157e308
    p['scenario_counts'][0]['expected_count'] = 1e154
    assert evaluate_player_usage(snapshot(), p)['status'] == 'PLAYER_EVIDENCE_UNAVAILABLE'
