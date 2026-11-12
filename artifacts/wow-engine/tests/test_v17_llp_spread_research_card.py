"""Adversarial tests for LLP's read-only cross-sport spread research receipt."""
from copy import deepcopy
import pytest

from v17.llp_spread_research_card import audit_spread_research_card

START = '2026-11-12T23:00:00Z'
FREEZE = '2026-11-12T22:00:00Z'
OBSERVED = '2026-11-12T21:45:00Z'
SETTLED = '2026-11-13T02:30:00Z'

RULES = {
    'NFL': ('POINT_SPREAD', 'FULL_GAME_INCLUDING_OVERTIME'),
    'NBA': ('POINT_SPREAD', 'FULL_GAME_INCLUDING_OVERTIME'),
    'WNBA': ('POINT_SPREAD', 'FULL_GAME_INCLUDING_OVERTIME'),
    'NCAAF': ('POINT_SPREAD', 'FULL_GAME_INCLUDING_OVERTIME'),
    'NCAAB': ('POINT_SPREAD', 'FULL_GAME_INCLUDING_OVERTIME'),
    'NHL': ('PUCK_LINE', 'FULL_GAME_INCLUDING_OVERTIME_SHOOTOUT'),
    'MLB': ('RUN_LINE', 'FULL_GAME_INCLUDING_EXTRA_INNINGS'),
    'SOCCER': ('ASIAN_HANDICAP', 'REGULATION'),
    'TENNIS': ('GAME_HANDICAP', 'COMPLETED_MATCH_NO_RETIREMENT'),
}


def event(sport='NFL', event_id='game-a', **override):
    market, basis = RULES.get(sport, ('UNKNOWN', 'UNKNOWN'))
    data = dict(sport=sport, official_event_id=event_id, home_team='Home', away_team='Away',
                event_start_time_utc=START, event_state='SCHEDULED',
                market_type=market, settlement_basis=basis)
    data.update(override)
    return data


def pick(sport='NFL', event_id='game-a', side='HOME', line=-3.5, **override):
    source = event(sport, event_id)
    data = dict(source, selected_side=side, signed_home_line=line,
                frozen_at_utc=FREEZE, available_to_user_at_utc=FREEZE,
                quote_source_id='FIXTURE_SOURCE_A',
                immutable_pregame=True, pregame_receipt_verified=True,
                pregame_receipt_id=f'pick-{sport}-{event_id}',
                quote=dict(source, signed_home_line=line, observed_at_utc=OBSERVED,
                           quote_source_id='FIXTURE_SOURCE_A',
                           available_to_user_at_utc=OBSERVED, independently_verified=True,
                           receipt_id=f'quote-{sport}-{event_id}'))
    data.update(override)
    return data


def final(sport='NFL', event_id='game-a', hs=30, aws=20, **override):
    data = dict(event(sport, event_id), final_status='FINAL', independently_verified=True,
                settlement_receipt_id=f'final-{sport}-{event_id}',
                settlement_source='OFFICIAL_LEAGUE', settled_at_utc=SETTLED,
                home_score=hs, away_score=aws)
    data.update(override)
    return data


def audit(slate, picks, finals=(), **kw):
    return audit_spread_research_card(slate, picks, finals,
        official_slate_verified=True, official_slate_receipt_id='official-slate-001', **kw)


def test_nine_sport_frozen_full_slate_is_research_only_even_without_finals():
    sports = list(RULES)
    fixtures = [event(s, f'{s}-1') for s in sports]
    picks = [pick(s, f'{s}-1', line=(-0.5 if s == 'SOCCER' else -1.5)) for s in sports]
    original = deepcopy((fixtures, picks))
    result = audit(fixtures, picks)
    assert result['scheduled_count'] == result['playable_count'] == 9
    assert result['card_complete'] is True
    assert result['settlement_complete'] is False
    assert result['pending_count'] == 9
    assert result['headline_full_win_rate'] is None
    assert len(result['rows']) == 9
    assert result['fitted_model_samples_added'] == 0
    assert result['probability_publishable'] is False
    assert result['automatic_certification'] is False
    assert result['can_execute'] is False
    assert all(r['research_status'] == 'PROVISIONAL_RESEARCH_ONLY' for r in result['rows'])
    assert all(r['rank_eligible'] is False and r['model_probability'] is None for r in result['rows'])
    assert (fixtures, picks) == original


