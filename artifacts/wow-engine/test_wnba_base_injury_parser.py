"""Exercise the base hydrator independently of the router's parser override."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def base():
    spec = importlib.util.spec_from_file_location(
        "wnba_base_parser_under_test", Path(__file__).with_name("wnba_prop_auto_hydration.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse(base, rows):
    return base._availability_from_report(
        f"AAA@BBB Alpha Aces {rows} Beta Belles Opponent Player Out",
        player_name="Test Player", team_name="Alpha Aces",
        opponent_name="Beta Belles", matchup="AAA@BBB", game_date="08/30/2026",
    )


@pytest.mark.parametrize("name", ["Test Player", "Player Test"])
@pytest.mark.parametrize("rows", [
    "{name} Available Returned To Competition Other Player Out Injury",
    "Other Player Out Injury {name} Available Returned To Competition",
])
def test_available_target_isolated_from_out_teammate(base, name, rows):
    assert parse(base, rows.format(name=name))["designation"] == "AVAILABLE"


@pytest.mark.parametrize("name", ["Test Player", "Player Test"])
def test_out_target_isolated_from_available_teammate(base, name):
    assert parse(base, f"{name} Out Injury Other Player Available")["availability"] == "UNAVAILABLE"


def test_absent_target_in_complete_section(base):
    assert parse(base, "Other Player Out Injury")["availability"] == "NOT_LISTED_ON_FRESH_OFFICIAL_INJURY_REPORT"


def test_missing_immediate_status_does_not_read_teammate_and_translates_error(base):
    with pytest.raises(base.WNBAPropHydrationError) as exc:
        parse(base, "Test Player Injury Other Player Available")
    assert exc.value.code == "WNBA_INJURY_REPORT_PLAYER_STATUS_UNRESOLVED"
    assert exc.value.detail == {"player": "Test Player", "team": "Alpha Aces"}
