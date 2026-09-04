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


def _get(event_start):
    def fake_get(url, *, params, timeout):
        if url.endswith('/people/search'):
            return _Response({'people': [{'id': 123, 'fullName': 'Test Pitcher'}]})
        if url.endswith('/schedule'):
            return _Response({'dates': [{'games': [{
                'gamePk': 999,
                'gameDate': event_start.isoformat(),
                'teams': {
                    'home': {'team': {'abbreviation': 'HOM'}, 'probablePitcher': {'id': 123, 'fullName': 'Test Pitcher'}},
                    'away': {'team': {'abbreviation': 'AWY'}},
                },
                'venue': {'name': 'Park'},
                'status': {'detailedState': 'Scheduled'},
            }]}]})
        if url.endswith('/people/123/stats'):
            splits = []
            for i in range(12):
                splits.append({
                    'date': (event_start.date() - timedelta(days=i + 1)).isoformat(),
                    'opponent': {'abbreviation': f'O{i}'},
                    'stat': {
                        'gamesStarted': 1,
                        'strikeOuts': 5 + (i % 3),
                        'inningsPitched': '6.0',
                        'numberOfPitches': 90 + i,
                        'strikes': 60 + i,
                        'battersFaced': 24,
                        'baseOnBalls': 2,
                        'earnedRuns': 2,
                    },
                })
            return _Response({'stats': [{'splits': splits}]})
        raise AssertionError(url)
    return fake_get


@pytest.mark.parametrize(
    'stat_type, expected_first',
    [
        ('PITCHER_STRIKEOUTS', 5.0),
        ('PITCHING_OUTS', 18.0),
        ('STRIKES_THROWN', 60.0),
        ('BALLS_THROWN', 30.0),
    ],
)
def test_certified_mlb_pitcher_stats_share_official_prior_start_hydration(stat_type, expected_first):
    now = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    event_start = now + timedelta(hours=6)
    evidence = hydration.auto_hydrate_prop_evidence(
        sport='MLB',
        player='Test Pitcher',
        stat_type=stat_type,
        event_start_time=event_start.isoformat(),
        http_get=_get(event_start),
        now=now,
        source_capture_timestamp=(now - timedelta(minutes=1)).isoformat(),
        source_label='PDF:PRIZEPICKS',
    )
    assert len(evidence['game_log']) == 10
    assert evidence['game_log'][0] == expected_first
    assert evidence['box_score_log'][0]['outs'] == 18
    assert evidence['box_score_log'][0]['pitches'] == 90
    assert evidence['box_score_log'][0]['strikes'] == 60
    assert evidence['opportunity_ledger']['target_stat_type'] == stat_type
    assert evidence['role_status']['role'] == 'STARTING_PITCHER'
    assert evidence['rate_provenance'].endswith(f'{stat_type}:OFFICIAL_PRIOR_STARTS')


def test_pitch_composition_does_not_invent_missing_pitch_counts():
    now = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    event_start = now + timedelta(hours=6)

    def fake_get(url, *, params, timeout):
        if url.endswith('/people/search'):
            return _Response({'people': [{'id': 123, 'fullName': 'Test Pitcher'}]})
        if url.endswith('/schedule'):
            return _get(event_start)(url, params=params, timeout=timeout)
        if url.endswith('/people/123/stats'):
            splits = []
            for i in range(12):
                splits.append({'date': (event_start.date() - timedelta(days=i + 1)).isoformat(), 'stat': {
                    'gamesStarted': 1, 'strikeOuts': 5, 'inningsPitched': '6.0'
                }})
            return _Response({'stats': [{'splits': splits}]})
        raise AssertionError(url)

    with pytest.raises(hydration.PropAutoHydrationError) as exc:
        hydration.auto_hydrate_prop_evidence(
            sport='MLB', player='Test Pitcher', stat_type='STRIKES_THROWN',
            event_start_time=event_start.isoformat(), http_get=fake_get, now=now,
        )
    assert exc.value.code == 'MLB_PITCH_COMPOSITION_INSUFFICIENT'
    assert exc.value.detail['starts_found'] == 0