def test_unverified_manifest_cannot_claim_complete_card():
    r = audit_spread_research_card([event()], [pick()], [final()],
        official_slate_verified=False, official_slate_receipt_id='false-receipt')
    assert r['card_complete'] is False
    assert r['settlement_complete'] is False
    assert r['headline_full_win_rate'] is None
    assert 'OFFICIAL_SLATE_NOT_VERIFIED' in r['board_blockers']


def test_missing_pick_is_visible_and_blocks_full_slate():
    r = audit([event(), event('NBA', 'b')], [pick()], [final()])
    assert r['playable_count'] == 2
    assert r['card_complete'] is False
    assert len(r['rows']) == 2
    assert r['rows'][1]['pregame_status'] == 'HELD'
    assert 'PREGAME_PICK_MISSING' in r['rows'][1]['blockers']
    assert r['headline_full_win_rate'] is None


def test_duplicate_event_identity_holds_all_not_last_write_wins():
    canonical = event()
    alternate = event(away_team='Different')
    for manifest in ([canonical, alternate], [alternate, canonical]):
        r = audit(manifest, [pick()], [final()])
        assert r['card_complete'] is False
        assert r['rows'][0]['pregame_status'] == 'HELD'
        assert 'DUPLICATE_OFFICIAL_EVENT' in r['rows'][0]['blockers']
        assert r['headline_full_win_rate'] is None


def test_duplicate_pregame_side_and_reused_receipts_hold_conflicted_rows():
    a, b = pick(), pick('NBA', 'b')
    b['quote']['receipt_id'] = a['quote']['receipt_id']
    r = audit([event(), event('NBA', 'b')], [a, b], [final(), final('NBA', 'b')])
    assert r['card_complete'] is False
    assert all('QUOTE_RECEIPT_REUSED' in item['blockers'] for item in r['rows'])
    r2 = audit([event()], [pick(), pick(side='AWAY')])
    assert r2['card_complete'] is False
    assert 'DUPLICATE_PREGAME_PICK' in r2['rows'][0]['blockers']


@pytest.mark.parametrize('field,bad', [('away_team','Other'), ('official_event_id','other'),
                                      ('market_type','MONEYLINE'), ('settlement_basis','REGULATION'),
                                      ('signed_home_line',-2.5)])
def test_wrong_quote_identity_or_exact_line_is_held(field, bad):
    p = pick()
    p['quote'][field] = bad
    r = audit([event()], [p])
    assert r['card_complete'] is False
    assert r['rows'][0]['pregame_status'] == 'HELD'
    assert r['rows'][0]['model_probability'] is None
    assert 'EXACT_QUOTE_IDENTITY_OR_LINE_MISMATCH' in r['rows'][0]['blockers']


@pytest.mark.parametrize('time', ['2026-11-12T20:59:59Z', '2026-11-12T22:01:00Z',
                                  '2026-11-12T21:45:00'])
def test_old_future_or_naive_quote_is_held(time):
    p = pick()
    p['quote']['observed_at_utc'] = time
    r = audit([event()], [p])
    assert r['card_complete'] is False
    assert 'EXACT_QUOTE_NOT_FRESH_PREGAME' in r['rows'][0]['blockers']


def test_postkickoff_pick_or_hindsight_receipt_is_held():
    p = pick(frozen_at_utc='2026-11-13T00:00:00Z')
    assert 'PREGAME_FREEZE_INVALID' in audit([event()], [p])['rows'][0]['blockers']
    p = pick(available_to_user_at_utc='2026-11-12T22:01:00Z')
    assert 'PREGAME_VISIBILITY_INVALID' in audit([event()], [p])['rows'][0]['blockers']


