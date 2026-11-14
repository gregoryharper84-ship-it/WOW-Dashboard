import logging

import pytest

import v17_synthetic_self_acceptance as mod


def test_prop_payload_targets_unsupported_sport_with_no_certified_model():
    payload = mod._prop_payload(event_start="2026-09-03T00:00:00+00:00")
    row = payload["rows"][0]
    assert row["sport"] == "TABLE_TENNIS"
    assert row["stat_type"] == "ACES"
    assert row["direction"] == "MORE"
    assert row["line"] == 2.5


def test_team_event_payload_targets_unsupported_sport_with_no_certified_model():
    payload = mod._team_event_payload(event_date="2026-09-03")
    row = payload["rows"][0]
    assert row["sport"] == "TABLE_TENNIS"
    assert row["league"] == "TEST_LEAGUE"
    assert row["price_required_for_objective"] is False


@pytest.mark.anyio
async def test_missing_api_key_fails_closed_without_any_http_call(monkeypatch):
    monkeypatch.delenv("WOW_ACTION_API_KEY", raising=False)
    logger = logging.getLogger("test-v17-synthetic-self-acceptance")
    result = await mod.run_v17_synthetic_self_acceptance(logger)
    assert result == {"status": "FAILED", "code": "WOW_ACTION_API_KEY_MISSING", "can_execute": False}


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, responses, *, timeout=None):
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, headers, json):
        return self._responses.pop(0)


def _patch_client(monkeypatch, responses):
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(responses, **kwargs))


def _projected_candidate():
    return {
        "official_event_id": "824226",
        "official_date": "2026-09-09",
        "event_start_time": "2026-09-10T00:10:00+00:00",
        "home_team": "Home Team",
        "away_team": "Away Team",
        "snapshot_id": "snapshot-824226",
        "snapshot_timestamp": "2026-09-09T13:30:00+00:00",
    }


@pytest.mark.anyio
async def test_both_scenarios_failing_closed_reports_pass(monkeypatch, caplog):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "test-key")
    _patch_client(
        monkeypatch,
        [
            _FakeResponse(200, {
                "rows": [{
                    "row_key": "TEST-PROP-001", "terminal_status": "HELD",
                    "terminal_label": "MODEL_UNAVAILABLE", "probability_publishable": False,
                    "can_execute": False,
                }],
            }),
            _FakeResponse(200, {
                "rows": [{
                    "research_run_id": "TEST-EVENT-001-RUN", "terminal_status": "HELD",
                    "code": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False,
                }],
            }),
        ],
    )
    logger = logging.getLogger("test-v17-synthetic-self-acceptance")
    result = await mod.run_v17_synthetic_self_acceptance(logger)
    assert result["status"] == "PASS"
    assert result["status_scope"] == "SELF_ACCEPTANCE_DIAGNOSTICS_ONLY"
    assert result["prop_ok"] is True
    assert result["team_event_ok"] is True
    assert result["can_execute"] is False
    # Synthetic negatives may PASS without proving real scoring acceptance.
    assert result["scoring_acceptance_proven"] is False
    assert result["synthetic_negative_probes"]["status"] == "PASS"
    assert result["source_backed_pregame_scoring"]["status"] == "BLOCKED"
    assert result["source_backed_pregame_scoring"]["blocker"] == (
        "NO_ELIGIBLE_SOURCE_BACKED_PREGAME_CANDIDATE"
    )
    # Production log must not confuse synthetic negative-probe PASS with a
    # certified fitted numerical scoring receipt.
    diagnostic_lines = [
        record.getMessage() for record in caplog.records
        if "WOW_V17_SYNTHETIC_SELF_ACCEPTANCE status=PASS" in record.getMessage()
    ]
    assert len(diagnostic_lines) == 1
    assert "status_scope=SELF_ACCEPTANCE_DIAGNOSTICS_ONLY" in diagnostic_lines[0]
    assert "scoring_acceptance_proven=False" in diagnostic_lines[0]
    assert "synthetic_negative_status=PASS" in diagnostic_lines[0]
    assert "source_backed_status=BLOCKED" in diagnostic_lines[0]
    assert "source_backed_lane=SOURCE_BACKED_PREGAME_SCORING" in diagnostic_lines[0]
    assert "source_backed_blocker=NO_ELIGIBLE_SOURCE_BACKED_PREGAME_CANDIDATE" in diagnostic_lines[0]


