from datetime import datetime, timezone
from types import SimpleNamespace
import sys

import v17_synthetic_self_acceptance as acceptance


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def table(self, _name):
        return self

    def select(self, *_args, **_kwargs):
        return self

    def gt(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _Result(self.rows)


def _install_production(monkeypatch, rows):
    client = _Query(rows)
    production = SimpleNamespace(
        market_api=SimpleNamespace(prod=SimpleNamespace(get_client=lambda: client))
    )
    monkeypatch.setitem(sys.modules, "api_prod_market_acceptance", production)


def _row(
    event_id,
    snapshot_ts,
    *,
    lineup_status,
    score_status,
    hydration_status="PASS",
    start="2026-09-06T00:05:00+00:00",
):
    return {
        "official_event_id": event_id,
        "official_date": "2026-09-05",
        "event_start_time": start,
        "home_team": "HOME",
        "away_team": "AWAY",
        "snapshot_id": f"snapshot-{event_id}-{snapshot_ts}",
        "snapshot_timestamp": snapshot_ts,
        "lineup_status": lineup_status,
        "feature_hydration_status": hydration_status,
        "model_score_status": score_status,
    }


def test_stale_projected_snapshot_is_ignored_when_same_event_has_newer_confirmed_snapshot(monkeypatch):
    rows = [
        _row("824797", "2026-09-04T00:08:08+00:00", lineup_status="PROJECTED", score_status="SHADOW_SCORED_LINEUP_PENDING"),
        _row("824797", "2026-09-04T05:47:09+00:00", lineup_status="CONFIRMED", score_status="SHADOW_SCORED_PREGAME"),
    ]
    _install_production(monkeypatch, rows)

    candidate = acceptance._real_projected_mlb_candidate(datetime(2026, 9, 4, 22, 45, tzinfo=timezone.utc))

    assert candidate is None


def test_stale_pass_snapshot_is_ignored_when_newer_event_snapshot_is_held(monkeypatch):
    rows = [
        _row("824798", "2026-09-04T00:08:08+00:00", lineup_status="PROJECTED", score_status="SHADOW_SCORED_LINEUP_PENDING"),
        _row(
            "824798",
            "2026-09-04T05:47:09+00:00",
            lineup_status="PROJECTED",
            score_status="MODEL_INPUTS_INSUFFICIENT",
            hydration_status="HOLD",
        ),
    ]
    _install_production(monkeypatch, rows)

    candidate = acceptance._real_projected_mlb_candidate(datetime(2026, 9, 4, 22, 45, tzinfo=timezone.utc))

    assert candidate is None


def test_probe_selects_another_event_only_if_its_latest_snapshot_is_still_projected(monkeypatch):
    rows = [
        _row("824797", "2026-09-04T00:08:08+00:00", lineup_status="PROJECTED", score_status="SHADOW_SCORED_LINEUP_PENDING", start="2026-09-06T00:05:00+00:00"),
        _row("824797", "2026-09-04T05:47:09+00:00", lineup_status="CONFIRMED", score_status="SHADOW_SCORED_PREGAME", start="2026-09-06T00:05:00+00:00"),
        _row("824900", "2026-09-04T06:00:00+00:00", lineup_status="PROJECTED", score_status="SHADOW_SCORED_LINEUP_PENDING", start="2026-09-06T01:05:00+00:00"),
    ]
    _install_production(monkeypatch, rows)

    candidate = acceptance._real_projected_mlb_candidate(datetime(2026, 9, 4, 22, 45, tzinfo=timezone.utc))

    assert candidate is not None
    assert str(candidate["official_event_id"]) == "824900"
    assert candidate["snapshot_timestamp"] == "2026-09-04T06:00:00+00:00"


def test_newest_snapshot_wins_even_if_database_returns_rows_out_of_order(monkeypatch):
    rows = [
        _row("824901", "2026-09-04T08:00:00+00:00", lineup_status="CONFIRMED", score_status="SHADOW_SCORED_PREGAME"),
        _row("824901", "2026-09-04T02:00:00+00:00", lineup_status="PROJECTED", score_status="SHADOW_SCORED_LINEUP_PENDING"),
    ]
    _install_production(monkeypatch, rows)

    candidate = acceptance._real_projected_mlb_candidate(datetime(2026, 9, 4, 22, 45, tzinfo=timezone.utc))

    assert candidate is None
