import logging
import math
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from v17 import closing_line_grading as g
from v17 import rundown_market_history as history


def _close(event, team, odds, *, book="19", start="2026-10-05T23:08:00Z", kind="CLOSE", market="1", live=False, main=True):
    return {
        "provider_event_id": event, "event_start_utc": start, "market_id": market,
        "participant_name": team, "affiliate_id": book, "sportsbook": f"affiliate_{book}",
        "american_odds": odds, "snapshot_kind": kind, "is_live": live, "is_main_line": main,
        "price_updated_at": "2026-10-05T22:55:00Z", "fetched_at": "2026-10-05T23:00:00Z",
    }


def _pair(event, home, h_odds, away, a_odds, **kw):
    return [_close(event, home, h_odds, **kw), _close(event, away, a_odds, **kw)]


START = datetime(2026, 10, 5, 23, 8, tzinfo=timezone.utc)


# --- price math ----------------------------------------------------------------

def test_no_vig_pair_removes_margin_and_is_complementary():
    p = g.no_vig_pair(-150, 130)
    q = g.no_vig_pair(130, -150)
    assert p == pytest.approx(0.6 / (0.6 + 100 / 230))
    assert p + q == pytest.approx(1.0)


@pytest.mark.parametrize("odds", [None, "x", -50, 99, float("nan"), float("inf")])
def test_invalid_american_odds_are_rejected(odds):
    assert g.american_to_implied(odds) is None


# --- matching ------------------------------------------------------------------

def test_consensus_close_averages_books_with_both_sides():
    rows = _pair("e1", "Detroit Tigers", -150, "Seattle Mariners", 130, book="19") + _pair(
        "e1", "Detroit Tigers", -140, "Seattle Mariners", 120, book="3"
    )
    m = g.match_close(selected="Detroit Tigers", opponent="Seattle Mariners", event_start=START, close_rows=rows)
    assert m["status"] == g.LINKED
    assert m["books"] == ["19", "3"]
    expected = (g.no_vig_pair(-150, 130) + g.no_vig_pair(-140, 120)) / 2
    assert m["close_probability"] == pytest.approx(expected)


def test_only_pregame_close_moneyline_main_rows_are_used():
    rows = (
        _pair("e1", "A Team", -300, "B Team", 250, kind="CURRENT")
        + _pair("e1", "A Team", -300, "B Team", 250, kind="OPEN")
        + _pair("e1", "A Team", -300, "B Team", 250, live=True)
        + _pair("e1", "A Team", -300, "B Team", 250, market="2")
        + _pair("e1", "A Team", -300, "B Team", 250, main=False)
    )
    m = g.match_close(selected="A Team", opponent="B Team", event_start=START, close_rows=rows)
    assert m["status"] == g.NO_CLOSE_EVENT


def test_one_sided_book_is_skipped_and_all_one_sided_fails_typed():
    rows = [_close("e1", "A Team", -150, book="19"), _close("e1", "B Team", 130, book="3")]
    m = g.match_close(selected="A Team", opponent="B Team", event_start=START, close_rows=rows)
    assert m["status"] == g.NO_TWO_SIDED_CLOSE


def test_doubleheader_matches_each_game_by_start_time():
    later = "2026-10-06T03:10:00Z"
    rows = _pair("g1", "A Team", -150, "B Team", 130) + _pair("g2", "A Team", 110, "B Team", -130, start=later)
    m1 = g.match_close(selected="A Team", opponent="B Team", event_start=START, close_rows=rows)
    m2 = g.match_close(
        selected="A Team", opponent="B Team",
        event_start=datetime(2026, 10, 6, 3, 10, tzinfo=timezone.utc), close_rows=rows,
    )
    assert (m1["provider_event_id"], m2["provider_event_id"]) == ("g1", "g2")


def test_selected_only_match_is_ambiguous_when_two_events_fit():
    rows = _pair("e1", "A Team", -150, "B Team", 130) + _pair(
        "e2", "A Team", -150, "C Team", 130, start="2026-10-05T23:15:00Z"
    )
    m = g.match_close(selected="A Team", opponent=None, event_start=START, close_rows=rows)
    assert m["status"] == g.AMBIGUOUS_EVENT


def test_start_time_outside_tolerance_or_wrong_team_never_matches():
    rows = _pair("e1", "A Team", -150, "B Team", 130, start="2026-10-06T01:00:00Z")
    assert g.match_close(selected="A Team", opponent="B Team", event_start=START, close_rows=rows)["status"] == g.NO_CLOSE_EVENT
    rows = _pair("e1", "A Team", -150, "B Team", 130)
    assert g.match_close(selected="A Team", opponent="Z Team", event_start=START, close_rows=rows)["status"] == g.NO_CLOSE_EVENT


# --- grading -------------------------------------------------------------------

def _grade(**overrides):
    kwargs = dict(
        source=g.SOURCE_MLB, prediction_id="p1", sport="MLB", official_event_id="776",
        selected="A Team", opponent="B Team", event_start="2026-10-05T23:08:00Z",
        model_probability=0.7, outcome=1, close_rows=_pair("e1", "A Team", -150, "B Team", 130),
    )
    kwargs.update(overrides)
    return g.grade_row(**kwargs)