@pytest.mark.anyio
async def test_a_fabricated_probability_would_fail_this_acceptance_check(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "test-key")
    _patch_client(
        monkeypatch,
        [
            _FakeResponse(200, {
                "rows": [{
                    "row_key": "TEST-PROP-001", "terminal_status": "COMPLETED",
                    "terminal_label": "MODEL_QUALIFIED_HOLD", "probability_publishable": True,
                    "can_execute": False,
                }],
            }),
            _FakeResponse(200, {
                "rows": [{
                    "research_run_id": "TEST-EVENT-001-RUN", "terminal_status": "HELD",
                    "code": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False,
                }],
            }),
        ],
    )
    logger = logging.getLogger("test-v17-synthetic-self-acceptance")
    result = await mod.run_v17_synthetic_self_acceptance(logger)
    assert result["status"] == "FAIL"
    assert result["prop_ok"] is False


@pytest.mark.anyio
async def test_projected_422_preserves_typed_backend_diagnostics(monkeypatch):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "test-key")
    monkeypatch.setattr(mod, "_real_projected_mlb_candidate", lambda now: _projected_candidate())
    _patch_client(
        monkeypatch,
        [
            _FakeResponse(200, {
                "rows": [{
                    "row_key": "TEST-PROP-001", "terminal_status": "HELD",
                    "terminal_label": "MODEL_UNAVAILABLE", "probability_publishable": False,
                    "can_execute": False,
                }],
            }),
            _FakeResponse(200, {
                "rows": [{
                    "research_run_id": "TEST-EVENT-001-RUN", "terminal_status": "HELD",
                    "code": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False,
                }],
            }),
            _FakeResponse(422, {
                "detail": {
                    "code": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                    "blocker_code": "MLB_TEAM_EVENT_CANONICAL_EVIDENCE_UNAVAILABLE",
                    "failure_class": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                    "missing_fields": ["away_lineup_status", "home_lineup_status"],
                    "probability_publishable": False,
                    "can_execute": False,
                }
            }),
        ],
    )
    logger = logging.getLogger("test-v17-synthetic-self-acceptance")
    result = await mod.run_v17_synthetic_self_acceptance(logger)

    assert result["status"] == "FAIL"
    assert result["projected_lineup_http_status"] == 422
    assert result["projected_lineup_code"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert result["projected_lineup_blocker_code"] == "MLB_TEAM_EVENT_CANONICAL_EVIDENCE_UNAVAILABLE"
    assert result["projected_lineup_failure_class"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert result["projected_lineup_missing_fields"] == ["away_lineup_status", "home_lineup_status"]
    assert result["projected_lineup_failed_checks"] == ["http_status"]
    assert result["projected_lineup_code"] != "MODEL_UNAVAILABLE"
    assert result["can_execute"] is False
    assert result["scoring_acceptance_proven"] is False
    assert result["source_backed_pregame_scoring"]["status"] == "BLOCKED"
    assert result["source_backed_pregame_scoring"]["blocker"] == (
        "MLB_TEAM_EVENT_CANONICAL_EVIDENCE_UNAVAILABLE"
    )
    assert result["source_backed_pregame_scoring"]["official_event_id"] == "824226"
    assert result["source_backed_pregame_scoring"]["source_snapshot_id"] == "snapshot-824226"


@pytest.mark.anyio
@pytest.mark.parametrize("prediction_id", [None, "claimed-id-is-not-persistence-proof"])
async def test_source_backed_projected_pass_emits_trace_receipt_not_synthetic_claim(monkeypatch, prediction_id):
    monkeypatch.setenv("WOW_ACTION_API_KEY", "test-key")
    monkeypatch.setattr(mod, "_real_projected_mlb_candidate", lambda now: _projected_candidate())
    _patch_client(
        monkeypatch,
        [
            _FakeResponse(200, {
                "rows": [{
                    "row_key": "TEST-PROP-001", "terminal_status": "HELD",
                    "terminal_label": "MODEL_UNAVAILABLE", "probability_publishable": False,
                    "can_execute": False,
                }],
            }),
            _FakeResponse(200, {
                "rows": [{
                    "research_run_id": "TEST-EVENT-001-RUN", "terminal_status": "HELD",
                    "code": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False,
                }],
            }),
            _FakeResponse(200, {
                "code": "LINEUP_PROJECTED_PROBABILITY_AVAILABLE",
                "lineup_state": "PROJECTED_HIGH_CONFIDENCE",
                "calibrated_home_probability": 0.55,
                "calibrated_away_probability": 0.45,
                "calibrated_home_lower_bound": 0.51,
                "calibrated_away_lower_bound": 0.41,
                "sporting_probability_publishable": True,
                "probability_publishable": True,
                "rank_eligible": False,
                "terminal_label": "MODEL_QUALIFIED_HOLD",
                "global_terminal_authority": "V17_TERMINAL_REDUCER",
                "final_refresh_required": True,
                "blockers": ["LINEUP_CONFIRMATION_PENDING"],
                "can_execute": False,
                "request_id": "V17-PROJECTED-LINEUP-SELF-ACCEPTANCE",
                "candidate_envelope": {
                    "research_run_id": "V17-PROJECTED-LINEUP-SELF-ACCEPTANCE",
                    "official_event_id": "824226",
                    "source_snapshot_id": "snapshot-824226",
                },
                "prediction_id": prediction_id,
            }),
        ],
    )
    result = await mod.run_v17_synthetic_self_acceptance(
        logging.getLogger("test-v17-synthetic-self-acceptance")
    )
    assert result["status"] == "PASS"
    assert result["synthetic_negative_probes"]["status"] == "PASS"
    assert result["scoring_acceptance_proven"] is False
    assert result["status_scope"] == "SELF_ACCEPTANCE_DIAGNOSTICS_ONLY"
    scoring = result["source_backed_pregame_scoring"]
    assert scoring["status"] == "HELD"
    assert scoring["lane"] == "SOURCE_BACKED_PROJECTED_TEAM_EVENT"
    assert scoring["request_id"] == "V17-PROJECTED-LINEUP-SELF-ACCEPTANCE"
    assert scoring["official_event_id"] == "824226"
    assert scoring["source_snapshot_id"] == "snapshot-824226"
    assert scoring["fitted_route"] == "score-team-event"
    assert scoring["calibrated_lower_bound"] == 0.41
    assert scoring["calibrated_lower_bound_scope"] == "CONSERVATIVE_TWO_SIDE_MIN"
    assert scoring["reducer_authority"] == "V17_TERMINAL_REDUCER"
    assert scoring["immutable_prediction_id"] == prediction_id
    assert scoring["blocker"] == "LINEUP_CONFIRMATION_PENDING"
    assert result["can_execute"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("corruption", [
    "research_run_id", "official_event_id", "source_snapshot_id",
    "missing_envelope", "rejected_terminal",
])
async def test_projected_diagnostics_reject_wrong_identity_or_terminal(monkeypatch, corruption):
    """A 200 projected response cannot attest a different event or a rejection."""
    monkeypatch.setenv("WOW_ACTION_API_KEY", "test-key")
    monkeypatch.setattr(mod, "_real_projected_mlb_candidate", lambda now: _projected_candidate())
    projected = {
        "code": "LINEUP_PROJECTED_PROBABILITY_AVAILABLE",
        "lineup_state": "PROJECTED_HIGH_CONFIDENCE",
        "calibrated_home_probability": 0.55,
        "calibrated_away_probability": 0.45,
        "calibrated_home_lower_bound": 0.51,
        "calibrated_away_lower_bound": 0.41,
        "sporting_probability_publishable": True,
        "probability_publishable": True,
        "rank_eligible": False,
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "final_refresh_required": True,
        "blockers": ["LINEUP_CONFIRMATION_PENDING"],
        "can_execute": False,
        "candidate_envelope": {
            "research_run_id": "V17-PROJECTED-LINEUP-SELF-ACCEPTANCE",
            "official_event_id": "824226",
            "source_snapshot_id": "snapshot-824226",
        },
    }
    if corruption == "missing_envelope":
        del projected["candidate_envelope"]
    elif corruption == "rejected_terminal":
        projected["terminal_label"] = "REJECTED"
    else:
        projected["candidate_envelope"][corruption] = "OTHER-REQUEST"
    _patch_client(monkeypatch, [
        _FakeResponse(200, {"rows": [{"row_key": "TEST-PROP-001", "terminal_status": "HELD",
            "terminal_label": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False}]}),
        _FakeResponse(200, {"rows": [{"research_run_id": "TEST-EVENT-001-RUN", "terminal_status": "HELD",
            "code": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False}]}),
        _FakeResponse(200, projected),
    ])
    result = await mod.run_v17_synthetic_self_acceptance(
        logging.getLogger("test-v17-synthetic-self-acceptance")
    )
    assert result["status"] == "FAIL"
    assert result["projected_lineup_ok"] is False
    assert result["source_backed_pregame_scoring"]["status"] == "BLOCKED"
    assert result["source_backed_pregame_scoring"]["blocker"] == "SOURCE_BACKED_PREGAME_SCORING_FAILED"
    assert result["source_backed_pregame_scoring"]["request_id"] is None
    assert result["scoring_acceptance_proven"] is False
    assert result["can_execute"] is False


@pytest.mark.anyio
async def test_stronger_purge_is_diagnostic_success_but_never_a_lineup_hold(monkeypatch, caplog):
    """A reducer purge may prove model diagnostics without granting a HELD row."""
    monkeypatch.setenv("WOW_ACTION_API_KEY", "test-key")
    monkeypatch.setattr(mod, "_real_projected_mlb_candidate", lambda now: _projected_candidate())
    projected = {
        "code": "LINEUP_PROJECTED_PROBABILITY_AVAILABLE",
        "lineup_state": "PROJECTED_HIGH_CONFIDENCE",
        "calibrated_home_probability": 0.55,
        "calibrated_away_probability": 0.45,
        "calibrated_home_lower_bound": 0.51,
        "calibrated_away_lower_bound": 0.41,
        "sporting_probability_publishable": True,
        "probability_publishable": True,
        "rank_eligible": False,
        "terminal_label": "SLATE_PURGE",
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "final_refresh_required": True,
        "blockers": ["LINEUP_CONFIRMATION_PENDING"],
        "can_execute": False,
        "candidate_envelope": {
            "research_run_id": "V17-PROJECTED-LINEUP-SELF-ACCEPTANCE",
            "official_event_id": "824226",
            "source_snapshot_id": "snapshot-824226",
        },
    }
    _patch_client(monkeypatch, [
        _FakeResponse(200, {"rows": [{"row_key": "TEST-PROP-001", "terminal_status": "HELD",
            "terminal_label": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False}]}),
        _FakeResponse(200, {"rows": [{"research_run_id": "TEST-EVENT-001-RUN", "terminal_status": "HELD",
            "code": "MODEL_UNAVAILABLE", "probability_publishable": False, "can_execute": False}]}),
        _FakeResponse(200, projected),
    ])
    result = await mod.run_v17_synthetic_self_acceptance(
        logging.getLogger("test-v17-synthetic-self-acceptance")
    )
    assert result["projected_lineup_ok"] is True
    assert result["status_scope"] == "SELF_ACCEPTANCE_DIAGNOSTICS_ONLY"
    assert result["source_backed_pregame_scoring"]["status"] == "BLOCKED"
    assert result["source_backed_pregame_scoring"]["blocker"] == "SLATE_PURGE"
    assert result["scoring_acceptance_proven"] is False
    assert result["can_execute"] is False
    assert any(
        "source_backed_status=BLOCKED" in record.getMessage()
        and "source_backed_blocker=SLATE_PURGE" in record.getMessage()
        and "scoring_acceptance_proven=False" in record.getMessage()
        for record in caplog.records
    )


def test_startup_readback_never_infers_qualified_scoring(caplog):
    logger = logging.getLogger("test-v17-startup-readback")
    result = {
        "status": "PASS",
        "status_scope": "SELF_ACCEPTANCE_DIAGNOSTICS_ONLY",
        "scoring_acceptance_proven": False,
        "source_backed_pregame_scoring": {
            "status": "HELD",
            "lane": "SOURCE_BACKED_PROJECTED_TEAM_EVENT",
            "blocker": "LINEUP_CONFIRMATION_PENDING",
            "calibrated_lower_bound": 0.41,
        },
    }
    mod.log_v17_synthetic_startup_readback(logger, result)
    line = next(
        item.getMessage() for item in caplog.records
        if item.getMessage().startswith("WOW_V17_SYNTHETIC_STARTUP_READBACK ")
    )
    assert "status=PASS" in line
    assert "status_scope=SELF_ACCEPTANCE_DIAGNOSTICS_ONLY" in line
    assert "scoring_acceptance_proven=False" in line
    assert "source_backed_status=HELD" in line
    assert "source_backed_lane=SOURCE_BACKED_PROJECTED_TEAM_EVENT" in line
    assert "source_backed_blocker=LINEUP_CONFIRMATION_PENDING" in line
    assert "can_execute=false" in line
    assert "0.41" not in line  # Do not leak numeric forecast from a held row.


def test_startup_readback_missing_result_never_claims_scoring(caplog):
    logger = logging.getLogger("test-v17-startup-empty")
    mod.log_v17_synthetic_startup_readback(logger, {"status": "FAILED"})
    line = next(
        item.getMessage() for item in caplog.records
        if item.getMessage().startswith("WOW_V17_SYNTHETIC_STARTUP_READBACK ")
    )
    assert "status_scope=DIAGNOSTIC_NOT_COMPLETED" in line
    assert "scoring_acceptance_proven=False" in line
    assert "source_backed_status=NOT_ATTEMPTED" in line
    assert "source_backed_blocker=NO_SOURCE_BACKED_SCORING_PROOF" in line
