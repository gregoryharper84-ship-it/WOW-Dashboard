"""Exact NFL final-refresh proof and monotone publication-ceiling tests.

These tests exercise evidence re-query outcomes without a live prediction write.
They do not claim a production end-to-end acceptance run.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from v17 import nfl_team_event_publication as nfl


NOW = datetime(2026, 10, 22, 15, 0, tzinfo=timezone.utc)
HASH = "a" * 64
GAME = "2026_08_CHI_DET"


def _req(**overrides):
    data = {
        "official_event_id": GAME,
        "home_team": "CHI",
        "away_team": "DET",
        "event_start_time_utc": "2026-10-23T17:00:00+00:00",
        "latest_material_update_timestamp": "2026-10-22T12:00:00+00:00",
        "source_snapshot_id": "initial-snapshot",
        "sport_specific_evidence": {
            "schedule_content_sha256": HASH,
            "canonical_home_team": "CHI",
            "canonical_away_team": "DET",
        },
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _new_snapshot(**overrides):
    data = {
        "ok": True,
        "canonical_event_id": GAME,
        "canonical_home_team": "CHI",
        "canonical_away_team": "DET",
        "canonical_source_snapshot_id": "latest-snapshot",
        "canonical_snapshot_timestamp": "2026-10-22T14:00:00+00:00",
        "schedule_content_sha256": HASH,
        "identity_resolution": "EXACT_CANONICAL_EVENT_ID",
    }
    data.update(overrides)
    return data


def _score(**overrides):
    return {"model_timestamp": "2026-10-22T13:00:00+00:00", **overrides}


def _refresh(monkeypatch, *, req=None, snapshot=None, score=None):
    monkeypatch.setattr(
        nfl,
        "resolve_nfl_team_event_evidence",
        lambda request, *, db: snapshot if snapshot is not None else _new_snapshot(),
    )
    return nfl._nfl_final_refresh(
        req if req is not None else _req(),
        score if score is not None else _score(),
        db=object(),
        now=NOW,
    )


def test_success_requires_actual_second_canonical_source_read(monkeypatch):
    calls = []
    def read(request, *, db):
        calls.append((request.official_event_id, db))
        return _new_snapshot()
    monkeypatch.setattr(nfl, "resolve_nfl_team_event_evidence", read)
    db = object()
    proof = nfl._nfl_final_refresh(_req(), _score(), db=db, now=NOW)
    assert calls == [(GAME, db)]
    assert proof["final_refresh_status"] == "PASS"
    assert proof["reasons"] == []
    assert proof["refresh_source_snapshot_id"] == "latest-snapshot"
    assert proof["can_execute"] is False
    # New capture of identical bytes is not a material model input change.
    assert proof["rerun_required"] is False


def test_changed_schedule_bytes_invalidate_and_require_rerun(monkeypatch):
    proof = _refresh(monkeypatch, snapshot=_new_snapshot(schedule_content_sha256="b" * 64))
    assert proof["final_refresh_status"] == "HOLD"
    assert "FINAL_REFRESH_MATERIAL_SOURCE_CHANGED" in proof["reasons"]
    assert proof["probability_invalidated"] is True
    assert proof["rerun_required"] is True


def test_changed_event_identity_invalidates_and_requires_rerun(monkeypatch):
    proof = _refresh(monkeypatch, snapshot=_new_snapshot(canonical_event_id="DIFFERENT"))
    assert "FINAL_REFRESH_EVENT_IDENTITY_CHANGED" in proof["reasons"]
    assert proof["rerun_required"] is True


@pytest.mark.parametrize("delta", [timedelta(seconds=0), timedelta(hours=-1)])
def test_event_not_pregame_is_held(monkeypatch, delta):
    req = _req(event_start_time_utc=(NOW + delta).isoformat())
    proof = _refresh(monkeypatch, req=req)
    assert proof["final_refresh_status"] == "HOLD"
    assert "FINAL_REFRESH_EVENT_NOT_PREGAME" in proof["reasons"]
    assert proof["probability_invalidated"] is True


def test_stale_canonical_capture_does_not_count_as_refresh(monkeypatch):
    proof = _refresh(
        monkeypatch,
        snapshot=_new_snapshot(
            canonical_snapshot_timestamp=(NOW - timedelta(hours=25)).isoformat(),
        ),
    )
    assert "FINAL_REFRESH_SOURCE_STALE_OR_INVALID" in proof["reasons"]
    assert proof["final_refresh_status"] == "HOLD"


def test_model_earlier_than_material_source_update_is_held(monkeypatch):
    proof = _refresh(monkeypatch, score=_score(model_timestamp="2026-10-21T13:00:00+00:00"))
    assert proof["final_refresh_status"] == "HOLD"
    assert "FINAL_REFRESH_MODEL_TIMESTAMP_NOT_CURRENT" in proof["reasons"]


def test_missing_original_source_provenance_is_not_recovered_by_latest_row(monkeypatch):
    proof = _refresh(monkeypatch, req=_req(source_snapshot_id="", sport_specific_evidence={}))
    assert "FINAL_REFRESH_SCORED_SOURCE_PROVENANCE_MISSING" in proof["reasons"]
    assert proof["final_refresh_status"] == "HOLD"


def test_refresh_source_failure_holds_without_turning_into_model_unavailable(monkeypatch):
    def unavailable(request, *, db):
        raise RuntimeError("service down")
    monkeypatch.setattr(nfl, "resolve_nfl_team_event_evidence", unavailable)
    proof = nfl._nfl_final_refresh(_req(), _score(), db=object(), now=NOW)
    assert proof["final_refresh_status"] == "HOLD"
    assert proof["reasons"] == ["FINAL_REFRESH_SOURCE_UNAVAILABLE"]


def test_source_identity_conflict_retains_native_source_reason(monkeypatch):
    proof = _refresh(monkeypatch, snapshot={"ok": False, "code": "NFL_CANONICAL_EVENT_IDENTITY_MISMATCH"})
    assert "FINAL_REFRESH_CANONICAL_EVENT_UNRESOLVED" in proof["reasons"]
    assert proof["source_failure_code"] == "NFL_CANONICAL_EVENT_IDENTITY_MISMATCH"


def test_ceiling_keeps_governed_probabilities_and_sql_receipt_but_removes_rank():
    incoming = {
        "code": "GOVERNED_PROBABILITY_PUBLISHED",
        "calibrated_probability": 0.6386,
        "calibrated_lower_bound": 0.5827,
        "sporting_probability_completed": True,
        "rank_eligible": True,
        "probability_publishable": True,
        "terminal_label": "FINAL_APPROVED",
        "llp_governance": {"status": "PASS", "terminal_label": "FINAL_APPROVED"},
        "can_execute": False,
    }
    blocked = nfl._apply_nfl_final_refresh_ceiling(incoming, {
        "final_refresh_status": "HOLD",
        "reasons": ["FINAL_REFRESH_SOURCE_UNAVAILABLE"],
        "probability_invalidated": False,
        "rerun_required": False,
        "can_execute": False,
    })
    assert blocked["rank_eligible"] is False
    assert blocked["probability_publishable"] is False
    assert blocked["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert blocked["calibrated_probability"] == incoming["calibrated_probability"]
    assert blocked["calibrated_lower_bound"] == incoming["calibrated_lower_bound"]
    assert blocked["llp_governance"]["status"] == "PASS"
    assert blocked["llp_governance"]["final_refresh"]["final_refresh_status"] == "HOLD"
    assert blocked["can_execute"] is False
    assert incoming["rank_eligible"] is True


def test_refresh_pass_never_upgrades_a_prior_sql_hold():
    prior = {
        "rank_eligible": False,
        "probability_publishable": False,
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "llp_governance": {"status": "HOLD"},
    }
    out = nfl._apply_nfl_final_refresh_ceiling(prior, {
        "final_refresh_status": "PASS", "reasons": [], "can_execute": False,
    })
    assert out["rank_eligible"] is False
    assert out["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert out["llp_governance"]["final_refresh"]["final_refresh_status"] == "PASS"
