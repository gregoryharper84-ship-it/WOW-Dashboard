from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import v17.spread_historical_close_proxy as proxy
from v17.spread_margin_challenger import MarginTrainingRow, SpreadChallengerUnavailable


def _row(event_id: str, margin: int) -> MarginTrainingRow:
    start = datetime(2025, 9, 1, tzinfo=timezone.utc)
    return MarginTrainingRow(
        event_id=event_id,
        event_start_time=start.isoformat(),
        feature_as_of=(start - timedelta(hours=1)).isoformat(),
        margin=margin,
        features={"x": 1.0},
        source_manifest_sha256=f"sha-{event_id}",
    )


def test_nflverse_positive_home_favorite_line_is_negated_to_v17_home_spread():
    evidence = proxy.nflverse_close_proxy(
        {
            "game_id": "2025_01_DAL_PHI",
            "home_team": "PHI",
            "away_team": "DAL",
            "spread_line": 7.5,
        }
    )
    assert evidence.home_spread == -7.5
    assert evidence.evidence_class == proxy.NFLVERSE_EVIDENCE_CLASS
    payload = evidence.payload()
    assert payload["live_card_receipt_eligible"] is False
    assert payload["clv_evidence"] is False
    assert payload["probability_publishable"] is False
    assert payload["rank_eligible"] is False
    assert payload["can_execute"] is False


def test_nflverse_negative_away_favorite_line_becomes_positive_home_spread():
    evidence = proxy.nflverse_close_proxy(
        {
            "game_id": "2025_02_A_B",
            "home_team": "B",
            "away_team": "A",
            "spread_line": -3.0,
        }
    )
    assert evidence.home_spread == 3.0


def test_nflverse_missing_line_fails_closed():
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        proxy.nflverse_close_proxy({"game_id": "g1", "home_team": "H", "away_team": "A"})
    assert exc.value.code == "SPREAD_HISTORICAL_PROXY_ROW_INCOMPLETE"


def test_espn_pickcenter_home_named_detail_preserves_signed_home_line():
    evidence = proxy.espn_pickcenter_close_proxy(
        summary={"id": "401", "pickcenter": [{"details": "NY -5.5", "spread": 5.5}]},
        sport="WNBA",
        event_id="espn-401",
        home_team_abbreviation="NY",
        away_team_abbreviation="LV",
    )
    assert evidence.home_spread == -5.5
    assert evidence.evidence_class == proxy.ESPN_WNBA_EVIDENCE_CLASS
    assert evidence.source_quote_timestamp_state == proxy.QUOTE_TIMESTAMP_STATE


def test_espn_pickcenter_away_named_detail_inverts_to_home_line():
    evidence = proxy.espn_pickcenter_close_proxy(
        summary={"id": "402", "pickcenter": [{"details": "LV -2.5", "spread": 2.5}]},
        sport="WNBA",
        event_id="espn-402",
        home_team_abbreviation="NY",
        away_team_abbreviation="LV",
    )
    assert evidence.home_spread == 2.5


def test_espn_mlb_proxy_keeps_separate_run_line_evidence_class():
    evidence = proxy.espn_pickcenter_close_proxy(
        summary={"id": "999", "pickcenter": [{"details": "LAD -1.5"}]},
        sport="MLB",
        event_id="canonical-mlb-game",
        home_team_abbreviation="LAD",
        away_team_abbreviation="SF",
    )
    assert evidence.home_spread == -1.5
    assert evidence.evidence_class == proxy.ESPN_MLB_EVIDENCE_CLASS
    assert evidence.payload()["historical_certification_evidence_only"] is True


def test_espn_pickcenter_ambiguous_distinct_lines_fail_closed():
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        proxy.espn_pickcenter_close_proxy(
            summary={
                "id": "403",
                "pickcenter": [
                    {"details": "NY -4.5"},
                    {"details": "NY -5.5"},
                ],
            },
            sport="WNBA",
            event_id="espn-403",
            home_team_abbreviation="NY",
            away_team_abbreviation="LV",
        )
    assert exc.value.code == "SPREAD_HISTORICAL_PROXY_LINE_AMBIGUOUS"


def test_espn_pickcenter_does_not_infer_from_generic_spread_when_details_missing():
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        proxy.espn_pickcenter_close_proxy(
            summary={"id": "404", "pickcenter": [{"spread": 6.5}]},
            sport="WNBA",
            event_id="espn-404",
            home_team_abbreviation="NY",
            away_team_abbreviation="LV",
        )
    assert exc.value.code == "SPREAD_HISTORICAL_PROXY_LINE_UNAVAILABLE"


def test_historical_evaluator_reports_metrics_and_remains_non_publishable(monkeypatch):
    def fake_score(_artifact, _features, *, home_spread):
        if home_spread < 0:
            return {"p_cover": 0.60, "p_push": 0.0, "p_not_cover": 0.40, "p_cover_given_no_push": 0.60}
        return {"p_cover": 0.45, "p_push": 0.0, "p_not_cover": 0.55, "p_cover_given_no_push": 0.45}

    monkeypatch.setattr(proxy, "score_home_spread", fake_score)
    evidence = {
        "g1": proxy.HistoricalCloseProxy(
            event_id="g1", sport="NFL", home_team="H", away_team="A", home_spread=-3.5,
            evidence_class=proxy.NFLVERSE_EVIDENCE_CLASS, source_provider="NFLVERSE",
            source_record_id="g1", source_payload_sha256="a" * 64, source_line_semantics="TEST",
        ),
        "g2": proxy.HistoricalCloseProxy(
            event_id="g2", sport="NFL", home_team="H2", away_team="A2", home_spread=2.5,
            evidence_class=proxy.NFLVERSE_EVIDENCE_CLASS, source_provider="NFLVERSE",
            source_record_id="g2", source_payload_sha256="b" * 64, source_line_semantics="TEST",
        ),
    }
    result = proxy.evaluate_historical_close_proxy(
        artifact=object(),
        test_rows=[_row("g1", 7), _row("g2", -1)],
        evidence_by_event=evidence,
        evidence_class=proxy.NFLVERSE_EVIDENCE_CLASS,
    )
    assert result["evidence_row_n"] == 2
    assert result["exact_line_coverage"] == 1.0
    assert result["cover_brier"] is not None
    assert result["three_way_brier"] is not None
    assert result["live_card_receipt_eligible"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False