def test_correct_overtime_nhl_shootout_mlb_extras_and_integer_push_grade():
    slate = [event('NBA', 'b'), event('NHL', 'h'), event('MLB', 'm'), event('NFL', 'f')]
    picks = [pick('NBA','b',line=-1.5), pick('NHL','h',line=-1.5),
             pick('MLB','m',line=1.5,side='AWAY'), pick('NFL','f',line=-3.0)]
    finals = [final('NBA','b',hs=124,aws=120), final('NHL','h',hs=3,aws=2),
              final('MLB','m',hs=2,aws=3), final('NFL','f',hs=20,aws=17)]
    r = audit(slate,picks,finals)
    assert r['card_complete'] is True
    assert r['settlement_complete'] is True
    assert [x['grade_status'] for x in r['rows']] == ['WIN','LOSS','LOSS','PUSH']
    assert r['push_count'] == 1
    assert r['headline_full_win_rate'] == pytest.approx(1/3)
    assert r['market_edge_established'] is False


def test_soccer_quarter_handicap_half_win_half_loss_and_no_draw_invention():
    for line, expected in [(0.25,'HALF_WIN'),(-0.25,'HALF_LOSS'), (0,'PUSH'),
                           (0.5,'WIN'),(-0.5,'LOSS')]:
        r = audit([event('SOCCER')], [pick('SOCCER', line=line)], [final('SOCCER',hs=1,aws=1)])
        assert r['card_complete'] is True
        assert r['rows'][0]['grade_status'] == expected
        assert r['rows'][0]['settlement_basis'] == 'REGULATION'
        if expected.startswith('HALF'):
            assert r['headline_full_win_rate'] is None


def test_tennis_games_vs_sets_are_separate_and_retirement_held():
    match = event('TENNIS', market_type='GAME_HANDICAP')
    p = pick('TENNIS',line=-2.5)
    f = final('TENNIS',hs=2,aws=1,home_games=18,away_games=17)
    assert audit([match], [p], [f])['rows'][0]['grade_status'] == 'LOSS'
    set_slate=event('TENNIS',market_type='SET_HANDICAP')
    set_pick=pick('TENNIS',line=-0.5,market_type='SET_HANDICAP')
    set_pick['quote']['market_type']='SET_HANDICAP'
    assert audit([set_slate],[set_pick],[final('TENNIS',hs=2,aws=1,market_type='SET_HANDICAP',home_sets=2,away_sets=1)])['rows'][0]['grade_status']=='WIN'
    retired=final('TENNIS',final_status='RETIRED')
    held=audit([match],[p],[retired]);assert held['rows'][0]['grade_status']=='HELD'
    assert 'TENNIS_RETIREMENT_RULE_UNVERIFIED' in held['rows'][0]['blockers']


@pytest.mark.parametrize('line', [float('inf'),float('nan'),True,10**1000,0.3])
def test_nonfinite_overflow_bool_or_invalid_tick_is_held(line):
    p=pick(line=line);r=audit([event()],[p])
    assert r['card_complete'] is False
    assert 'EXACT_LINE_INVALID' in r['rows'][0]['blockers']


def test_unsupported_sport_still_visible_and_no_fabricated_coverage():
    r = audit([event('GOLF','g')], [pick('GOLF','g')])
    assert r['scheduled_count'] == 1 and len(r['rows']) == 1
    assert r['card_complete'] is False
    assert 'SPORT_MARKET_FORMAT_UNSUPPORTED' in r['rows'][0]['blockers']


def test_missing_independent_final_no_partial_headline():
    r = audit([event(), event('NHL','h')],
              [pick(),pick('NHL','h')],[final()])
    assert r['card_complete'] is True
    assert r['settlement_complete'] is False
    assert r['pending_count'] == 1
    assert r['headline_full_win_rate'] is None
    assert r['rows'][1]['grade_status'] == 'PENDING'


