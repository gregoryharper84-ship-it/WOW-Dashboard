"""Strict, source-verbatim NBA October 26 recovery and negative route tests."""
from __future__ import annotations
import pytest
from v17.nba_offer_identity_v17 import NBAOfferIdentityError, parse_nba_offer, reconcile_nba_offer_labels


# Exact historical audit, not proof these were live platform offers or NBA official IDs.
MARKETS = [
    ("Davion Mitchell", "DAL @ MIA", "7+ assists"),
    ("Davion Mitchell", "DAL @ MIA", "8+ assists"),
    ("Davion Mitchell", "DAL @ MIA", "10+ points"),
    ("Jaden McDaniels", "MIN @ CLE", "5+ rebounds"),
    ("Jaden McDaniels", "MIN @ CLE", "6+ rebounds"),
    ("Jaden McDaniels", "MIN @ CLE", "OVER 13.5 points"),
    ("Devin Booker", "PHX @ OKC", "5+ rebounds"),
    ("Devin Booker", "PHX @ OKC", "5+ assists"),
    ("Lauri Markkanen", "MEM @ UTA", "OVER 6.5 rebounds"),
    ("Lauri Markkanen", "MEM @ UTA", "8+ rebounds"),
    ("Lauri Markkanen", "MEM @ UTA", "OVER 22.5 points"),
    ("Lauri Markkanen", "MEM @ UTA", "double-double"),
    ("Jaime Jaquez Jr.", "IND @ MIL", "OVER 5.5 rebounds"),
    ("Jaime Jaquez Jr.", "IND @ MIL", "7+ rebounds"),
    ("Jaime Jaquez Jr.", "IND @ MIL", "10+ points"),
    ("Jayson Tatum", "CHI @ BOS", "7+ assists"),
    ("Jayson Tatum", "CHI @ BOS", "7+ rebounds"),
    ("Jayson Tatum", "CHI @ BOS", "25+ points"),
    ("Tyrese Haliburton", "IND @ MIL", "15+ points"),
    ("Tyrese Haliburton", "IND @ MIL", "10+ assists"),
    ("Tyrese Haliburton", "IND @ MIL", "3+ rebounds"),
    ("Ty Jerome", "MEM @ UTA", "25+ points"),
    ("Ty Jerome", "MEM @ UTA", "5+ assists"),
    ("Ty Jerome", "MEM @ UTA", "3+ rebounds"),
    ("Alperen Sengun", "ATL @ HOU", "20+ points"),
    ("Alperen Sengun", "ATL @ HOU", "7+ rebounds"),
    ("Alperen Sengun", "ATL @ HOU", "5+ assists"),
]


def rows():
    return [dict(source_row=f"{i:02d}", player=p, matchup=m, original_label=label)
            for i,(p,m,label) in enumerate(MARKETS,1)]


def test_recovered_market_reconciliation_27_exact_once():
    result = reconcile_nba_offer_labels(rows())
    assert result["source_rows_reconciled"] == 27
    assert result["source_instances"] == 27
    assert result["offer_families"] == {"AT_LEAST": 22, "NUMERIC_OVER": 4, "COMPOUND": 1}
    assert len({(x["player"],x["matchup"],x["original_label"]) for x in result["rows"]}) == 27
    assert len({x["player"] for x in result["rows"]}) == 9
    assert len({x["matchup"] for x in result["rows"]}) == 7
    assert result["action_eligible_rows"] == result["score_action_invocations"] == 0
    assert result["probability_publishable"] is result["can_execute"] is False


def test_ladders_stay_distinct_and_are_not_numeric_lines():
    result = reconcile_nba_offer_labels(rows())
    first, second = result["rows"][:2]
    assert first["original_label"] == "7+ assists"
    assert second["original_label"] == "8+ assists"
    assert first["threshold"] == "7" and second["threshold"] == "8"
    assert first["settlement_predicate"] == "ASSISTS >= 7"
    assert first["explicit_direction"] is None
    assert first["host_disposition"] == "AT_LEAST_THRESHOLD_ADAPTER_NOT_VERIFIED"
    assert second["can_submit_score_action"] is False


def test_explicit_over_preserves_literal_half_line_and_only_more():
    obj = parse_nba_offer(source_row="06", player="Jaden McDaniels", matchup="MIN @ CLE", original_label="OVER 13.5 points")
    assert obj.threshold == "13.5" and obj.explicit_direction == "MORE"
    assert obj.settlement_predicate == "POINTS > 13.5"
    assert obj.can_submit_score_action is False


def test_double_double_not_scalar_and_no_default_direction():
    obj = parse_nba_offer(source_row="12", player="Lauri Markkanen", matchup="MEM @ UTA", original_label="double-double")
    assert obj.stat_type == "DOUBLE_DOUBLE" and obj.offer_family == "COMPOUND"
    assert obj.explicit_direction is None and obj.threshold is None
    assert obj.host_disposition == "COMPOUND_ROUTE_NOT_VERIFIED"


def test_preserves_capture_only_if_source_supplies_it():
    args = dict(source_row="01", player="Davion Mitchell", matchup="DAL @ MIA", original_label="7+ assists")
    assert parse_nba_offer(**args).source_capture_timestamp is None
    a = parse_nba_offer(**args, source_capture_timestamp="2026-10-26T12:00:00Z", platform="PRIZEPICKS")
    assert a.source_capture_timestamp == "2026-10-26T12:00:00Z"
    assert a.platform == "PRIZEPICKS"


@pytest.mark.parametrize("bad", ["7.0+ assists", "7+ fantasy points", "OVER 7+ assists", "UNDER 13.5 points", "double double", "7+ rebounds / 8+ points", "7+ Assists (LIVE)", "7 + assists", "0+ assists", "OVER -1 points", "OVER 20.5 fantasy score"])
def test_unsupported_offers_are_typed_holds(bad):
    with pytest.raises(NBAOfferIdentityError, match="NBA_OFFER_UNRECOGNIZED_LABEL"):
        parse_nba_offer(source_row="x", player="A", matchup="B", original_label=bad)


@pytest.mark.parametrize("row", [
    {"source_row": "", "player": "X", "matchup": "Y", "original_label": "7+ points"},
    {"source_row": "x", "player": "", "matchup": "Y", "original_label": "7+ points"},
    {"source_row": "x", "player": "X", "matchup": "", "original_label": "7+ points"},
    {"source_row": "x", "player": "X", "matchup": "Y", "original_label": ""},
])
def test_missing_source_fields_fail_closed(row):
    with pytest.raises(NBAOfferIdentityError):
        parse_nba_offer(**row)


def test_duplicate_source_key_not_silently_overwritten():
    assert rows()[0]["source_row"] == "01"
    with pytest.raises(NBAOfferIdentityError, match="NBA_OFFER_SOURCE_ROW_COLLISION"):
        reconcile_nba_offer_labels([rows()[0], rows()[0]])


def test_missing_source_timestamp_never_claims_fresh_or_available():
    result = reconcile_nba_offer_labels(rows())
    assert all(o["source_capture_timestamp"] is None for o in result["rows"])
    assert all(o["can_submit_score_action"] is False for o in result["rows"])


def test_no_implicit_market_side_in_compound_or_at_least():
    for x in reconcile_nba_offer_labels(rows())["rows"]:
        if x["offer_family"] != "NUMERIC_OVER":
            assert x["explicit_direction"] is None
            assert x["can_submit_score_action"] is False
