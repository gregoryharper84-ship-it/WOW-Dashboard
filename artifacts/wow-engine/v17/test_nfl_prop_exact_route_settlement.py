from datetime import datetime, timedelta, timezone

import v17.wnba_composite_settlement_overlay  # noqa: F401 - production overlay order
import v17.nfl_prop_settlement_overlay as nfl_overlay
import v17.prop_exact_route_settlement as settlement


NOW = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)
EVENT_START = NOW - timedelta(hours=4)
ESPN_EVENT_ID = "401872948"
CANONICAL_GAME_ID = "2026_04_ARI_SF"


class _Response:
    def __init__(self, *, payload=None, text="", status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = text.encode("utf-8")
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _scoreboard(*, final=True):
    return {
        "events": [
            {
                "id": ESPN_EVENT_ID,
                "status": {
                    "type": {
                        "completed": final,
                        "state": "post" if final else "in",
                    }
                },
            }
        ]
    }


def _csv(*, player="Test Player", game_id=CANONICAL_GAME_ID):
    return (
        "player_display_name,game_id,season,week,passing_yards,rushing_yards,"
        "receiving_yards,rushing_tds,receiving_tds,special_teams_tds\n"
        f"{player},{game_id},2026,4,287,12,64,1,1,0\n"
    )


def _snapshot():
    return {
        "role_status": {
            "player": "Test Player",
            "event_id": ESPN_EVENT_ID,
            "verified_canonical_event_id": CANONICAL_GAME_ID,
            "provider_season": 2026,
            "provider_week": 4,
            "provider_home_team": "SF",
            "provider_away_team": "ARI",
        }
    }


def _prediction(stat_type, *, line=100.5, direction="MORE"):
    return {
        "prediction_id": "00000000-0000-0000-0000-000000000001",
        "event_id": CANONICAL_GAME_ID,
        "event_start_time": EVENT_START.isoformat(),
        "player": "Test Player",
        "sport": "NFL",
        "stat_type": stat_type,
        "line": line,
        "direction": direction,
        "primary_failure_path": "TEST_FAILURE_PATH",
    }


def _get_factory(*, final=True, csv_text=None, calls=None):
    def get(url, **_kwargs):
        if calls is not None:
            calls.append(url)
        if "scoreboard" in url:
            return _Response(payload=_scoreboard(final=final))
        return _Response(text=csv_text if csv_text is not None else _csv())
    return get


def test_inventory_marks_only_fitted_nfl_routes_settlement_ready():
    rows = settlement.build_settlement_inventory()
    by_key = {(row["sport"], row["stat_type"]): row for row in rows}

    for key in nfl_overlay.NFL_SUPPORTED:
        row = by_key[key]
        assert row["status"] == settlement.SETTLEMENT_READY
        assert row["official_source"] == nfl_overlay.NFL_SETTLEMENT_SOURCE
        assert row["blocker"] is None
        assert row["can_execute"] is False

    if ("NFL", "COMPLETIONS") in by_key:
        unsupported = by_key[("NFL", "COMPLETIONS")]
        assert unsupported["status"] == settlement.EXACT_SETTLEMENT_ADAPTER_REQUIRED
        assert unsupported["blocker"] == "CERTIFIED_OFFICIAL_OUTCOME_ADAPTER_NOT_WIRED"


def test_nfl_exact_fitted_routes_extract_canonical_postgame_stats():
    cases = {
        "PASSING_YARDS": (287.0, 250.5),
        "RUSHING_YARDS": (12.0, 10.5),
        "RECEIVING_YARDS": (64.0, 60.5),
        # ANYTIME_TD is the fitted Bernoulli target: two touchdowns still settle
        # the modeled route as 1.0 (scored at least once), not as a count of 2.
        "ANYTIME_TD": (1.0, 0.5),
    }
    for stat_type, (expected, line) in cases.items():
        result = nfl_overlay.settle_nfl_scalar(
            _prediction(stat_type, line=line),
            _snapshot(),
            http_get=_get_factory(),
            now=NOW,
        )
        assert result["status"] == "SETTLED"
        assert result["outcome"]["actual_stat"] == expected
        assert result["outcome"]["official_result"] == "HIT"
        assert result["outcome"]["void"] is False
        assert result["can_execute"] is False
        assert "sha256=" in result["outcome"]["settlement_source"]
        assert f"game_id={CANONICAL_GAME_ID}" in result["outcome"]["settlement_source"]

    td = nfl_overlay.settle_nfl_scalar(
        _prediction("ANYTIME_TD", line=0.5),
        _snapshot(),
        http_get=_get_factory(),
        now=NOW,
    )
    assert td["settlement_components"] == {
        "rushing_tds": 1.0,
        "receiving_tds": 1.0,
        "special_teams_tds": 0.0,
    }
    assert td["outcome"]["actual_stat"] == 1.0
    assert td["touchdown_settlement_method"] == (
        "BERNOULLI_ANY_TOUCHDOWN_FROM_CANONICAL_TD_COMPONENTS"
    )


def test_nfl_does_not_settle_until_exact_espn_event_is_final():
    calls = []
    result = nfl_overlay.settle_nfl_scalar(
        _prediction("PASSING_YARDS"),
        _snapshot(),
        http_get=_get_factory(final=False, calls=calls),
        now=NOW,
    )
    assert result["status"] == "NOT_FINAL"
    assert "outcome" not in result
    assert all("stats_player_week" not in url for url in calls)
    assert result["can_execute"] is False


def test_nfl_missing_canonical_game_row_holds_instead_of_inventing_result():
    result = nfl_overlay.settle_nfl_scalar(
        _prediction("PASSING_YARDS"),
        _snapshot(),
        http_get=_get_factory(csv_text=_csv(game_id="2026_04_OTHER_GAME")),
        now=NOW,
    )
    assert result["status"] == "OFFICIAL_STAT_SOURCE_NOT_YET_UPDATED"
    assert result["blocker"] == "NFL_CANONICAL_GAME_ROW_MISSING"
    assert "outcome" not in result
    assert result["can_execute"] is False


def test_nfl_missing_player_row_holds_instead_of_zero_or_dnp_void():
    result = nfl_overlay.settle_nfl_scalar(
        _prediction("RECEIVING_YARDS", line=0.5),
        _snapshot(),
        http_get=_get_factory(csv_text=_csv(player="Different Player")),
        now=NOW,
    )
    assert result["status"] == "OFFICIAL_PLAYER_STAT_ROW_MISSING"
    assert result["blocker"] == "NFL_CANONICAL_PLAYER_GAME_ROW_REQUIRED"
    assert "outcome" not in result
    assert result["can_execute"] is False


def test_overlay_preserves_governance_flags_and_support_registration():
    assert nfl_overlay.NFL_SUPPORTED.issubset(settlement.SUPPORTED_SETTLEMENT_ROUTES)
    assert settlement.settle_nfl_scalar is nfl_overlay.settle_nfl_scalar
    assert settlement.run_exact_route_settlement is nfl_overlay.run_exact_route_settlement