def test_postponement_visible_not_in_playable_denominator():
    r=audit([event(),event('NBA','post',event_state='POSTPONED')],[pick()],[final()])
    assert r['scheduled_count']==2 and r['playable_count']==1
    assert r['rows'][1]['grade_status']=='NOT_PLAYABLE'
    assert r['card_complete'] is True
    assert r['settlement_complete'] is True


def test_pregame_pick_for_unknown_slate_event_is_visible_and_ungraded():
    extra = pick('NBA', 'unknown')
    r = audit([event()], [pick(), extra], [final()])
    assert r['card_complete'] is False
    assert 'PICK_NOT_IN_OFFICIAL_SLATE' in r['board_blockers']
    assert r['unmatched_pick_count'] == 1
    unknown = [x for x in r['rows'] if x['official_event_id'] == 'unknown']
    assert len(unknown) == 1
    assert unknown[0]['grade_status'] == 'HELD'
    assert unknown[0]['in_official_slate'] is False
    assert unknown[0]['model_probability'] is None
    assert r['playable_count'] == 1


def test_official_final_winner_conflicting_with_score_is_held():
    r = audit([event()], [pick()], [final(hs=30, aws=20, winning_side='AWAY')])
    assert r['card_complete'] is True
    assert r['settlement_complete'] is False
    assert r['rows'][0]['grade_status'] == 'HELD'
    assert 'OFFICIAL_SCORE_WINNER_CONFLICT' in r['rows'][0]['blockers']


def test_away_soccer_quarter_line_correct_half_settlement():
    for line, expected in [(0.25,'HALF_LOSS'), (-0.25,'HALF_WIN')]:
        r = audit([event('SOCCER')], [pick('SOCCER',side='AWAY',line=line)],
                  [final('SOCCER',hs=2,aws=2)])
        assert r['rows'][0]['grade_status'] == expected
        assert r['rows'][0]['selected_signed_line'] == -line


def test_tennis_match_winner_may_differ_from_game_handicap_units():
    # Match winner is defined by sets, not total games: a 2-1 sets winner
    # can win fewer individual games than the opponent.
    p = pick('TENNIS', line=4.5, side='HOME')
    official = final('TENNIS', home_games=15, away_games=20,
                     home_sets=2, away_sets=1, winning_side='HOME')
    report = audit([event('TENNIS')], [p], [official])
    assert report['card_complete'] is True
    assert report['rows'][0]['grade_status'] == 'LOSS'
    assert report['settlement_complete'] is True


def test_tennis_deciding_match_tiebreak_requires_handicap_rule_proof():
    p = pick('TENNIS', line=-1.5)
    f = final('TENNIS', home_games=12, away_games=9,
              deciding_match_tiebreak=True, provider_handicap_rule_verified=False)
    r = audit([event('TENNIS')], [p], [f])
    assert r['rows'][0]['grade_status'] == 'HELD'
    assert 'TENNIS_MATCH_TIEBREAK_SETTLEMENT_UNVERIFIED' in r['rows'][0]['blockers']
    assert r['settlement_complete'] is False


def test_mlb_shortened_game_requires_independent_runline_rule():
    p=pick('MLB',line=-1.5)
    f=final('MLB',hs=4,aws=2,shortened_game=True)
    r=audit([event('MLB')],[p],[f])
    assert r['rows'][0]['grade_status']=='HELD'
    assert 'SHORTENED_GAME_SETTLEMENT_UNVERIFIED' in r['rows'][0]['blockers']
    f['provider_settlement_rule_verified']=True
    r=audit([event('MLB')],[p],[f])
    assert r['rows'][0]['grade_status']=='WIN'


