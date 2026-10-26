"""Nonpublishing NHL total-goals exact-source model-input integration tests."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import pytest

from v17.nhl_total_goals_challenger import TotalRow
from v17.nhl_total_goals_model_bridge import (
    CAN_EXECUTE, PROBABILITY_PUBLISHABLE, NHLTotalsModelBridgeError,
    build_regime_model_research_rows,
)
from v17.nhl_total_goals_regime_source import (
    normalize_official_regime_schedule, reconcile_regime_labels_with_d1_events,
)


def inputs():
    # Three authentic event/source histories but SYNTHETIC feature vectors.
    # This validates the join contract only, NOT genuine pregame reconstruction.
    root = Path(__file__).parent / "tests" / "fixtures"
    game_file = root / "nhl_totals_d1_schedule_join_three_games.json"
    schedule_file = root / "nhl_official_schedule_regimes_NYR_20242025.json"
    games = json.loads(game_file.read_text())["rows"]
    schedule = json.loads(schedule_file.read_text())["official_completed_samples"]
    events = normalize_official_regime_schedule(
        {"games": schedule}, expected_season=20242025, requested_team="NYR"
    )
    verified_receipt = reconcile_regime_labels_with_d1_events(games, events)
    rows = []
    for g in games:
        start = datetime.fromisoformat(g["event_start_time"].replace("Z", "+00:00"))
        rows.append(TotalRow(
            event_id=g["official_event_id"], start_utc=start.isoformat(),
            season_start_year=2024,
            home=g["home_participant"], away=g["away_participant"],
            total_goals=g["outcome_json"]["home_score"] + g["outcome_json"]["away_score"],
            reconstructed_as_of=(start - timedelta(minutes=1)).isoformat(),
            features=(4.0, 3.1, 3.3, 3.0, 2.0, 2.1),
            source_hash=g["source_payload_sha256"],
        ))
    return rows, verified_receipt


def test_real_official_join_yields_three_model_input_rows_without_publishability():
    rows, receipt = inputs()
    joined, audit = build_regime_model_research_rows(rows, receipt)
    assert audit["eligible_feature_rows"] == 3
    assert audit["source_labels_total"] == 3
    assert audit["source_only_ineligible_games"] == 0
    assert len(audit["joined_dataset_sha256"]) == 64
    assert audit["immutable_pregame_snapshots"] is False
    assert audit["calibrated_lower_bound"] is None
    assert audit["probability_publishable"] is False and audit["can_execute"] is False
    assert all(r.historical_reconstruction is True for r in joined)
    assert all(r.archived_pregame_snapshot is False for r in joined)
    assert {r.event_id: r.terminal_regime for r in joined} == {
        "2024020007": "REG", "2024020030": "OT", "2024020728": "SO"
    }
    assert {r.event_id: r.total_goals for r in joined} == {
        "2024020007": 6, "2024020030": 11, "2024020728": 1
    }
    assert not CAN_EXECUTE and not PROBABILITY_PUBLISHABLE


@pytest.mark.parametrize("field,value,code", [
    ("source_hash", "f"*64, "NHL_TOTALS_BRIDGE_IDENTITY_CONFLICT"),
    ("start_utc", "2024-10-10T00:00:00+00:00", "NHL_TOTALS_BRIDGE_IDENTITY_CONFLICT"),
    ("season_start_year", 2023, "NHL_TOTALS_BRIDGE_IDENTITY_CONFLICT"),
    ("total_goals", 100, "NHL_TOTALS_BRIDGE_IDENTITY_CONFLICT"),
    ("features", (1.0, 2.0), "NHL_TOTALS_BRIDGE_FEATURE_WIDTH"),
    ("historically_reconstructed", False, "NHL_TOTALS_BRIDGE_FEATURE_UNVERIFIED"),
])
def test_source_identity_and_features_never_silently_default(field,value,code):
    rows,receipt=inputs()
    rows[0]=replace(rows[0],**{field:value})
    with pytest.raises(NHLTotalsModelBridgeError) as error:
        build_regime_model_research_rows(rows,receipt)
    assert error.value.code == code


def test_poststart_features_blocked_even_when_historical_game_identifies():
    rows,receipt=inputs()
    rows[0]=replace(rows[0],reconstructed_as_of=rows[0].start_utc)
    with pytest.raises(NHLTotalsModelBridgeError) as error:
        build_regime_model_research_rows(rows,receipt)
    assert error.value.code=="NHL_TOTALS_BRIDGE_LEAKAGE"


def test_omitted_label_and_duplicate_source_inputs_fail_closed():
    rows,receipt=inputs()
    bad=dict(receipt)
    bad["labels"]=bad["labels"][1:]
    bad["count"]=2
    with pytest.raises(NHLTotalsModelBridgeError) as error:
        build_regime_model_research_rows(rows,bad)
    assert error.value.code=="NHL_TOTALS_BRIDGE_MISSING_REGIME"
    with pytest.raises(NHLTotalsModelBridgeError) as error:
        build_regime_model_research_rows(rows+[rows[0]],receipt)
    assert error.value.code=="NHL_TOTALS_BRIDGE_DUPLICATE_FEATURE"


@pytest.mark.parametrize("key,value", [
    ("probability_publishable", True),
    ("can_execute", True),
    ("historical_reconstruction", False),
    ("archived_pregame_snapshot", True),
    ("status", "FINAL_APPROVED"),
    ("model_probability", 0.9),
    ("calibrated_lower_bound", 0.8),
])
def test_approved_probability_or_fake_archive_in_receipt_refused(key,value):
    rows,receipt=inputs()
    bad=dict(receipt)
    bad[key]=value
    with pytest.raises(NHLTotalsModelBridgeError) as error:
        build_regime_model_research_rows(rows,bad)
    assert error.value.code=="NHL_TOTALS_BRIDGE_RECEIPT_INVALID"


def test_noneligible_early_game_labels_may_exist_but_no_feature_missing_label():
    rows,receipt=inputs()
    joined,audit=build_regime_model_research_rows(rows[:2],receipt)
    assert len(joined)==2
    assert audit["source_only_ineligible_games"]==1
    assert audit["eligible_feature_rows"]==2


def test_mutated_official_regime_is_not_trusted():
    rows,receipt=inputs()
    bad=dict(receipt)
    labels=[dict(x) for x in bad["labels"]]
    bad["labels"]=labels
    index=next(i for i,x in enumerate(labels) if x["final_period_type"]=="SO")
    labels[index]["final_period_type"]="UNKNOWN"
    with pytest.raises(NHLTotalsModelBridgeError) as error:
        build_regime_model_research_rows(rows,bad)
    assert error.value.code=="NHL_TOTALS_BRIDGE_REGIME_CONFLICT"
