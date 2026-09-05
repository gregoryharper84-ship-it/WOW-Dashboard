from pathlib import Path

import yaml

import pick_request_runtime as runtime


def test_v17_pick_request_schema_does_not_require_host_historical_evidence():
    schema_path = Path(__file__).parent / "v17" / "openapi.wow-betting-engine.v17.yaml"
    schema = yaml.safe_load(schema_path.read_text())
    row_schema = schema["components"]["schemas"]["PickRequestRow"]
    assert "evidence" not in row_schema.get("required", [])


def test_minimal_mlb_pitcher_k_row_is_valid_without_evidence():
    batch = runtime.PickRequestBatch.model_validate(
        {
            "request_id": "skubal-minimal-contract-regression",
            "rows": [
                {
                    "row_key": "skubal-k55-more",
                    "event_id": "823907",
                    "event_start_time": "2026-09-04T02:10:00+00:00",
                    "sport": "MLB",
                    "league": "MLB",
                    "player": "Tarik Skubal",
                    "stat_type": "PITCHER_STRIKEOUTS",
                    "line": 5.5,
                    "direction": "MORE",
                    "source_type": "NORMALIZED",
                    "platform": "PrizePicks",
                }
            ],
        }
    )
    row = batch.rows[0]
    assert row.evidence is None
    assert runtime._canonical_stat(row.sport, row.stat_type) == "PITCHER_STRIKEOUTS"
