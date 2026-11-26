"""Guard against cross-sport coverage silently becoming certification."""
import pytest

from v17.universal_sport_market_audit import (
    AUDIT_SCHEMA_VERSION, MARKET_APPLICABILITY, MARKETS, audit_summary,
    build_universal_sport_market_audit,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS


def test_all_13_sports_and_four_market_families_accounted_for():
    rows = build_universal_sport_market_audit()
    assert len(EXPECTED_TEAM_EVENT_SPORTS) == 13
    assert len(rows) == 13 * len(MARKETS)
    assert {(row.sport, row.market) for row in rows} == {
        (sport, market) for sport in EXPECTED_TEAM_EVENT_SPORTS for market in MARKETS
    }


def test_no_declaration_promotes_probability_or_allows_execution():
    for row in build_universal_sport_market_audit():
        assert not row.is_certified_market
        assert not row.production_verified
        assert not row.can_execute
        assert row.status in {"REQUIRES_EVENT_LEVEL_VERIFICATION", "NOT_APPLICABLE"}
        if row.market != "WINNER":
            assert row.declared_specialist is None
        if row.status != "NOT_APPLICABLE":
            assert "immutable_pregame_receipt" in row.missing_evidence
            assert "independent_acceptance" in row.missing_evidence


def test_summary_is_explicitly_non_authoritative():
    report = audit_summary()
    assert report["schema_version"] == AUDIT_SCHEMA_VERSION
    assert report["kind"] == "NON_AUTHORITATIVE_READ_ONLY_INVENTORY"
    assert report["row_count"] == 52
    assert report["certified_market_rows"] == 0
    assert report["production_verified_rows"] == 0
    assert report["can_execute"] is False


@pytest.mark.parametrize("sport,market", (
    ("NBA", "WINNER"), ("NBA", "HANDICAP"), ("NBA", "TOTAL"),
    ("NBA", "PLAYER_PROP"), ("MLB", "WINNER"),
))
def test_excluded_market_keeps_a_non_authoritative_inventory_row(monkeypatch, sport, market):
    baseline = audit_summary()
    if sport == "MLB":
        winner = next(row for row in baseline["rows"]
                      if (row["sport"], row["market"]) == ("MLB", "WINNER"))
        assert winner["declared_specialist"] == "MLB_GAME_WIN_PROBABILITY_EXPERT"
    # Exercise a structurally excluded market without changing the production
    # applicability declarations or treating a missing model as inapplicable.
    monkeypatch.setitem(
        MARKET_APPLICABILITY, sport,
        frozenset({"WINNER", "HANDICAP", "TOTAL", "PLAYER_PROP"} - {market}),
    )

    report = audit_summary()
    excluded = [row for row in report["rows"] if row["status"] == "NOT_APPLICABLE"]
    assert excluded == [{
        "sport": sport, "market": market, "status": "NOT_APPLICABLE",
        "declared_specialist": None, "required_inputs": (), "missing_evidence": (),
        "is_certified_market": False, "production_verified": False,
        "can_execute": False,
    }]
    assert report["row_count"] == len(report["rows"]) == 52
    assert report["certified_market_rows"] == 0
    assert report["production_verified_rows"] == 0
    assert report["can_execute"] is False
    assert [row for row in report["rows"] if row not in excluded] == [
        row for row in baseline["rows"]
        if (row["sport"], row["market"]) != (sport, market)
    ]
