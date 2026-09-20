import prop_fitted_provider, prop_discrete_engine, prop_model_adapters, prop_calibration_adapters
import pick_request_runtime_core as runtime
from prop_auto_hydration_router import provider_for_sport
from v17.prop_route_lifecycle import hydration_route_registered
def test_aliases():
    assert runtime._canonical_stat('NFL','Pass Yards')=='PASSING_YARDS'
    assert runtime._canonical_stat('NFL','Rush Yards')=='RUSHING_YARDS'
    assert runtime._canonical_stat('NFL','Receiving Yards')=='RECEIVING_YARDS'
    assert runtime._canonical_stat('NFL','Anytime TDs')=='ANYTIME_TD'
def test_registration():
    prop_fitted_provider.clear_model_family_adapters(); getattr(prop_discrete_engine,'_CALIBRATION_ADAPTERS').clear()
    prop_model_adapters.register(); prop_calibration_adapters.register()
    assert 'NFL_PROP_ROLLING_FITTED_V1' in prop_fitted_provider._ADAPTERS
    assert 'NFL_PROP_PRECALIBRATION_BOOTSTRAP_V1' in prop_discrete_engine._CALIBRATION_ADAPTERS
def test_hydration_registration():
    assert provider_for_sport('NFL','PASSING_YARDS')=='NFL_ESPN_IDENTITY_NFLVERSE_STATS_V1'
    for stat in ('PASSING_YARDS','RUSHING_YARDS','RECEIVING_YARDS','ANYTIME_TD'): assert hydration_route_registered('NFL',stat)
    assert not hydration_route_registered('NFL','TACKLES')