def test_grade_metrics_and_sign_convention():
    row = _grade()
    p_close = g.no_vig_pair(-150, 130)
    assert row["link_status"] == g.LINKED
    assert row["model_brier"] == pytest.approx(0.09)
    assert row["close_brier"] == pytest.approx((1 - p_close) ** 2)
    # Positive advantage = model closer to the result than the close.
    assert row["brier_advantage_vs_close"] == pytest.approx(row["close_brier"] - 0.09)
    assert row["brier_advantage_vs_close"] > 0
    assert row["log_loss_advantage_vs_close"] == pytest.approx(-math.log(p_close) + math.log(0.7))
    assert row["prediction_authority"] is False and row["can_execute"] is False


@pytest.mark.parametrize(
    "override",
    [{"model_probability": 1.0}, {"model_probability": 0}, {"model_probability": None},
     {"outcome": True}, {"outcome": 2}, {"event_start": "not-a-date"}, {"selected": ""}],
)
def test_invalid_prediction_fields_fail_typed(override):
    assert _grade(**override)["link_status"] == g.INVALID_PREDICTION


# --- runtime -------------------------------------------------------------------

class _Query:
    def __init__(self, client, table):
        self.client, self.table, self.filters = client, table, []

    def select(self, *_a, **_k):
        return self

    def eq(self, field, value):
        self.filters.append(lambda r: r.get(field) == value)
        return self

    def gte(self, field, value):
        self.filters.append(lambda r: str(r.get(field)) >= value)
        return self

    def lt(self, field, value):
        self.filters.append(lambda r: str(r.get(field)) < value)
        return self

    def in_(self, field, values):
        self.filters.append(lambda r: r.get(field) in values)
        return self

    def limit(self, *_a):
        return self

    def upsert(self, rows, **kwargs):
        self.client.upserts.append((self.table, rows, kwargs))
        return self

    def execute(self):
        rows = [r for r in self.client.data.get(self.table, []) if all(f(r) for f in self.filters)]
        return SimpleNamespace(data=rows)


class _Client:
    def __init__(self, data):
        self.data, self.upserts = data, []

    def table(self, name):
        return _Query(self, name)


def _runtime_data():
    pred = lambda pid, created, p: {  # noqa: E731
        "event_prediction_id": pid, "created_at": created, "sport": "MLB", "official_event_id": "776",
        "event_start_time": "2026-10-05T23:08:00Z", "home_team": "A Team", "away_team": "B Team",
        "calibrated_home_probability": p,
    }
    return {
        "wow_event_outcomes": [
            {"event_prediction_id": pid, "official_winner": "A Team", "void": False, "created_at": "2026-10-06T03:00:00Z"}
            for pid in ("early", "final", "postgame")
        ],
        "wow_event_predictions": [
            pred("early", "2026-10-05T12:00:00Z", 0.55),
            pred("final", "2026-10-05T22:30:00Z", 0.62),
            pred("postgame", "2026-10-05T23:30:00Z", 0.99),  # after start: never graded
        ],
        "wow_nfl_forward_shadow_grades": [{
            "grade_id": "n1", "official_event_id": "2026_05_X_Y", "event_start_time_utc": "2026-10-05T17:00:00Z",
            "selected_participant": "Nobody FC", "calibrated_probability": 0.6, "outcome": 1,
            "created_at": "2026-10-06T00:00:00Z",
        }],
        "wow_market_price_observations": [
            {**r, "provider": "ESPN", "sport_key": "baseball_mlb"}
            for r in _pair("e1", "A Team", -150, "B Team", 130)
        ],
        "wow_closing_line_grades": [],
    }


NOW = datetime(2026, 10, 9, 15, tzinfo=timezone.utc)


def test_runtime_grades_final_pregame_prediction_once_and_counts_nonlinks():
    client = _Client(_runtime_data())
    result = g.run_closing_line_grading(client, now=NOW)
    assert result["grades_written"] == 1
    table, rows, kwargs = client.upserts[0]
    assert table == g.TABLE
    assert kwargs == {"on_conflict": "prediction_source,prediction_id", "ignore_duplicates": True}
    assert rows[0]["prediction_id"] == "final"
    assert rows[0]["model_probability"] == pytest.approx(0.62)
    assert rows[0]["outcome"] == 1
    assert result["by_sport"]["NFL"]["statuses"] == {g.NO_CLOSE_EVENT: 1}
    assert result["can_execute"] is False


def test_runtime_skips_already_linked_predictions():
    data = _runtime_data()
    data["wow_closing_line_grades"] = [{"prediction_source": g.SOURCE_MLB, "prediction_id": "final"}]
    client = _Client(data)
    result = g.run_closing_line_grading(client, now=NOW)
    assert result["grades_written"] == 0
    assert client.upserts == []


