"""Synthetic-only regression tests. No MLB licensed source bytes in fixtures."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from runpy import run_path

import pytest

_module = run_path(str(Path(__file__).resolve().parents[1] / "v17" / "mlb_hits_official_outcome_research.py"))
MLBHitsResearchHold = _module["MLBHitsResearchHold"]
extract_mlb_hits_labels = _module["extract_mlb_hits_labels"]


def fixture():
    body = {"teams": {"away": {"players": {
        "ID10": {"person": {"id": 10}, "stats": {"batting": {"hits": 0, "plateAppearances": 4}}},
        "ID11": {"person": {"id": 11}, "stats": {"batting": {"hits": 2, "plateAppearances": 5}}},
    }}, "home": {"players": {
        "ID20": {"person": {"id": 20}, "stats": {"batting": {"hits": 1, "plateAppearances": 1}}},
        "ID21": {"person": {"id": 21}, "stats": {}},
    }}}}
    return from_body(body)


def from_body(body):
    raw = json.dumps(body, sort_keys=True)
    return {"research_only": True, "can_execute": False, "http_status": 200,
            "game_pk": 123456, "source_url": "https://statsapi.mlb.com/api/v1/game/123456/boxscore",
            "official_date": "2026-10-27", "captured_at": "2026-10-28T18:00:00Z",
            "raw_body": raw, "raw_sha256": sha256(raw.encode()).hexdigest()}


def test_valid_labels_have_no_prediction_or_training_authority():
    result = extract_mlb_hits_labels(fixture())
    assert result["player_count"] == 4
    assert result["hit_pa_label_count"] == 3
    assert [(r["hits"], r["plate_appearances"], r["candidate_hit_at_least_one"])
            for r in result["rows"]] == [(0, 4, False), (2, 5, True), (1, 1, True)]
    assert all(r["pregame_feature_snapshot_id"] is None for r in result["rows"])
    assert all(r["candidate_label_status"] == "UNVERIFIED_FINAL_RESEARCH_ONLY" for r in result["rows"])
    assert result["source_authenticated"] is False
    assert all(r["source_authenticated"] is False for r in result["rows"])
    for k in ("event_start_time_verified", "game_final_independently_verified",
              "pregame_features_verified", "source_rights_verified",
              "historical_training_authorized", "probability_publishable", "can_execute"):
        assert result[k] is False


@pytest.mark.parametrize("edit,code", [
    (lambda x: x.update({"research_only": False}), "MLB_HITS_RESEARCH_SOURCE_NOT_ELIGIBLE"),
    (lambda x: x.update({"can_execute": True}), "MLB_HITS_RESEARCH_SOURCE_NOT_ELIGIBLE"),
    (lambda x: x.update({"http_status": 403}), "MLB_HITS_RESEARCH_SOURCE_NOT_ELIGIBLE"),
    (lambda x: x.update({"source_url": "https://evil.example/game/123456/boxscore"}),
     "MLB_HITS_RESEARCH_SOURCE_IDENTITY_MISMATCH"),
    (lambda x: x.update({"game_pk": 123457}), "MLB_HITS_RESEARCH_SOURCE_IDENTITY_MISMATCH"),
    (lambda x: x.update({"raw_sha256": "0" * 64}), "MLB_HITS_RESEARCH_SOURCE_HASH_MISMATCH"),
    (lambda x: x.update({"captured_at": "2026-10-27T20:00:00Z"}),
     "MLB_HITS_RESEARCH_CHRONOLOGY_UNVERIFIED"),
    (lambda x: x.update({"captured_at": "2026-10-28T20:00:00"}),
     "MLB_HITS_RESEARCH_TIMESTAMP_INVALID"),
    (lambda x: x.update({"official_date": "2026-02-30"}), "MLB_HITS_RESEARCH_DATE_INVALID"),
    (lambda x: x.update({"raw_body": "not-json", "raw_sha256": sha256(b"not-json").hexdigest()}),
     "MLB_HITS_RESEARCH_BOX_SCHEMA_INVALID"),
])
def test_snapshot_invalid_rejected_with_typed_code(edit, code):
    data = fixture()
    edit(data)
    with pytest.raises(MLBHitsResearchHold, match=code):
        extract_mlb_hits_labels(data)


@pytest.mark.parametrize("edit,code", [
    (lambda b: b["teams"]["home"]["players"].update(
        {"ID10": deepcopy(b["teams"]["away"]["players"]["ID10"])}),
     "MLB_HITS_RESEARCH_DUPLICATE_PLAYER"),
    (lambda b: b["teams"]["away"]["players"]["ID10"]["stats"]["batting"].update({"hits": 5}),
     "MLB_HITS_RESEARCH_STAT_CONTRADICTION"),
    (lambda b: b["teams"]["away"]["players"]["ID10"]["stats"]["batting"].update({"hits": True}),
     "MLB_HITS_RESEARCH_STAT_INVALID"),
    (lambda b: b["teams"]["home"]["players"]["ID20"]["person"].update({"id": 22}),
     "MLB_HITS_RESEARCH_PLAYER_ID_INVALID"),
    (lambda b: b["teams"].pop("away"), "MLB_HITS_RESEARCH_BOX_SCHEMA_INVALID"),
])
def test_schema_corruptions_or_missing_batting(edit, code):
    base = fixture()
    body = json.loads(base["raw_body"])
    edit(body)
    with pytest.raises(MLBHitsResearchHold, match=code):
        extract_mlb_hits_labels(from_body(body))


def test_missing_batting_explicitly_excluded_not_assumed_zero():
    body = json.loads(fixture()["raw_body"])
    body["teams"]["away"]["players"]["ID10"]["stats"].pop("batting")
    assert extract_mlb_hits_labels(from_body(body))["hit_pa_label_count"] == 2


def test_large_source_is_rejected_before_hashing_and_json():
    sample = fixture()
    sample["raw_body"] = "A" * 256001
    with pytest.raises(MLBHitsResearchHold, match="MLB_HITS_RESEARCH_SNAPSHOT_INVALID"):
        extract_mlb_hits_labels(sample)


def test_unpaired_surrogate_raw_body_fails_closed_with_typed_hold():
    # Malformed Python Unicode must not escape the research-only hold contract.
    sample = fixture()
    sample["raw_body"] = "\ud800"
    with pytest.raises(MLBHitsResearchHold, match="MLB_HITS_RESEARCH_SNAPSHOT_INVALID"):
        extract_mlb_hits_labels(sample)

def test_deeply_nested_json_fails_with_typed_hold(monkeypatch):
    # A small, SHA-consistent payload can exhaust JSON decoder recursion.
    # Reject with the canonical research HOLD, not an unhandled 500.
    sample = fixture()
    raw = (sample["raw_body"][:-1] + ',"unused":'
           + "[" * 12000 + "0" + "]" * 12000 + "}")
    assert len(raw.encode("utf-8")) < 256_000
    sample["raw_body"] = raw
    sample["raw_sha256"] = sha256(raw.encode("utf-8")).hexdigest()
    # A depth guard must reject BEFORE json.loads, not just catch its recursion error.
    monkeypatch.setattr(json, "loads", lambda _: pytest.fail("decoder invoked on excessive depth"))
    with pytest.raises(MLBHitsResearchHold, match="MLB_HITS_RESEARCH_BOX_SCHEMA_INVALID"):
        extract_mlb_hits_labels(sample)


def test_brackets_and_escaped_quotes_inside_json_string_are_not_nesting():
    body = json.loads(fixture()["raw_body"])
    body["teams"]["away"]["players"]["ID10"]["note"] = "[" * 1000 + 'quote " slash \\'
    result = extract_mlb_hits_labels(from_body(body))
    assert result["hit_pa_label_count"] == 3
    assert result["historical_training_authorized"] is False
