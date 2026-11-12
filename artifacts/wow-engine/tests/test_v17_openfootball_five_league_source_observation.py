"""Synthetic accounting of immutable OpenFootball observation, not a model test."""
from __future__ import annotations

import json
import re
from pathlib import Path


RECEIPT = (Path(__file__).resolve().parents[1] / "v17" / "research"
           / "source_openfootball_five_leagues_2024_25_observation_20261112.json")


def _receipt():
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


def test_source_observation_conserves_every_record_and_third_outcome():
    r = _receipt()
    assert r["source_repo_commit"] == "e6744429ee395bc86f247348c6184bb08d4eb361"
    assert len(r["datasets"]) == r["totals"]["league_files"] == 5
    assert len({row["path"] for row in r["datasets"]}) == 5
    for field in ("match_records", "with_final_score", "home_wins", "away_wins",
                  "draws", "missing_final_score", "duplicate_match_keys",
                  "hhmm_clock_records"):
        assert r["totals"][field] == sum(row[field] for row in r["datasets"])
    assert r["totals"]["with_final_score"] == sum(
        r["totals"][k] for k in ("home_wins", "away_wins", "draws")
    )
    assert r["totals"]["match_records"] == (
        r["totals"]["with_final_score"] + r["totals"]["missing_final_score"]
    )
    assert r["totals"]["match_records"] == 1752
    assert r["totals"]["with_final_score"] == 1732
    assert r["totals"]["draws"] == 434
    assert r["totals"]["missing_final_score"] == 20
    assert r["totals"]["duplicate_match_keys"] == 0
    for row in r["datasets"]:
        assert row["path"].startswith("2024-25/")
        assert re.fullmatch(r"[0-9a-f]{40}", row["github_blob_sha1"])
        assert row["with_final_score"] == (
            row["home_wins"] + row["away_wins"] + row["draws"]
        )
        assert row["match_records"] == row["with_final_score"] + row["missing_final_score"]


def test_source_observation_cannot_authorize_training_or_probability():
    r = _receipt()
    assert r["source_id"] == "OPENFOOTBALL_CC0"
    assert r["license_evidence"]["source_upstream_rights_independently_attested"] is False
    assert r["source_review_required"] is True
    for key in ("model_training_authorized", "model_artifact_generated",
                "probability_publishable", "production_verified", "can_execute"):
        assert r[key] is False
    assert r["model_probability"] is None
    assert r["calibrated_lower_bound"] is None
    assert r["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert r["missing_final_score_treatment"].startswith("QUARANTINE")
    assert "CLOCK_TIMEZONE" in r["unverified"]
    assert "ORIGINAL_PREGAME_SNAPSHOT_TIME" in r["unverified"]
    assert "INDEPENDENT_SETTLEMENT" in r["unverified"]


def test_github_source_identity_is_not_misrepresented_as_raw_bytes_entitlement():
    r = _receipt()
    assert r["acquisition_type"] == "READ_ONLY_GITHUB_CONNECTOR_DECODED_CONTENT"
    assert re.fullmatch(r"[0-9a-f]{40}", r["license_evidence"]["github_blob_sha1"])
    assert re.fullmatch(r"[0-9a-f]{64}", r["license_evidence"]["decoded_utf8_sha256"])
    epl = next(row for row in r["datasets"] if row["path"] == "2024-25/en.1.json")
    assert re.fullmatch(r"[0-9a-f]{64}", epl["decoded_utf8_sha256"])