def test_history_loop_grading_failure_is_isolated(caplog):
    def broken_client():
        raise RuntimeError("table missing")

    with caplog.at_level(logging.ERROR):
        assert history.run_closing_line_grading_cycle(broken_client, logging.getLogger("t")) is None
    assert "CLOSING_LINE_GRADING=FAIL" in caplog.text


# --- pick-time price and closing-line value ------------------------------------

def _cur(team, odds, at, *, event="e1", book="19"):
    return {**_close(event, team, odds, book=book, kind="CURRENT"), "price_updated_at": at, "fetched_at": at}


def _clv_rows():
    return (
        [_cur("A Team", -120, "2026-10-05T14:00:00Z"), _cur("B Team", 100, "2026-10-05T14:00:00Z")]
        + [_cur("A Team", -135, "2026-10-05T20:00:00Z"), _cur("B Team", 115, "2026-10-05T20:00:00Z")]
        + _pair("e1", "A Team", -150, "B Team", 130)
    )


def test_pick_price_uses_latest_capture_at_or_before_pick_and_clv_sign():
    row = _grade(close_rows=_clv_rows(), predicted_at="2026-10-05T15:30:00Z", model_probability=0.62)
    pick = g.no_vig_pair(-120, 100)
    close = g.no_vig_pair(-150, 130)
    assert row["pick_market_probability"] == pytest.approx(pick)
    assert row["pick_quote_at"] == "2026-10-05T14:00:00Z"
    assert row["clv_model_side"] == pytest.approx(close - pick)  # model favoured A; line moved toward A
    assert row["clv_model_side"] > 0


def test_clv_is_measured_on_the_side_the_model_favoured():
    row = _grade(close_rows=_clv_rows(), predicted_at="2026-10-05T15:30:00Z", model_probability=0.40)
    assert row["clv_model_side"] == pytest.approx(-(g.no_vig_pair(-150, 130) - g.no_vig_pair(-120, 100)))
    assert row["clv_model_side"] < 0


def test_later_capture_is_used_for_a_later_pick():
    row = _grade(close_rows=_clv_rows(), predicted_at="2026-10-05T21:00:00Z")
    assert row["pick_market_probability"] == pytest.approx(g.no_vig_pair(-135, 115))


@pytest.mark.parametrize("predicted_at", [None, "2026-10-05T10:00:00Z", "2026-10-05T23:30:00Z"])
def test_no_pick_price_before_any_capture_or_after_start_or_unknown(predicted_at):
    row = _grade(close_rows=_clv_rows(), predicted_at=predicted_at)
    assert row["link_status"] == g.LINKED
    assert row["pick_market_probability"] is None and row["clv_model_side"] is None


def test_one_sided_book_at_pick_time_is_skipped():
    rows = [_cur("A Team", -120, "2026-10-05T14:00:00Z")] + _pair("e1", "A Team", -150, "B Team", 130)
    row = _grade(close_rows=rows, predicted_at="2026-10-05T15:00:00Z")
    assert row["pick_market_probability"] is None


@pytest.mark.parametrize("raw, full", [("PIT", "Pittsburgh Steelers"), ("SF", "San Francisco 49ers"), ("Buffalo Bills", "Buffalo Bills"), ("", "")])
def test_nfl_abbreviated_historic_grades_expand_for_matching(raw, full):
    assert g._nfl_full_name(raw) == full


def test_clv_fails_closed_on_backdated_quote_observed_after_pick():
    """First observed in WOW after pick is never a valid price-at-pick quote."""
    from v17.closing_line_grading import pick_time_price
    from datetime import datetime, timezone

    at = datetime(2026, 10, 5, 15, 30, tzinfo=timezone.utc)
    backdated = [
        {**_cur("Team A", -130, "2026-10-05T16:00:00Z"),
         "price_updated_at": "2026-10-05T15:00:00Z"},
        {**_cur("Team B", +110, "2026-10-05T16:00:00Z"),
         "price_updated_at": "2026-10-05T15:00:00Z"},
    ]
    assert pick_time_price(
        selected="Team A", provider_event_id="e1", rows=backdated, at=at
    ) is None
    valid_early = [
        _cur("Team A", -115, "2026-10-05T14:00:00Z"),
        _cur("Team B", -105, "2026-10-05T14:00:00Z"),
    ]
    result = pick_time_price(
        selected="Team A", provider_event_id="e1", rows=valid_early + backdated, at=at
    )
    assert result is not None
    assert result["quote_at"] == "2026-10-05T14:00:00+00:00"


def test_clv_requires_fetched_at_provenance_even_if_provider_timestamp_old():
    from v17.closing_line_grading import pick_time_price
    from datetime import datetime, timezone

    at = datetime(2026, 10, 5, 15, 30, tzinfo=timezone.utc)
    rows = [
        {**_cur("Team A", -120, "2026-10-05T14:00:00Z"), "fetched_at": None},
        {**_cur("Team B", +110, "2026-10-05T14:00:00Z"), "fetched_at": None},
    ]
    assert pick_time_price(selected="Team A", provider_event_id="e1", rows=rows, at=at) is None
