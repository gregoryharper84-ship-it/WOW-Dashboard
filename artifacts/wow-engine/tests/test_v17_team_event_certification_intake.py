"""Adversarial acceptance for staged team-event certification receipts.

Only the trusted service-role registry can back an intake readiness state.
Every outcome remains dry-run, including fully verified test fixtures.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from v17.team_event_capability_manifest import (
    CERTIFIED_TEAM_EVENT_SPORTS,
    EXPECTED_TEAM_EVENT_SPORTS,
)
from v17.team_event_certification_intake import (
    CertificationIntakeError,
    catalog_integrity,
    intake,
    load_receipts,
    pending_promotions,
)


def _receipt(sport="WNBA", **changes):
    names = {
        "WNBA": "WNBA_GAME_WIN_PROBABILITY_EXPERT_V1",
        "NHL": "NHL_GAME_WIN_PROBABILITY_EXPERT_V1",
    }
    value = {
        "certification_id": f"test-{sport.lower()}-certification",
        "sport": sport,
        "league_scope": sport,
        "controlling_specialist": names[sport],
        "model_family": "TEST_FITTED_FAMILY",
        "model_version": "test-version-1",
        "artifact_id": f"test-{sport.lower()}-artifact",
        "artifact_sha256": "a" * 64,
        "feature_schema_version": "TEST_FEATURES",
        "input_contract_version": "TEST_INPUTS",
        "calibration_artifact_id": f"test-{sport.lower()}-cal",
        "calibration_sha256": "b" * 64,
        "calibration_method": "PLATT_TIME_SPLIT",
        "independent_verification_status": "PASS",
        "certification_status": "CERTIFIED",
        "certified_at": "2026-10-01T00:00:00Z",
        "can_execute": False,
    }
    value.update(changes)
    return value


def _save(root, payload, name="wnba.json"):
    (root / name).write_text(json.dumps(payload), encoding="utf-8")


class _Registry:
    def __init__(self, payload):
        self.payload = payload

    def rpc(self, operation, arguments):
        assert operation == "wow_v17_active_team_event_certification"
        assert arguments["p_sport"] == self.payload["sport"]
        assert arguments["p_league_scope"] == self.payload["league_scope"]
        return self

    def execute(self):
        return SimpleNamespace(data={"ok": True, **self.payload})


def test_clean_legacy_baseline_only_mlb(tmp_path):
    assert catalog_integrity(directory=tmp_path)["status"] == "PASS"
    assert intake("MLB", directory=tmp_path)["status"] == "CERTIFIED_LEGACY"
    assert pending_promotions(directory=tmp_path) == []


def test_every_sport_has_fail_closed_diagnostic(tmp_path):
    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        result = intake(sport, directory=tmp_path)
        assert result["status"] in ("BLOCKED", "CERTIFIED_LEGACY")
        assert result["probability_publishable"] is False
        assert result["rank_eligible"] is False
        assert result["can_execute"] is False
        assert result["catalog_mutated"] is False


def test_bare_valid_json_is_not_independent_verification(tmp_path):
    _save(tmp_path, _receipt())
    assert load_receipts(tmp_path)["WNBA"].sport == "WNBA"
    result = intake("WNBA", directory=tmp_path)
    assert result["status"] == "BLOCKED"
    assert result["blockers"] == ["TEAM_EVENT_TRUSTED_REGISTRY_PROOF_REQUIRED"]
    assert pending_promotions(directory=tmp_path) == []
    assert catalog_integrity(directory=tmp_path)["status"] == "PASS"


def test_matching_service_registry_only_makes_staged_receipt_review_ready(tmp_path):
    payload = _receipt()
    _save(tmp_path, payload)
    before = dict(CERTIFIED_TEAM_EVENT_SPORTS)
    result = intake("WNBA", directory=tmp_path, registry_client=_Registry(payload))
    assert result["status"] == "READY_FOR_GOVERNANCE_PROMOTION"
    assert result["probability_publishable"] is False
    assert pending_promotions(directory=tmp_path, registry_client=_Registry(payload)) == ["WNBA"]
    assert CERTIFIED_TEAM_EVENT_SPORTS == before


def test_invalid_json_or_tampered_sha_blocks_the_entire_inventory(tmp_path):
    _save(tmp_path, _receipt())
    (tmp_path / "corrupt.json").write_text("{invalid", encoding="utf-8")
    with pytest.raises(CertificationIntakeError) as error:
        load_receipts(tmp_path)
    assert error.value.code == "TEAM_EVENT_RECEIPT_UNREADABLE"
    assert catalog_integrity(directory=tmp_path)["status"] == "FAIL"
    (tmp_path / "corrupt.json").unlink()
    _save(tmp_path, _receipt(artifact_sha256="not-a-sha"))
    assert catalog_integrity(directory=tmp_path)["status"] == "FAIL"


def test_duplicate_sport_and_duplicate_json_keys_fail_closed(tmp_path):
    _save(tmp_path, _receipt())
    _save(tmp_path, _receipt(certification_id="different"), name="other.json")
    assert catalog_integrity(directory=tmp_path)["blockers"] == ["TEAM_EVENT_RECEIPT_DUPLICATE"]
    (tmp_path / "other.json").unlink()
    (tmp_path / "bad.json").write_text('{"sport":"WNBA","sport":"NHL"}', encoding="utf-8")
    assert catalog_integrity(directory=tmp_path)["blockers"] == ["TEAM_EVENT_RECEIPT_DUPLICATE_JSON_KEY"]


def test_unbacked_catalog_entry_fails_without_mutating_it(tmp_path, monkeypatch):
    monkeypatch.setitem(CERTIFIED_TEAM_EVENT_SPORTS, "WNBA", _receipt()["controlling_specialist"])
    assert catalog_integrity(directory=tmp_path)["blockers"] == [
        "TEAM_EVENT_LEDGER_ENTRY_WITHOUT_RECEIPT:WNBA"
    ]
    assert intake("WNBA", directory=tmp_path)["status"] == "BLOCKED"


def test_catalog_entry_with_only_unsigned_json_still_fails(tmp_path, monkeypatch):
    payload = _receipt()
    _save(tmp_path, payload)
    monkeypatch.setitem(CERTIFIED_TEAM_EVENT_SPORTS, "WNBA", payload["controlling_specialist"])
    assert catalog_integrity(directory=tmp_path)["blockers"] == [
        "TEAM_EVENT_TRUSTED_REGISTRY_PROOF_REQUIRED:WNBA"
    ]
    assert intake("WNBA", directory=tmp_path)["probability_publishable"] is False


def test_catalog_entry_with_matching_trusted_record_passes_integrity_only(tmp_path, monkeypatch):
    payload = _receipt()
    _save(tmp_path, payload)
    monkeypatch.setitem(CERTIFIED_TEAM_EVENT_SPORTS, "WNBA", payload["controlling_specialist"])
    trusted = _Registry(payload)
    assert catalog_integrity(directory=tmp_path, registry_client=trusted)["status"] == "PASS"
    result = intake("WNBA", directory=tmp_path, registry_client=trusted)
    assert result["status"] == "CERTIFIED_CATALOG_BACKED"
    assert result["rank_eligible"] is False
    assert result["probability_publishable"] is False


def test_mismatched_registry_receipt_never_becomes_governance_ready(tmp_path):
    payload = _receipt()
    _save(tmp_path, payload)
    changed = _receipt(artifact_sha256="c" * 64)
    result = intake("WNBA", directory=tmp_path, registry_client=_Registry(changed))
    assert result["status"] == "BLOCKED"
    assert result["blockers"] == ["TEAM_EVENT_RECEIPT_REGISTRY_MISMATCH"]


def test_mismatched_declared_specialist_and_future_certification_fail(tmp_path):
    _save(tmp_path, _receipt(controlling_specialist="OTHER_SPECIALIST"))
    assert catalog_integrity(directory=tmp_path)["blockers"] == [
        "TEAM_EVENT_RECEIPT_SPECIALIST_MISMATCH"
    ]
    _save(tmp_path, _receipt(certified_at="2099-01-01T00:00:00Z"))
    assert catalog_integrity(directory=tmp_path)["blockers"] == [
        "TEAM_EVENT_RECEIPT_FUTURE_CERTIFICATION"
    ]


def test_registry_unavailability_does_not_relabel_as_model_failure(tmp_path):
    _save(tmp_path, _receipt())

    class _Unavailable:
        def rpc(self, *_args, **_kwargs):
            raise RuntimeError("service not reachable")

    result = intake("WNBA", directory=tmp_path, registry_client=_Unavailable())
    assert result["status"] == "BLOCKED"
    assert result["blockers"] == ["TEAM_EVENT_CERTIFICATION_REGISTRY_UNAVAILABLE"]
    assert result["probability_publishable"] is False
