from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from v17.prediction_receipt_lookup_runtime import (
    PredictionReceiptLookupBatch,
    install_prediction_receipt_lookup_route,
    lookup_prediction_receipts,
)


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, _fields):
        return self

    def eq(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) == value]
        return self

    def gte(self, field, value):
        self.rows = [row for row in self.rows if str(row.get(field)) >= str(value)]
        return self

    def lt(self, field, value):
        self.rows = [row for row in self.rows if str(row.get(field)) < str(value)]
        return self

    def order(self, field, desc=False):
        self.rows.sort(key=lambda row: str(row.get(field) or ""), reverse=desc)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "wow_predictions"
        return _Query(self.rows)


def _prediction(**overrides):
    now = datetime.now(timezone.utc)
    event_start = now + timedelta(hours=3)
    base = {
        "prediction_id": "11111111-1111-1111-1111-111111111111",
        "created_at": (now - timedelta(minutes=2)).isoformat(),
        "event_id": "MLB:2026-09-13:BOS-KC",
        "event_start_time": event_start.isoformat(),
        "model_timestamp": (now - timedelta(minutes=3)).isoformat(),
        "locked_at": (now - timedelta(minutes=2)).isoformat(),
        "player": "Payton Tolle",
        "team": "BOS",
        "opponent": "KC",
        "sport": "MLB",
        "market_type": "PROP_DISCRETE_PMF",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 4.5,
        "direction": "MORE",
        "source_snapshot_id": "22222222-2222-2222-2222-222222222222",
        "raw_model_probability": 0.70,
        "independent_model_probability": 0.69,
        "calibrated_probability": 0.68,
        "calibrated_probability_lower_bound": 0.62,
        "calibrated_probability_upper_bound": 0.74,
        "calibration_status": "PRECALIBRATION_SHRINKAGE",
        "calibration_method": "PRECALIBRATION_SHRINKAGE",
        "calibration_version": "v1",
        "probability_publishable": True,
        "probability_ceiling": "MODEL_QUALIFIED_HOLD",
        "money_lane_status": "PAYOUT_UNRESOLVED",
        "data_gaps": [],
        "blockers": [],
    }
    base.update(overrides)
    return base


def _exact_identity(**overrides):
    identity = {
        "event_id": "MLB:2026-09-13:BOS-KC",
        "player": "Payton Tolle",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 4.5,
        "direction": "MORE",
    }
    identity.update(overrides)
    return identity


def test_exact_thesis_recovers_immutable_pregame_probability_package():
    batch = PredictionReceiptLookupBatch.model_validate(
        {"rows": [{"row_key": "tolle-k", **_exact_identity(), "sport": "MLB"}]}
    )
    result = lookup_prediction_receipts(_Db([_prediction()]), batch)
    assert result["rows_matched"] == 1
    assert result["rows_blocked"] == 0
    receipt = result["rows"][0]["matches"][0]
    assert receipt["governed_prediction_table"] == "wow_predictions"
    assert receipt["is_immutable_pregame"] is True
    assert receipt["calibrated_probability"] == 0.68
    assert receipt["calibrated_lower_bound"] == 0.62
    assert receipt["can_execute"] is False


def test_prediction_id_with_conflicting_line_is_blocked_not_silently_retrieved():
    batch = PredictionReceiptLookupBatch.model_validate(
        {
            "rows": [
                {
                    "prediction_id": "11111111-1111-1111-1111-111111111111",
                    **_exact_identity(line=3.5),
                }
            ]
        }
    )
    result = lookup_prediction_receipts(_Db([_prediction()]), batch)
    row = result["rows"][0]
    assert result["rows_blocked"] == 1
    assert result["rows_matched"] == 0
    assert row["code"] == "PREDICTION_RECEIPT_IDENTITY_CONFLICT"
    assert row["detail"]["conflicting_fields"] == ["line"]
    assert row["matches"] == []


def test_prediction_id_with_conflicting_direction_is_blocked():
    batch = PredictionReceiptLookupBatch.model_validate(
        {
            "rows": [
                {
                    "prediction_id": "11111111-1111-1111-1111-111111111111",
                    **_exact_identity(direction="LESS"),
                }
            ]
        }
    )
    result = lookup_prediction_receipts(_Db([_prediction()]), batch)
    row = result["rows"][0]
    assert row["status"] == "BLOCKED"
    assert row["code"] == "PREDICTION_RECEIPT_IDENTITY_CONFLICT"
    assert row["detail"]["conflicting_fields"] == ["direction"]


def test_prediction_id_with_matching_identity_succeeds():
    batch = PredictionReceiptLookupBatch.model_validate(
        {
            "rows": [
                {
                    "prediction_id": "11111111-1111-1111-1111-111111111111",
                    **_exact_identity(),
                }
            ]
        }
    )
    result = lookup_prediction_receipts(_Db([_prediction()]), batch)
    assert result["rows_matched"] == 1
    assert result["rows"][0]["code"] == "IMMUTABLE_PREGAME_PREDICTION_MATCHED"


