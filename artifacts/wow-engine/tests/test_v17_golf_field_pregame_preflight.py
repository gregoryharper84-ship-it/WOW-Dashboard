"""PGA field input research safety: no binary/H2H substitution or early promotion."""
from datetime import datetime, timezone
import json
import hashlib
from copy import deepcopy

import pytest

from v17.golf_field_pregame_preflight import (
    GolfFieldInputBlocked, preflight_pga_field,
)


NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def _sha(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()).hexdigest()


def _fixture():
    players = ["golfer-1", "golfer-2", "golfer-3"]
    payload = {"official_tournament_id": "pga-event-10", "field_player_ids": players}
    return {
        "tour": "PGA", "source_id": "DATA_GOLF",
        "official_tournament_id": "pga-event-10",
        "source_snapshot_id": "source-10",
        "event_start_time": "2026-10-25T12:00:00Z",
        "source_timestamp": "2026-10-24T11:59:00Z",
        "source_payload": payload, "source_payload_sha256": _sha(payload),
        "entrants": [{
            "official_player_id": name, "status": "CONFIRMED",
            "feature_as_of": "2026-10-24T10:00:00Z",
            "historical_features": {"prior_strokes_gained": 0.4 + i},
        } for i, name in enumerate(players)],
    }


def _authorized(monkeypatch):
    monkeypatch.setenv("DATA_GOLF_API_KEY", "test-present")
    monkeypatch.setenv("WOW_DATA_GOLF_COMMERCIAL_TRAINING_ENTITLED", "approved")


def _blocked(snapshot, code):
    with pytest.raises(GolfFieldInputBlocked) as exc:
        preflight_pga_field(snapshot, now=NOW)
    assert exc.value.code == code


def test_exact_pga_field_only_prepares_research_inputs(monkeypatch):
    _authorized(monkeypatch)
    row = preflight_pga_field(_fixture(), now=NOW)
    assert row["field_size"] == 3
    assert row["model_probability"] is None
    assert row["status"] == "RESEARCH_EVIDENCE_PREPARED_UNCERTIFIED"
    assert row["probability_publishable"] is False
    assert row["rank_eligible"] is False
    assert row["can_execute"] is False


def test_credential_and_training_rights_both_required(monkeypatch):
    monkeypatch.delenv("DATA_GOLF_API_KEY", raising=False)
    monkeypatch.delenv("WOW_DATA_GOLF_COMMERCIAL_TRAINING_ENTITLED", raising=False)
    _blocked(_fixture(), "GOLF_TRAINING_SOURCE_ENTITLEMENT_NOT_PROVEN")


def test_lpga_requires_separately_source_pinned_tour_not_pga_alias(monkeypatch):
    _authorized(monkeypatch)
    row = _fixture()
    row["tour"] = "LPGA"
    _blocked(row, "GOLF_TOUR_SOURCE_NOT_CERTIFIED")


def test_h2h_cannot_be_misrepresented_as_tournament_field(monkeypatch):
    _authorized(monkeypatch)
    row = _fixture()
    row["entrants"] = row["entrants"][:2]
    _blocked(row, "GOLF_FIELD_OUTCOME_SPACE_INVALID")


def test_wrong_payload_hash_event_and_roster_hold(monkeypatch):
    _authorized(monkeypatch)
    row = _fixture()
    row["source_payload"]["official_tournament_id"] = "other-event"
    _blocked(row, "GOLF_SOURCE_PAYLOAD_HASH_MISMATCH")
    row["source_payload_sha256"] = _sha(row["source_payload"])
    _blocked(row, "GOLF_SOURCE_EVENT_ID_MISMATCH")
    row = _fixture()
    row["source_payload"]["field_player_ids"] = ["golfer-1", "golfer-2", "wrong"]
    row["source_payload_sha256"] = _sha(row["source_payload"])
    _blocked(row, "GOLF_SOURCE_FIELD_ROSTER_MISMATCH")


def test_duplicate_withdrawn_and_poststart_inputs_hold(monkeypatch):
    _authorized(monkeypatch)
    row = _fixture()
    row["entrants"][2]["official_player_id"] = "golfer-1"
    _blocked(row, "GOLF_DUPLICATE_ENTRANT_ID")
    row = _fixture()
    row["entrants"][1]["status"] = "WITHDRAWN"
    _blocked(row, "GOLF_WITHDRAWAL_OR_FIELD_STATUS_UNRESOLVED")
    row = _fixture()
    row["entrants"][1]["feature_as_of"] = "2026-10-26T00:00:00Z"
    _blocked(row, "GOLF_FEATURE_TEMPORAL_LEAKAGE")


def test_market_prices_never_become_historical_fitted_features(monkeypatch):
    _authorized(monkeypatch)
    row = _fixture()
    row["entrants"][0]["historical_features"]["implied_probability"] = 0.5
    _blocked(row, "GOLF_MARKET_FEATURE_FORBIDDEN")
    row = _fixture()
    row["entrants"][0]["historical_features"]["prior_strokes_gained"] = float("nan")
    _blocked(row, "GOLF_FEATURE_NONFINITE_OR_UNTYPED")
