from types import SimpleNamespace
import pytest
from kalshi_weather_v2.free_forecast_acquisition import acquire_hourly_forecasts
from kalshi_weather_v2.http_client import HttpAcquisitionError
from kalshi_weather_v2.test_hourly_forecast_fusion import contract
from kalshi_weather_v2.hourly_forecast_fusion import build_hourly_weather_evidence

def getter(calls, fail_secondary=False):
    def get(url, headers=None):
        calls.append(url)
        if '/points/' in url:
            return {'properties': {'forecastHourly': 'https://api.weather.gov/gridpoints/OKX/33,37/forecast/hourly'}}
        if 'api.weather.gov' in url:
            return {'properties': {'generatedAt': '2026-09-10T15:55:00Z', 'periods': [{'startTime': '2026-09-10T17:00:00Z', 'temperature': 82, 'temperatureUnit': 'F'}]}}
        if fail_secondary:
            raise HttpAcquisitionError('HTTP_RETRY_EXHAUSTED', url, 'http_status=429')
        return {'timezone':'GMT', 'utc_offset_seconds':0, 'hourly_units':{'temperature_2m_gfs_seamless':'°F'}, 'hourly':{'time':['2026-09-10T17:00'], 'temperature_2m_gfs_seamless':[81]}}
    return get

def acquire(get, secondary_enabled=False):
    return acquire_hourly_forecasts(get, latitude=40.7, longitude=-74, target_date='2026-09-10', models=('gfs_seamless',), retrieved_at='2026-09-10T16:10:00Z', secondary_enabled=secondary_enabled)

def test_default_uses_only_free_official_forecast():
    calls=[]
    result=acquire(getter(calls))
    assert len(calls)==2
    assert all(url.startswith('https://api.weather.gov/') for url in calls)
    assert result.secondary is None
    evidence=build_hourly_weather_evidence(contract=contract(), analysis_time='2026-09-10T16:10:00Z', nws_snapshot=result.nws, open_meteo_snapshot=result.secondary, settlement_source_verified=True, settlement_location_verified=True)
    assert evidence.central_estimate==82
    assert not evidence.evidence_complete
    assert evidence.providers==('NWS',)
    assert result.warnings==('SECONDARY_FORECAST_DISABLED',)

def test_rate_limit_preserves_nws_evidence():
    result=acquire(getter([], True), True)
    assert result.nws.provider=='NWS'
    assert result.secondary is None
    assert result.warnings==('SECONDARY_FORECAST_UNAVAILABLE:HTTP_RETRY_EXHAUSTED',)

def test_enabled_secondary_retains_existing_fusion():
    result=acquire(getter([]), True)
    assert result.secondary.provider=='OPEN_METEO'
    assert result.warnings==()

def test_primary_failure_is_not_filled_with_secondary():
    def fail(url, headers=None):
        raise HttpAcquisitionError('HTTP_RETRY_EXHAUSTED',url,'http_status=503')
    with pytest.raises(HttpAcquisitionError):
        acquire(fail, True)

def test_untrusted_forecast_url_is_rejected_before_fetch():
    calls=[]
    def get(url, headers=None):
        calls.append(url)
        return {'properties':{'forecastHourly':'https://api.weather.gov.evil.test/forecast'}}
    with pytest.raises(ValueError, match='NWS_FORECAST_URL_INVALID'):
        acquire(get)
    assert len(calls)==1

@pytest.mark.parametrize('secondary_enabled', [False, True])
def test_runtime_persists_primary_only_capture_without_publication(monkeypatch, secondary_enabled):
    import kalshi_weather_v2.runtime as runtime
    saved = {'sources': [], 'predictions': []}
    class Persistence:
        def __init__(self, client): pass
        def persist_rule_package(self, rules): pass
        def persist_source_snapshot(self, **kwargs): saved['sources'].append(kwargs['snapshot'].provider)
        def persist_settlement_twin(self, twin): pass
        def load_latest_certified_calibration(self, **kwargs):
            raise AssertionError('incomplete evidence must not reuse calibration')
        def load_runtime_capability(self): return {'capability_status': 'AVAILABLE'}
        def persist_prediction(self, **kwargs): saved['predictions'].append(kwargs)
    monkeypatch.setattr(runtime, 'KalshiWeatherPersistence', Persistence)
    monkeypatch.setattr(runtime, 'build_atomic_temperature_twin', lambda **kwargs: object())
    monkeypatch.setenv('WOW_KALSHI_WEATHER_OPEN_METEO_ENABLED', '1' if secondary_enabled else '0')
    rules=SimpleNamespace(market_rules=SimpleNamespace(ticker='KXTEMPNYCHS-TEST', rule_snapshot_id='rule-1'), settlement_source=SimpleNamespace(url='https://example.invalid/settlement'))
    parsed=SimpleNamespace(ticker='KXTEMPNYCHS-TEST', index_city='nyc', rule_snapshot_id='rule-1', observation_time_utc='2026-09-10T17:00:00Z', to_contract_snapshot=lambda _: contract())
    calls=[]
    result=runtime.capture_hourly_shadow(client=object(), ticker='KXTEMPNYCHS-TEST', index_city='nyc', expected_location='New York City', forecast_latitude=40.7, forecast_longitude=-74, decision_time='2026-09-10T16:10:00Z', preacquired_rules=rules, preparsed_rule=parsed, http=SimpleNamespace(get_json=getter(calls, True)))
    assert result['status']=='SHADOW_CAPTURED'
    assert result['p_yes'] is None and result['p_no'] is None
    assert not result['probability_publishable'] and not result['can_execute']
    assert saved['sources']==['NWS', 'NWS']
    assert len(saved['predictions'])==1
    assert saved['predictions'][0]['calibration_profile_id'] is None
    assert not saved['predictions'][0]['evidence'].evidence_complete
