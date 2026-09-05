from datetime import datetime, timedelta, timezone

import pytest

import prop_auto_hydration as hydration


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _split(date_value, *, so=5, pitches=90, strikes=60):
    return {
        'date': date_value.isoformat(),
        'opponent': {'abbreviation': 'OPP'},
        'stat': {
            'gamesStarted': 1,
            'strikeOuts': so,
            'inningsPitched': '6.0',
            'numberOfPitches': pitches,
            'strikes': strikes,
            'battersFaced': 24,
            'baseOnBalls': 2,
            'earnedRuns': 2,
        },
    }


def test_event_probable_pitcher_id_resolves_duplicate_display_name_before_people_search():
    event_start = datetime(2026, 9, 5, 0, 10, tzinfo=timezone.utc)
    people_search_called = False

    def fake_get(url, *, params, timeout):
        nonlocal people_search_called
        if url.endswith('/schedule'):
            return _Response({'dates': [{'games': [{
                'gamePk': 111,
                'gameDate': event_start.isoformat(),
                'teams': {
                    'home': {'team': {'abbreviation': 'PIT'}, 'probablePitcher': {'id': 683003, 'fullName': 'Jared Jones'}},
                    'away': {'team': {'abbreviation': 'MIL'}},
                },
                'venue': {'name': 'PNC Park'},
                'status': {'detailedState': 'Scheduled'},
            }]}]})
        if url.endswith('/people/search'):
            people_search_called = True
            return _Response({'people': [
                {'id': 683003, 'fullName': 'Jared Jones'},
                {'id': 999999, 'fullName': 'Jared Jones'},
            ]})
        raise AssertionError(url)

    player_id, official_name = hydration._resolve_player_id(
        'Jared Jones', event_start=event_start, http_get=fake_get
    )
    assert player_id == 683003
    assert official_name == 'Jared Jones'
    assert people_search_called is False


def test_suffix_normalization_allows_event_identity_proof():
    event_start = datetime(2026, 9, 5, 0, 10, tzinfo=timezone.utc)

    def fake_get(url, *, params, timeout):
        if url.endswith('/schedule'):
            return _Response({'dates': [{'games': [{
                'gamePk': 222,
                'gameDate': event_start.isoformat(),
                'teams': {
                    'home': {'team': {'abbreviation': 'KC'}, 'probablePitcher': {'id': 663738, 'fullName': 'Daniel Lynch'}},
                    'away': {'team': {'abbreviation': 'DET'}},
                },
                'venue': {'name': 'Kauffman Stadium'},
                'status': {'detailedState': 'Scheduled'},
            }]}]})
        raise AssertionError(url)

    player_id, _ = hydration._resolve_player_id(
        'Daniel Lynch IV', event_start=event_start, http_get=fake_get
    )
    assert player_id == 663738


def _history_get(event_start, counts_by_season):
    def fake_get(url, *, params, timeout):
        if url.endswith('/schedule'):
            return _Response({'dates': [{'games': [{
                'gamePk': 333,
                'gameDate': event_start.isoformat(),
                'teams': {
                    'home': {'team': {'abbreviation': 'HOM'}, 'probablePitcher': {'id': 123, 'fullName': 'Test Pitcher'}},
                    'away': {'team': {'abbreviation': 'AWY'}},
                },
                'venue': {'name': 'Park'},
                'status': {'detailedState': 'Scheduled'},
            }]}]})
        if url.endswith('/people/123/stats'):
            season = int(params['season'])
            count = counts_by_season.get(season, 0)
            base = event_start.date() - timedelta(days=10 if season == event_start.year else 200 + (event_start.year - season) * 365)
            splits = [_split(base - timedelta(days=i * 5), so=4 + (i % 4)) for i in range(count)]
            return _Response({'stats': [{'splits': splits}]})
        if url.endswith('/people/123'):
            return _Response({'people': [{'pitchHand': {'code': 'R'}}]})
        if url.endswith('/game/333/boxscore'):
            return _Response({'teams': {'away': {'battingOrder': []}, 'home': {'battingOrder': []}}})
        if url.endswith('/people/search'):
            return _Response({'people': [{'id': 123, 'fullName': 'Test Pitcher'}]})
        raise AssertionError(url)
    return fake_get


@pytest.mark.parametrize('stat_type', ['PITCHER_STRIKEOUTS', 'PITCHING_OUTS', 'STRIKES_THROWN', 'BALLS_THROWN'])
def test_current_season_short_sample_uses_recent_official_prior_season_starts(stat_type):
    now = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    event_start = now + timedelta(hours=8)
    evidence = hydration.auto_hydrate_prop_evidence(
        sport='MLB',
        player='Test Pitcher',
        stat_type=stat_type,
        event_start_time=event_start.isoformat(),
        http_get=_history_get(event_start, {2026: 4, 2025: 8, 2024: 0}),
        now=now,
    )
    assert len(evidence['game_log']) == 10
    assert set(evidence['opportunity_ledger']['history_seasons_used']) == {2026, 2025}
    assert 'MLB_STATS_API_CROSS_SEASON_HISTORY' in evidence['source_timestamps']
    assert all('season' in row for row in evidence['box_score_log'])


def test_truly_thin_cross_season_history_still_fails_closed():
    now = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    event_start = now + timedelta(hours=8)
    with pytest.raises(hydration.PropAutoHydrationError) as exc:
        hydration.auto_hydrate_prop_evidence(
            sport='MLB',
            player='Test Pitcher',
            stat_type='PITCHER_STRIKEOUTS',
            event_start_time=event_start.isoformat(),
            http_get=_history_get(event_start, {2026: 4, 2025: 3, 2024: 2}),
            now=now,
        )
    assert exc.value.code == 'MLB_RECENT_STARTS_INSUFFICIENT'
    assert exc.value.detail['starts_found'] == 9
    assert exc.value.detail['seasons_queried'] == [2026, 2025, 2024]
