"""Immutable source-metadata assertions for researched OpenFootball seasons.

This test does not verify upstream rights, genuine as-of match features, official
settlement identity, fitting, calibration, or a production probability.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1] / "v17" / "research"
NEW = ROOT / "source_openfootball_fifteen_league_seasons_2022_2026_observation_20261112.json"
PREVIOUS = ROOT / "source_openfootball_five_leagues_2024_25_observation_20261112.json"
SOURCE_COMMIT = "e6744429ee395bc86f247348c6184bb08d4eb361"
LICENSE_BLOB = "670154e3538863b2d9891fd5483160fbdfc89164"
EXPECTED_BLOBS = {
    "2022-23/de.1.json": "7fc779362c4d775bc7cea2fa3b79ce1941bbc796",
    "2022-23/en.1.json": "0b6230e60d8cd7402810a5f515f6762d8b385b74",
    "2022-23/es.1.json": "f777848f48f8ec0528cc0ec67c6c05bba0bca961",
    "2022-23/fr.1.json": "5fae7b79a038f09f1ae982143cdd02dc111eb9f5",
    "2022-23/it.1.json": "b9bec73a92557da814c959b7c4f12bb501c20c7a",
    "2023-24/de.1.json": "2c41078e751d914f24bfe7e55c5567cea70a4aee",
    "2023-24/en.1.json": "f62b11dfaf06cffd1236c2777caebb880a0cfea1",
    "2023-24/es.1.json": "a9d391f61af872bb9f456951d2f71f10d9e10704",
    "2023-24/fr.1.json": "a5c5c479bd14c4581931219a41687d9938e4dc1e",
    "2023-24/it.1.json": "674079696d1298ce45abab6a1f8953d0675b995f",
    "2025-26/de.1.json": "320994ff4b376c0aa7abe7929f9b9740cfc0eff5",
    "2025-26/en.1.json": "41e223ee5954e6b634beb4bc70d7188b3183c665",
    "2025-26/es.1.json": "ca95ceb8861f8f1ae876eb066e71148ff773425b",
    "2025-26/fr.1.json": "b6eec1251b125725d32efe85c8a969b68765d26a",
    "2025-26/it.1.json": "00c57db6aa94737d6f965a67d1b89de58fcf3746",
}
QUANTITIES = (
    "match_records", "with_final_score", "missing_final_score", "home_wins",
    "away_wins", "draws", "hhmm_clock_records", "duplicate_source_match_keys",
    "invalid_participant_identity",
)


def _new():
    return json.loads(NEW.read_text(encoding="utf-8"))


def _validate_pins(receipt):
    assert receipt["source_repo_commit"] == SOURCE_COMMIT
    assert receipt["license_evidence"]["github_license_blob_sha1"] == LICENSE_BLOB
    assert {r["path"]: r["github_blob_sha1"] for r in receipt["datasets"]} == EXPECTED_BLOBS


def test_all_fifteen_immutable_source_files_have_exact_git_blob_pins():
    r = _new()
    _validate_pins(r)
    assert r["source_repo"] == "openfootball/football.json"
    assert r["acquisition_type"] == "READ_ONLY_GITHUB_CONNECTOR_DECODED_CONTENT"
    assert r["source_id"] == "OPENFOOTBALL_CC0"
    assert r["sport"] == "SOCCER"
    assert r["license_evidence"]["source_upstream_rights_independently_attested"] is False
    assert set(item["season"] for item in r["datasets"]) == {
        "2022-23", "2023-24", "2025-26"
    }
    for season in ("2022-23", "2023-24", "2025-26"):
        assert {item["league"] for item in r["datasets"]
                if item["season"] == season} == {
                    "de.1", "en.1", "es.1", "fr.1", "it.1"
                }
    assert len(r["datasets"]) == len(EXPECTED_BLOBS) == 15


@pytest.mark.parametrize(("field", "new_value"), [
    ("source_repo_commit", "0" * 40),
])
def test_tampered_source_commit_is_rejected(field, new_value):
    r = _new()
    r[field] = new_value
    with pytest.raises(AssertionError):
        _validate_pins(r)


def test_tampered_license_and_blob_hash_are_rejected():
    r = _new()
    r["license_evidence"]["github_license_blob_sha1"] = "0" * 40
    with pytest.raises(AssertionError):
        _validate_pins(r)
    r = _new()
    r["datasets"][0]["github_blob_sha1"] = "0" * 40
    with pytest.raises(AssertionError):
        _validate_pins(r)


def test_three_way_outcomes_and_missing_scores_conserve_every_record():
    r = _new()
    assert r["totals"]["match_records"] == 5330
    assert r["totals"]["with_final_score"] == 5216
    assert r["totals"]["missing_final_score"] == 114
    assert r["totals"]["draws"] == 1238
    assert r["totals"]["duplicate_source_match_keys"] == 0
    assert r["totals"]["invalid_participant_identity"] == 0
    assert r["totals"]["hhmm_clock_records"] == 5330
    assert r["three_way_outcomes_preserved"] is True
    for field in QUANTITIES:
        assert r["totals"][field] == sum(row[field] for row in r["datasets"])
    for row in r["datasets"]:
        assert row["match_records"] == row["with_final_score"] + row["missing_final_score"]
        assert row["with_final_score"] == row["home_wins"] + row["away_wins"] + row["draws"]
        assert row["duplicate_source_match_keys"] == 0
        assert row["invalid_participant_identity"] == 0


def test_four_seasons_plus_prior_receipt_reconcile_without_row_loss():
    older = json.loads(PREVIOUS.read_text(encoding="utf-8"))
    recent = _new()
    assert len(older["datasets"]) == 5
    assert len(recent["datasets"]) == 15
    assert not ({d["path"] for d in older["datasets"]} &
                {d["path"] for d in recent["datasets"]})
    assert older["source_repo_commit"] == recent["source_repo_commit"] == SOURCE_COMMIT
    assert older["totals"]["match_records"] + recent["totals"]["match_records"] == 7082
    assert older["totals"]["with_final_score"] + recent["totals"]["with_final_score"] == 6948
    assert older["totals"]["missing_final_score"] + recent["totals"]["missing_final_score"] == 134
    assert older["totals"]["draws"] + recent["totals"]["draws"] == 1672


def test_metadata_observations_never_grant_training_or_certification():
    r = _new()
    assert r["dataset_scope"] == "FIVE_LEAGUES_THREE_SEASONS_NOT_COMPLETE_HISTORY"
    assert r["missing_final_score_treatment"].startswith("QUARANTINE")
    assert "CLOCK_TIMEZONE" in r["unverified"]
    assert "ORIGINAL_PREGAME_SNAPSHOT_TIME" in r["unverified"]
    assert "SOURCE_UPSTREAM_RIGHTS" in r["unverified"]
    assert "INDEPENDENT_SETTLEMENT" in r["unverified"]
    assert r["source_review_required"] is True
    assert r["research_only"] is True
    for name in ("model_training_authorized", "model_artifact_generated",
                 "probability_publishable", "production_verified", "can_execute"):
        assert r[name] is False
    assert r["model_probability"] is None
    assert r["calibrated_lower_bound"] is None
    assert r["terminal_authority"] == "V17_TERMINAL_REDUCER"
