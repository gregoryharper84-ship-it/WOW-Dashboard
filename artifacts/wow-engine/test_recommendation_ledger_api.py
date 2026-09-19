from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from recommendation_ledger_api import install_recommendation_ledger_routes


class Mutation:
    def __init__(self, table, rows):
        self.table = table
        self.rows = rows

    def execute(self):
        self.table.rows.extend(self.rows)
        return SimpleNamespace(data=self.rows)


class Query:
    def __init__(self, table, columns):
        self.table = table
        self.columns = columns
        self.id_column = "recommendation_record_id"
        self.ids = []

    def in_(self, column, ids):
        self.id_column = column
        self.ids = ids
        return self

    def execute(self):
        return SimpleNamespace(
            data=[row for row in self.table.rows if row.get(self.id_column) in self.ids]
        )


class Table:
    def __init__(self):
        self.rows = []

    def insert(self, rows):
        return Mutation(self, rows)

    def select(self, columns):
        return Query(self, columns)


class RpcMutation:
    def __init__(self, client, params):
        self.client = client
        self.params = params

    def execute(self):
        position = self.params["p_position"]
        outcomes = self.params["p_outcomes"]
        self.client.positions.append(position)
        self.client.tables["wow_recommendation_outcomes"].rows.extend(outcomes)
        return SimpleNamespace(
            data={
                "position_reference": position["position_reference"],
                "rows_in": len(outcomes),
                "rows_persisted": len(outcomes),
                "reconciliation_pass": True,
                "can_execute": False,
            }
        )


class Client:
    def __init__(self):
        self.tables = {
            "wow_recommendation_records": Table(),
            "wow_recommendation_outcomes": Table(),
        }
        self.positions = []

    def table(self, name):
        return self.tables.setdefault(name, Table())

    def rpc(self, name, params):
        assert name == "wow_settle_recommendation_batch"
        return RpcMutation(self, params)


def app_client():
    db = Client()
    app = FastAPI()
    install_recommendation_ledger_routes(
        app, auth_dependency=Depends(lambda: None), get_client_fn=lambda: db
    )
    return TestClient(app), db


def recommendation_payload():
    return {
        "research_run_id": "run-20260830",
        "request_id": "request-1",
        "host_identity": "WOW_CUSTOM_GPT",
        "model_identity": "WOW_BETTING_ENGINE",
        "source_type": "AUTONOMOUS_DISCOVERY",
        "rows": [
            {
                "row_key": "fsu",
                "sport": "NCAAF",
                "league": "NCAAF",
                "event_id": "NCAAF:NMSU:FSU",
                "event_start_time": (
                    datetime.now(timezone.utc) + timedelta(days=1)
                ).isoformat(),
                "participant": "Florida State",
                "opponent": "New Mexico State",
                "selection": "Florida State to win",
                "terminal_label": "MODEL_QUALIFIED_HOLD",
                "blockers": ["MONEY_LANE_UNRESOLVED"],
            }
        ],
    }