@pytest.mark.parametrize('winner', ['HOME', 'BANANA'])
def test_tennis_claimed_match_winner_without_sets_cannot_grade_games(winner):
    p = pick('TENNIS', line=-1.5)
    f = final('TENNIS', home_games=15, away_games=11, winning_side=winner)
    r = audit([event('TENNIS')], [p], [f])
    assert r['rows'][0]['grade_status'] == 'HELD'
    assert 'OFFICIAL_TENNIS_WINNER_SETS_UNVERIFIED' in r['rows'][0]['blockers']
    assert r['settlement_complete'] is False


def test_duplicate_postponement_does_not_allow_another_game_to_publish_complete_card():
    valid = event()
    postponed = event('NBA', 'post', event_state='POSTPONED')
    r = audit([valid, postponed, deepcopy(postponed)], [pick()], [final()])
    assert r['playable_count'] == 1
    assert r['card_complete'] is False
    assert r['settlement_complete'] is False
    assert 'OFFICIAL_SLATE_MANIFEST_INVALID' in r['board_blockers']
    assert all('DUPLICATE_OFFICIAL_EVENT' in x['blockers'] for x in r['rows'][1:])
    assert r['headline_full_win_rate'] is None


@pytest.mark.parametrize('source', ['', 'FIXTURE_OTHER_PROVIDER'])
def test_quote_source_must_match_frozen_pick_source(source):
    p = pick()
    p['quote_source_id'] = 'FIXTURE_SOURCE_A'
    p['quote']['quote_source_id'] = source
    r = audit([event()], [p])
    assert r['rows'][0]['pregame_status'] == 'HELD'
    assert 'EXACT_QUOTE_SOURCE_MISMATCH' in r['rows'][0]['blockers']


def test_missing_quote_source_on_pick_and_quote_is_not_authenticated():
    p = pick()
    p.pop('quote_source_id')
    p['quote'].pop('quote_source_id')
    r = audit([event()], [p])
    assert r['rows'][0]['pregame_status'] == 'HELD'
    assert 'EXACT_QUOTE_SOURCE_MISMATCH' in r['rows'][0]['blockers']


def test_quote_first_visible_after_immutable_pick_is_rejected():
    p = pick(available_to_user_at_utc='2026-11-12T21:00:00Z')
    r = audit([event()], [p])
    assert r['card_complete'] is False
    assert 'EXACT_QUOTE_NOT_VISIBLE_AT_PICK_PUBLICATION' in r['rows'][0]['blockers']


def test_reused_pregame_receipt_across_different_games_blocks_both():
    a, b = pick(), pick('NBA', 'b')
    b['pregame_receipt_id'] = a['pregame_receipt_id']
    r = audit([event(), event('NBA', 'b')], [a, b])
    assert r['card_complete'] is False
    assert all('PREGAME_RECEIPT_UNVERIFIED_OR_REUSED' in x['blockers'] for x in r['rows'])


@pytest.mark.parametrize('flag,bad', [
    ('deciding_match_tiebreak', 1), ('deciding_match_tiebreak', 'true'),
    ('deciding_match_tiebreak', None),
])
def test_invalid_tennis_exception_flag_is_held(flag, bad):
    f = final('TENNIS', home_games=14, away_games=10, **{flag: bad})
    r = audit([event('TENNIS')], [pick('TENNIS', line=-1.5)], [f])
    assert r['rows'][0]['grade_status'] == 'HELD'
    assert 'EXCEPTION_FORMAT_FLAG_INVALID' in r['rows'][0]['blockers']


@pytest.mark.parametrize('bad', [1, 'true', None])
def test_invalid_mlb_shortened_flag_is_held(bad):
    f = final('MLB', hs=5, aws=2, shortened_game=bad)
    r = audit([event('MLB')], [pick('MLB', line=-1.5)], [f])
    assert r['rows'][0]['grade_status'] == 'HELD'
    assert 'EXCEPTION_FORMAT_FLAG_INVALID' in r['rows'][0]['blockers']
