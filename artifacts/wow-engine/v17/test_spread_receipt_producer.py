"""Governed pregame spread producer: certified artifact in, admitted receipts out."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from v17 import spread_receipt_producer as producer

START = datetime(2026, 11, 12, 23, 0, tzinfo=timezone.utc)
OBSERVED_AT = START - timedelta(hours=1)


def _artifact_payload(**overrides):
    payload = {
        "sport": "NFL",
        "model_family": "NFL_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1",
        "feature_schema_version": "NFL_SPREAD_MARGIN_TEAM_STATE_V1",
        "feature_names": ["a", "b"],
        "scaler_mean": [0.0, 0.0],
        "scaler_scale": [1.0, 1.0],
        "coefficients": [1.0, 0.0],
        "intercept": 0.0,
        "calibration_residuals": [-2.0, -1.0, 0.0, 1.0, 2.0],
        "train_rows": 100,
        "calibration_rows": 50,
        "test_rows": 50,
        "training_dataset_hash": "dataset-sha-1",
        "ridge_alpha": 4.0,
        # Certification identity: supplied by the certifying body, not derived here.
        "lifecycle_state": "CERTIFIED",
        "probability_publishable": True,
        "controlling_spread_specialist": "NFL_SPREAD_MARGIN_DISTRIBUTION_CHALLENGER_V1",
        "calibration_version": "nfl-spread-cal-v1",
    }
    payload.update(overrides)
    return payload


def _event(event_id="nfl-123", *, start=START, home="DAL", away="ATL"):
    return {
        "event_id": event_id,
        "event_start_time": start.isoformat(),
        "home_team": home,
        "away_team": away,
    }


def _market_rows(*, home_spread=-0.5, provider_event_id="rd-nfl-9101", start=START):
    common = {
        "provider_event_id": provider_event_id,
        "sport_key": "americanfootball_nfl",
        "event_start_utc": start.isoformat(),
        "market_id": "2",
        "market_name": "spreads",
        "sportsbook": "Pinnacle",
        "price_updated_at": (start - timedelta(hours=2)).isoformat(),
        # A bound quote must have been actually observed, not merely re-timestamped.
        "fetched_at": (start - timedelta(hours=2)).isoformat(),
        "is_live": False,
        "is_main_line": True,
        "is_available": True,
    }
    return [
        {**common, "participant_type": "home", "participant_name": "Dallas Cowboys",
         "line_value": home_spread},
        {**common, "participant_type": "away", "participant_name": "Atlanta Falcons",
         "line_value": -home_spread},
    ]


class _DB:
    """Append-only ledger read plus receipt upsert recording."""

    def __init__(self, market_rows=()):
        self.market_rows = list(market_rows)
        self.upserts: list[dict] = []
        self._mode = "read"
        self._name = ""

    def table(self, name):
        self._name = name
        self._mode = "read"
        return self

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def gte(self, *_args, **_kwargs):
        return self

    def lte(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False):
        self._mode = "write"
        self.upserts.append(
            {
                "table": self._name,
                "rows": [dict(row) for row in rows],
                "on_conflict": on_conflict,
                "ignore_duplicates": ignore_duplicates,
            }
        )
        return self

    def execute(self):
        if self._mode == "write":
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=self.market_rows)


def _features(_db, *, sport, event, artifact):
    return {name: 0.0 for name in artifact.feature_names}


def _run(db, **kwargs):
    kwargs.setdefault("artifact_payload", _artifact_payload())
    kwargs.setdefault("feature_provider", _features)
    kwargs.setdefault("observed_at", OBSERVED_AT)
    return producer.run_pregame_spread_production(
        db, sport=kwargs.pop("sport", "NFL"), events=kwargs.pop("events", [_event()]), **kwargs
    )


def test_a_certified_artifact_produces_one_admitted_receipt():
    db = _DB(_market_rows())

    result = _run(db)

    assert result["status"] == "COMPLETE"
    row = result["rows"][0]
    assert row["status"] == "PERSISTED"
    assert row["p_cover"] == 0.4
    assert 0.0 < row["calibrated_probability_lower_bound"] < 0.4
    assert 0.4 < row["calibrated_probability_upper_bound"] < 1.0
    assert row["can_execute"] is False

    assert len(db.upserts) == 1
    stored = db.upserts[0]["rows"][0]
    assert db.upserts[0]["table"] == producer.RECEIPT_TABLE
    assert db.upserts[0]["on_conflict"] == "receipt_key"
    assert db.upserts[0]["ignore_duplicates"] is True
    assert stored["line_source"] == producer.LEDGER_LINE_SOURCE
    assert stored["exact_line_side"] == "HOME"
    assert stored["exact_signed_spread"] == -0.5
    assert stored["quote_sportsbook"] == "Pinnacle"
    assert stored["provider_event_id"] == "rd-nfl-9101"
    assert stored["settlement_basis"] == "FULL_GAME_INCLUDING_OVERTIME"
    assert stored["model_class"] == "CERTIFIED_FITTED"
    assert stored["controlling_spread_specialist"] == "NFL_SPREAD_MARGIN_DISTRIBUTION_CHALLENGER_V1"
    assert stored["calibration_version"] == "nfl-spread-cal-v1"
    assert stored["admission_status"] == "PASS"
    assert stored["can_execute"] is False
    assert result["run_summary"]["reconciliation_balanced"] is True
    assert result["run_summary"]["can_execute"] is False
    assert result["run_summary"]["dry_run_only"] is True


def test_without_a_certified_artifact_nothing_is_written():
    db = _DB(_market_rows())

    result = _run(db, artifact_payload=None)

    assert result["status"] == "MODEL_UNAVAILABLE"
    assert result["run_summary"]["receipts_persisted"] == 0
    assert [row["status"] for row in result["rows"]] == ["HELD"]
    assert result["rows"][0]["reason_codes"] == ["MODEL_UNAVAILABLE"]
    assert "CERTIFIED_SPREAD_ARTIFACT_UNAVAILABLE" in result["run_summary"]["artifact_reason"]
    assert db.upserts == []


def test_a_challenger_artifact_is_not_a_certified_capability():
    db = _DB(_market_rows())
    payload = _artifact_payload(lifecycle_state="CHALLENGER", probability_publishable=False)

    result = _run(db, artifact_payload=payload)

    assert result["status"] == "MODEL_UNAVAILABLE"
    assert "CHALLENGER" in result["run_summary"]["artifact_reason"]
    assert db.upserts == []


def test_a_malformed_certified_artifact_is_output_invalid_not_unavailable():
    db = _DB(_market_rows())
    payload = _artifact_payload(coefficients=[1.0])

    result = _run(db, artifact_payload=payload)

    assert result["status"] == "MODEL_OUTPUT_INVALID"
    assert db.upserts == []


def test_a_certified_artifact_without_a_specialist_identity_is_invalid():
    payload = _artifact_payload()
    payload.pop("controlling_spread_specialist")

    result = _run(_DB(_market_rows()), artifact_payload=payload)

    assert result["status"] == "MODEL_OUTPUT_INVALID"
    assert "controlling_spread_specialist" in str(result["rows"][0]["detail"])


def test_a_missing_ledger_line_holds_the_event_without_writing():
    db = _DB([])

    result = _run(db)

    row = result["rows"][0]
    assert row["status"] == "HELD"
    # The binder's own typed reason survives; no line and no probability appear.
    assert row["reason_codes"] == ["SPREAD_EVENT_NOT_FOUND"]
    assert "p_cover" not in row
    assert result["status"] == "PARTIAL"
    assert db.upserts == []


def test_ledger_rows_for_another_matchup_do_not_produce_a_line():
    # Real observations exist, but none belong to this event: the event holds
    # rather than borrowing another game's spread.
    unrelated = [
        {**_market_rows()[0], "participant_name": "Buffalo Bills"},
        {**_market_rows()[1], "participant_name": "New York Jets"},
    ]
    db = _DB(unrelated)

    result = _run(db)

    assert result["rows"][0]["status"] == "HELD"
    assert result["rows"][0]["reason_codes"]
    assert "p_cover" not in result["rows"][0]
    assert db.upserts == []


def test_a_started_event_is_held_before_any_model_work():
    db = _DB(_market_rows())
    calls = []

    def features(*args, **kwargs):
        calls.append(args)
        return {"a": 0.0, "b": 0.0}

    result = _run(
        db,
        events=[_event()],
        feature_provider=features,
        observed_at=START + timedelta(minutes=5),
    )

    assert result["rows"][0]["reason_codes"] == ["SPREAD_EVENT_ALREADY_STARTED"]
    assert calls == []
    assert db.upserts == []


def test_missing_features_is_inputs_insufficient():
    db = _DB(_market_rows())

    result = _run(db, feature_provider=None)

    assert result["rows"][0]["status"] == "HELD"
    assert "MODEL_INPUTS_INSUFFICIENT" in result["rows"][0]["reason_codes"]
    assert db.upserts == []


def test_a_feature_schema_mismatch_is_insufficient_not_unavailable():
    db = _DB(_market_rows())

    result = _run(db, feature_provider=lambda *_a, **_k: {"a": 0.0})

    assert result["rows"][0]["reason_codes"] == ["MODEL_INPUTS_INSUFFICIENT"]
    assert db.upserts == []


def test_a_throwing_scorer_is_scorer_failed():
    def boom(*_args, **_kwargs):
        raise RuntimeError("scorer exploded")

    result = _run(_DB(_market_rows()), scorer=boom)

    assert result["rows"][0]["reason_codes"][0] == "MODEL_SCORER_FAILED"


def test_a_degenerate_distribution_is_rejected_by_the_write_boundary():
    # A +3.0 home line covers on every residual sample, so the bound is not
    # interior and the registry refuses the receipt instead of publishing 1.0.
    result = _run(_DB(_market_rows(home_spread=3.0)))

    assert result["rows"][0]["status"] == "REJECTED"
    assert result["rows"][0]["reason_codes"] == ["RECEIPT_BOUNDS_INCOHERENT"]
    assert result["run_summary"]["receipts_persisted"] == 0


def test_a_mixed_slate_reconciles_and_writes_only_admitted_rows():
    db = _DB(_market_rows())
    events = [
        _event(),
        _event("nfl-456", home="BUF", away="NYJ"),  # no ledger quote
        _event("nfl-789", home="KC", away="LAC", start=START - timedelta(hours=3)),  # started
    ]

    result = _run(db, events=events)

    summary = result["run_summary"]
    assert summary["rows_in"] == 3
    assert summary["receipts_persisted"] == 1
    assert summary["held"] == 2
    assert summary["rejected"] == 0
    assert summary["reconciliation_balanced"] is True
    assert result["status"] == "PARTIAL"
    assert len(db.upserts) == 1


def test_the_event_limit_is_bounded():
    db = _DB(_market_rows())
    events = [_event(f"nfl-{index}", home="DAL", away="ATL") for index in range(20)]

    capped = _run(db, events=events, requested_limit=999)
    assert capped["run_summary"]["rows_in"] == producer.MAX_EVENT_LIMIT

    floored = _run(db, events=events, requested_limit=0)
    assert floored["run_summary"]["rows_in"] == 1


def test_the_moneyline_slate_adapter_uses_lane_identities():
    db = _DB(_market_rows())
    moneyline = [
        {
            "lane": "MONEYLINE",
            "identity": {
                "official_event_id": "nfl-123",
                "sport": "NFL",
                "home_team": "DAL",
                "away_team": "ATL",
                "event_start_time": START.isoformat(),
            },
            "result": {"sport": "NFL"},
        },
        {
            "lane": "MONEYLINE",
            "identity": {
                "official_event_id": "nba-1",
                "sport": "NBA",
                "home_team": "BOS",
                "away_team": "NYK",
                "event_start_time": START.isoformat(),
            },
            "result": {"sport": "NBA"},
        },
    ]

    events = producer.events_from_moneyline_rows(moneyline, sport="NFL")
    assert [event["event_id"] for event in events] == ["nfl-123"]

    result = producer.run_pregame_spread_production_from_moneyline(
        db,
        moneyline_rows=moneyline,
        sport="NFL",
        artifact_payload=_artifact_payload(),
        feature_provider=_features,
        observed_at=OBSERVED_AT,
    )
    assert result["status"] == "COMPLETE"
    assert len(db.upserts) == 1


def test_the_producer_never_reports_execution_authority():
    result = _run(_DB(_market_rows()))

    assert result["can_execute"] is False
    assert result["run_summary"]["can_execute"] is False
    assert result["run_summary"]["automatic_certification"] is False
    assert result["run_summary"]["automatic_promotion"] is False
    assert result["run_summary"]["write_scope"] == producer.WRITE_SCOPE
    assert all(row["can_execute"] is False for row in result["rows"])
    assert producer.DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS is True


@pytest.mark.parametrize("sport", ["NFL", "NBA", "WNBA", "NCAAF", "NCAAB"])
def test_every_supported_sport_shares_the_lane_settlement_basis(sport):
    from v17.daily_snapshot_runtime import _settlement_basis

    assert producer.settlement_basis_for(sport) == _settlement_basis(sport)