def test_write_before_display_returns_durable_id():
    client, db = app_client()
    response = client.post("/record-recommendations", json=recommendation_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["display_authorized"] is True
    assert body["rows_in"] == body["rows_persisted"] == 1
    assert body["can_execute"] is False
    assert len(db.tables["wow_recommendation_records"].rows) == 1
    row = db.tables["wow_recommendation_records"].rows[0]
    assert row["capture_timing"] == "PREGAME"
    assert row["calibration_eligible"] is False


def test_started_event_is_rejected_before_write():
    client, db = app_client()
    payload = recommendation_payload()
    payload["rows"][0]["event_start_time"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    response = client.post("/record-recommendations", json=payload)
    assert response.status_code == 422
    assert db.tables["wow_recommendation_records"].rows == []


def test_publishable_row_requires_governed_prediction_link():
    client, _ = app_client()
    payload = recommendation_payload()
    payload["rows"][0]["probability_publishable"] = True
    response = client.post("/record-recommendations", json=payload)
    assert response.status_code == 422


def test_settlement_requires_existing_recommendation_and_links_result():
    client, db = app_client()
    recorded = client.post(
        "/record-recommendations", json=recommendation_payload()
    ).json()
    recommendation_id = recorded["recommendation_record_ids"][0]
    settled = client.post(
        "/settle-recommendations",
        json={
            "rows": [
                {
                    "recommendation_record_id": recommendation_id,
                    "settled_at": datetime.now(timezone.utc).isoformat(),
                    "settled_result": "WIN",
                    "official_result": "Florida State won",
                    "settlement_source": "KALSHI_SCREENSHOT",
                    "position_reference": "KALSHI-20260829-3-MARKET",
                    "position_structure": "COMBO_ALL_OR_NOTHING",
                    "underlying_market_count": 1,
                    "entry_cost": 12.98,
                    "payout": 21.31,
                    "profit_loss": 8.33,
                    "displayed_roi": 0.6422,
                }
            ]
        },
    )
    assert settled.status_code == 200
    assert settled.json()["reconciliation_pass"] is True
    outcome = db.tables["wow_recommendation_outcomes"].rows[0]
    assert outcome["attribution_status"] == "MATCHED_PREGAME_RECORD"
    assert outcome["excluded_from_calibration"] is False
    assert outcome["can_execute"] is False
    assert "profit_loss" not in outcome
    assert db.positions[0]["profit_loss"] == 8.33
    assert db.positions[0]["recommendation_record_ids"] == [recommendation_id]


def test_unknown_recommendation_cannot_be_settled():
    client, _ = app_client()
    response = client.post(
        "/settle-recommendations",
        json={
            "rows": [
                {
                    "recommendation_record_id": "00000000-0000-0000-0000-000000000001",
                    "settled_at": datetime.now(timezone.utc).isoformat(),
                    "settled_result": "WIN",
                    "settlement_source": "OPERATOR_SCREENSHOT",
                    "position_reference": "UNKNOWN-POSITION",
                    "position_structure": "SINGLE",
                    "underlying_market_count": 1,
                }
            ]
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "RECOMMENDATION_RECORD_NOT_FOUND"


def test_settlement_rejects_mixed_or_mismatched_position_economics():
    client, _ = app_client()
    recorded = client.post(
        "/record-recommendations", json=recommendation_payload()
    ).json()
    recommendation_id = recorded["recommendation_record_ids"][0]
    payload = {
        "rows": [
            {
                "recommendation_record_id": recommendation_id,
                "settled_at": datetime.now(timezone.utc).isoformat(),
                "settled_result": "WIN",
                "settlement_source": "KALSHI_SCREENSHOT",
                "position_reference": "POSITION-1",
                "position_structure": "COMBO_ALL_OR_NOTHING",
                "underlying_market_count": 2,
                "profit_loss": 8.33,
            }
        ]
    }
    response = client.post("/settle-recommendations", json=payload)
    assert response.status_code == 422


def _publishable_row(**overrides):
    row = {
        "row_key": "cole-k",
        "sport": "MLB",
        "league": "MLB",
        "event_id": "MLB:NYY:BOS:20260919",
        "event_start_time": (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(),
        "participant": "Gerrit Cole",
        "opponent": "Boston Red Sox",
        "market_family": "PROP_DISCRETE_PMF",
        "selection": "Gerrit Cole MORE 5.5 strikeouts",
        "terminal_label": "MODEL_QUALIFIED_RANK_ELIGIBLE",
        "probability_publishable": True,
        "calibrated_probability": 0.62,
        "calibrated_probability_lower_bound": 0.55,
        "governed_prediction_table": "wow_predictions",
        "governed_prediction_id": "11111111-1111-1111-1111-111111111111",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 5.5,
        "direction": "MORE",
        "final_refresh": {"status": "PASS", "checked_at": datetime.now(timezone.utc).isoformat()},
    }
    row.update(overrides)
    return row


def _matching_prediction(**overrides):
    prediction = {
        "prediction_id": "11111111-1111-1111-1111-111111111111",
        "event_id": "MLB:NYY:BOS:20260919",
        "player": "Gerrit Cole",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 5.5,
        "direction": "MORE",
        "probability_publishable": True,
        "blockers": [],
        "calibrated_probability": 0.62,
        "calibrated_probability_lower_bound": 0.55,
        "locked_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    }
    prediction.update(overrides)
    return prediction


def test_publishable_row_backed_by_exact_frozen_prediction_is_admitted():
    client, db = app_client()
    db.tables["wow_predictions"] = Table()
    db.tables["wow_predictions"].rows.append(_matching_prediction())

    payload = recommendation_payload()
    payload["rows"] = [_publishable_row()]
    response = client.post("/record-recommendations", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["governed_admitted_row_keys"] == ["cole-k"]
    assert body["governed_blocked_rows"] == []
    row = db.tables["wow_recommendation_records"].rows[0]
    assert row["probability_publishable"] is True
    assert row["calibration_eligible"] is True


def test_publishable_row_without_matching_prediction_is_persisted_but_not_admitted():
    client, db = app_client()
    db.tables["wow_predictions"] = Table()  # no matching row

    payload = recommendation_payload()
    payload["rows"] = [_publishable_row()]
    response = client.post("/record-recommendations", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["governed_admitted_row_keys"] == []
    assert len(body["governed_blocked_rows"]) == 1
    assert body["governed_blocked_rows"][0]["row_key"] == "cole-k"
    assert "PUBLICATION_NO_MATCHING_GOVERNED_PREDICTION_ROW" in body["governed_blocked_rows"][0]["blockers"]

    # The row is still durably recorded (write-before-display), but the
    # unproven publication claim is never persisted as publishable/eligible.
    assert body["rows_persisted"] == 1
    row = db.tables["wow_recommendation_records"].rows[0]
    assert row["probability_publishable"] is False
    assert row["calibration_eligible"] is False
    assert any(b.startswith("PUBLICATION_") for b in row["blockers"])


def test_publishable_row_with_mismatched_line_is_blocked_not_admitted():
    client, db = app_client()
    db.tables["wow_predictions"] = Table()
    db.tables["wow_predictions"].rows.append(_matching_prediction(line=6.5))

    payload = recommendation_payload()
    payload["rows"] = [_publishable_row()]
    response = client.post("/record-recommendations", json=payload)

    body = response.json()
    assert body["governed_admitted_row_keys"] == []
    assert "PUBLICATION_EXACT_IDENTITY_MISMATCH:line" in body["governed_blocked_rows"][0]["blockers"]
    row = db.tables["wow_recommendation_records"].rows[0]
    assert row["probability_publishable"] is False


def test_publishable_row_missing_final_refresh_evidence_is_blocked():
    client, db = app_client()
    db.tables["wow_predictions"] = Table()
    db.tables["wow_predictions"].rows.append(_matching_prediction())

    payload = recommendation_payload()
    row = _publishable_row()
    row.pop("final_refresh")
    payload["rows"] = [row]
    response = client.post("/record-recommendations", json=payload)

    body = response.json()
    assert body["governed_admitted_row_keys"] == []
    assert "PUBLICATION_FINAL_REFRESH_EVIDENCE_MISSING" in body["governed_blocked_rows"][0]["blockers"]