def test_fallback_requires_complete_event_player_stat_line_direction_identity():
    batch = PredictionReceiptLookupBatch.model_validate(
        {
            "rows": [
                {
                    "event_id": "MLB:2026-09-13:BOS-KC",
                    "player": "Payton Tolle",
                    "stat_type": "PITCHER_STRIKEOUTS",
                }
            ]
        }
    )
    result = lookup_prediction_receipts(_Db([_prediction()]), batch)
    row = result["rows"][0]
    assert result["rows_blocked"] == 1
    assert row["code"] == "PREDICTION_RECEIPT_LOOKUP_IDENTITY_INSUFFICIENT"
    assert row["detail"]["missing_identity_fields"] == ["line", "direction"]


def test_materially_different_line_does_not_match_full_fallback_identity():
    batch = PredictionReceiptLookupBatch.model_validate(
        {"rows": [{**_exact_identity(line=3.5), "sport": "MLB"}]}
    )
    result = lookup_prediction_receipts(_Db([_prediction()]), batch)
    assert result["rows_not_found"] == 1
    assert result["rows"][0]["code"] == "IMMUTABLE_PREGAME_PREDICTION_NOT_FOUND"


def test_ambiguous_exact_identity_rows_are_not_guessed():
    second = _prediction(
        prediction_id="33333333-3333-3333-3333-333333333333",
        created_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )
    batch = PredictionReceiptLookupBatch.model_validate(
        {"rows": [{**_exact_identity(), "sport": "MLB"}]}
    )
    result = lookup_prediction_receipts(_Db([_prediction(), second]), batch)
    assert result["rows_ambiguous"] == 1
    assert result["rows"][0]["code"] == "MULTIPLE_PREDICTION_RECEIPTS_MATCH"


