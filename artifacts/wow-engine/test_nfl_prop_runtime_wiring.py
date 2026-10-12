import pick_request_runtime_core as runtime
import prop_calibration_adapters
import prop_discrete_engine
import prop_fitted_provider
import prop_model_adapters
from prop_auto_hydration_router import provider_for_sport
from v17.prop_route_lifecycle import hydration_route_registered


def test_aliases():
    assert runtime._canonical_stat("NFL", "Pass Yards") == "PASSING_YARDS"
    assert runtime._canonical_stat("NFL", "Rush Yards") == "RUSHING_YARDS"
    assert runtime._canonical_stat("NFL", "Receiving Yards") == "RECEIVING_YARDS"
    assert runtime._canonical_stat("NFL", "Anytime TDs") == "ANYTIME_TD"


def test_autonomous_nfl_player_yardage_labels_normalize_only_stat_identity():
    """Scout PLAYER_ labels must reach their existing fitted-route preflight."""
    expected = {
        "PLAYER_PASSING_YARDS": "PASSING_YARDS",
        "PLAYER_RUSHING_YARDS": "RUSHING_YARDS",
        "PLAYER_RECEIVING_YARDS": "RECEIVING_YARDS",
    }
    for discovery_stat, canonical_stat in expected.items():
        assert runtime._canonical_stat("NFL", discovery_stat) == canonical_stat
        assert runtime._canonical_stat("NFL", discovery_stat.lower().replace("_", " ")) == canonical_stat
        # Normalization alone cannot promote an unregistered sport/stat model.
        assert provider_for_sport("NFL", canonical_stat) is not None

    assert runtime._canonical_stat("NFL", "PLAYER_RECEPTIONS") == "PLAYER_RECEPTIONS"
    assert runtime._canonical_stat("NBA", "PLAYER_RECEIVING_YARDS") == "PLAYER_RECEIVING_YARDS"


def test_registration():
    prop_fitted_provider.clear_model_family_adapters()
    getattr(prop_discrete_engine, "_CALIBRATION_ADAPTERS").clear()
    prop_model_adapters.register()
    prop_calibration_adapters.register()
    assert "NFL_PROP_ROLLING_FITTED_V1" in prop_fitted_provider._ADAPTERS
    assert "NFL_PROP_PRECALIBRATION_BOOTSTRAP_V1" in prop_discrete_engine._CALIBRATION_ADAPTERS


def test_hydration_registration():
    assert provider_for_sport("NFL", "PASSING_YARDS") == "NFL_ESPN_IDENTITY_NFLVERSE_STATS_V1"
    for stat in ("PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS", "ANYTIME_TD"):
        assert hydration_route_registered("NFL", stat)
    assert not hydration_route_registered("NFL", "TACKLES")