def test_lookup_route_is_read_only_and_installs_once():
    app = FastAPI()
    db = _Db([_prediction()])
    dep = Depends(lambda: None)
    install_prediction_receipt_lookup_route(app, db_client_fn=lambda: db, auth_dependency=dep)
    install_prediction_receipt_lookup_route(app, db_client_fn=lambda: db, auth_dependency=dep)
    assert (
        sum(
            getattr(route, "path", None) == "/v17/prediction-receipts/lookup"
            for route in app.router.routes
        )
        == 1
    )

    client = TestClient(app)
    response = client.post(
        "/v17/prediction-receipts/lookup",
        json={"rows": [{"prediction_id": "11111111-1111-1111-1111-111111111111"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rows_matched"] == 1
    assert body["can_execute"] is False


# NFL outright event forecasts have a different immutable ledger from scalar props.
# A held model record is useful for verification, not a publication approval.
NFL_EVENT_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _nfl_event(**overrides):
    now = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)
    base = {
        "event_prediction_id": NFL_EVENT_UUID,
        "official_event_id": "2026_09_TB_CHI",
        "event_key": "NFL:2026_09_TB_CHI",
        "created_at": (now - timedelta(minutes=2)).isoformat(),
        "event_start_time_utc": (now + timedelta(hours=3)).isoformat(),
        "model_timestamp": (now - timedelta(minutes=3)).isoformat(),
        "home_team": "Chicago Bears",
        "away_team": "Tampa Bay Buccaneers",
        "selected_participant": "Chicago Bears",
        "opponent": "Tampa Bay Buccaneers",
        "model_family": "NFL_OUTRIGHT_WIN_LOGREG_V1",
        "model_version": "NFL_TEST_ARTIFACT_V1",
        "calibrated_selection_probability": 0.61,
        "calibrated_selection_lower_bound": 0.54,
        "calibrated_selection_upper_bound": 0.68,
        "model_package_valid": True,
        "provenance_complete": True,
        "model_probability_publishable": True,
        "blend_publishable": False,
        "can_execute": False,
    }
    base.update(overrides)
    return base


class _DualLedgerDb:
    def __init__(self, *, props=(), nfl=()):
        self.props = list(props)
        self.nfl = list(nfl)
        self.called = []

    def table(self, name):
        self.called.append(name)
        if name == "wow_predictions":
            return _Query(self.props)
        if name == "wow_nfl_event_predictions":
            return _Query(self.nfl)
        raise AssertionError(f"unexpected ledger: {name}")


def test_nfl_event_uuid_lookup_returns_immutable_model_evidence_without_release():
    db = _DualLedgerDb(nfl=[_nfl_event()])
    batch = PredictionReceiptLookupBatch.model_validate({"rows": [{
        "row_key": "nfl-win", "prediction_id": NFL_EVENT_UUID,
        "sport": "NFL", "market_family": "OUTRIGHT_WINNER",
        "event_id": "2026_09_TB_CHI", "team": "Chicago Bears",
        "opponent": "Tampa Bay Buccaneers",
    }]})
    out = lookup_prediction_receipts(db, batch)
    assert out["rows_matched"] == 1 and out["reconciliation_pass"] is True
    assert db.called == ["wow_nfl_event_predictions"]
    match = out["rows"][0]["matches"][0]
    assert match["governed_prediction_id"] == NFL_EVENT_UUID
    assert match["governed_prediction_table"] == "wow_nfl_event_predictions"
    assert match["is_immutable_pregame"] is True
    assert match["calibrated_probability"] == 0.61
    assert match["calibrated_lower_bound"] == 0.54
    assert match["model_probability_publishable"] is True
    assert match["blend_publishable"] is False
    assert match["probability_publishable"] is False
    assert match["rank_eligible"] is False
    assert match["terminal_label"] is None
    assert out["can_execute"] is False


def test_nfl_receipt_conflict_does_not_guess_event_or_side():
    db = _DualLedgerDb(nfl=[_nfl_event()])
    batch = PredictionReceiptLookupBatch.model_validate({"rows": [{
        "prediction_id": NFL_EVENT_UUID, "sport": "NFL",
        "market_family": "OUTRIGHT_WINNER",
        "event_id": "2026_09_GB_NE", "team": "Tampa Bay Buccaneers",
        "direction": "MORE",
    }]})
    out = lookup_prediction_receipts(db, batch)
    row = out["rows"][0]
    assert row["status"] == "BLOCKED"
    assert row["code"] == "PREDICTION_RECEIPT_IDENTITY_CONFLICT"
    assert row["detail"]["conflicting_fields"] == ["event_id", "team", "direction"]
    assert row["matches"] == []


def test_nfl_receipt_requires_explicit_sport_and_exact_uuid():
    db = _DualLedgerDb(nfl=[_nfl_event()])
    batch = PredictionReceiptLookupBatch.model_validate({"rows": [
        {"sport": "NFL", "market_family": "OUTRIGHT_WINNER",
         "event_id": "2026_09_TB_CHI"},
        {"prediction_id": NFL_EVENT_UUID, "sport": "NBA",
         "market_family": "OUTRIGHT_WINNER"},
    ]})
    out = lookup_prediction_receipts(db, batch)
    assert out["rows_blocked"] == 2
    assert out["rows"][0]["code"] == "PREDICTION_RECEIPT_LOOKUP_IDENTITY_INSUFFICIENT"
    assert out["rows"][1]["code"] == "MODEL_ROUTE_UNSUPPORTED"
    assert db.called == []


def test_nfl_event_malformed_or_post_start_receipt_cannot_prove_pregame():
    game = _nfl_event(model_timestamp="2026-11-03T00:00:00+00:00")
    db = _DualLedgerDb(nfl=[game])
    batch = PredictionReceiptLookupBatch.model_validate({"rows": [{
        "prediction_id": NFL_EVENT_UUID, "sport": "NFL",
        "market_family": "OUTRIGHT_WINNER",
    }]})
    out = lookup_prediction_receipts(db, batch)
    assert out["rows"][0]["status"] == "MATCHED"
    assert out["rows"][0]["code"] == "PREDICTION_MATCHED_NOT_PROVEN_IMMUTABLE_PREGAME"
    assert out["rows"][0]["matches"][0]["is_immutable_pregame"] is False
    assert out["can_execute"] is False


def test_nfl_event_uuid_missing_is_not_a_fake_prop_or_model_absence():
    db = _DualLedgerDb(nfl=[])
    batch = PredictionReceiptLookupBatch.model_validate({"rows": [{
        "prediction_id": NFL_EVENT_UUID, "sport": "NFL",
        "market_family": "OUTRIGHT_WINNER",
    }]})
    out = lookup_prediction_receipts(db, batch)
    assert out["rows_not_found"] == 1
    assert out["rows"][0]["code"] == "IMMUTABLE_PREGAME_PREDICTION_NOT_FOUND"
    assert db.called == ["wow_nfl_event_predictions"]


def test_nfl_event_uuid_collision_is_not_silently_deduplicated():
    db = _DualLedgerDb(nfl=[_nfl_event(), _nfl_event()])
    batch = PredictionReceiptLookupBatch.model_validate({"rows": [{
        "prediction_id": NFL_EVENT_UUID, "sport": "NFL",
        "market_family": "OUTRIGHT_WINNER",
    }]})
    out = lookup_prediction_receipts(db, batch)
    assert out["rows_ambiguous"] == 1
    assert out["rows"][0]["matches"][0]["governed_prediction_table"] == "wow_nfl_event_predictions"


def test_existing_nfl_player_prop_receipt_stays_in_prop_ledger():
    prop = _prediction(sport="NFL")
    db = _DualLedgerDb(props=[prop], nfl=[_nfl_event()])
    batch = PredictionReceiptLookupBatch.model_validate({"rows": [{
        "prediction_id": "11111111-1111-1111-1111-111111111111", "sport": "NFL",
    }]})
    out = lookup_prediction_receipts(db, batch)
    assert out["rows_matched"] == 1
    assert db.called == ["wow_predictions"]
    assert out["rows"][0]["matches"][0]["governed_prediction_table"] == "wow_predictions"
