"""Adversarial source/terminal gates for #2536. No synthetic probability."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from v17.model_evidence_bundle_contract import ModelBundleScope
from v17.model_evidence_runtime_gate import (
    TRIAD_CERTIFICATION_AMBIGUOUS,
    TRIAD_CERTIFICATION_READ_FAILED,
    TRIAD_CERTIFICATION_UNAVAILABLE,
    TRIAD_DB_UNAVAILABLE,
    TRIAD_READER_FAILED,
    TRIAD_READER_INVALID,
    TRIAD_SCOPE_IDENTITY_UNAVAILABLE,
    TriadBoundaryVerdict,
    assess_prop_action_boundary,
    assess_scoring_boundary,
    assess_team_event_boundary,
)
from v17 import team_event_bridge_runtime as bridge
from prop_terminal_reducer_v2 import reduce_prop_terminal


def _scope() -> ModelBundleScope:
    return ModelBundleScope(
        model_kind="TEAM_EVENT",
        sport="MLB",
        model_family="TEST_CERTIFIED_FITTED",
        model_artifact_version="test-model-v1",
        feature_schema_version="test-schema-v1",
        official_event_id="official-event-1",
        event_start_time="2026-11-14T18:00:00Z",
        prediction_timestamp="2026-11-08T14:00:00Z",
        market_family="OUTRIGHT_WINNER",
    )


def _assessment(scope: ModelBundleScope, **overrides):
    values = {
        "model_kind": scope.model_kind,
        "sport": scope.sport,
        "model_family": scope.model_family,
        "model_artifact_version": scope.model_artifact_version,
        "status": "READY_FOR_INDEPENDENT_REVIEW",
        "bundle_complete": True,
        "blockers": [],
        "probability_publishable": False,
        "can_execute": False,
    }
    values.update(overrides)
    return {"assessment": values, "component_sources": {}, "missing_component_fields": {}}


def _certificate(**changes):
    row = {
        "sport": "MLB",
        "controlling_specialist": "TEST_CERTIFIED_SPECIALIST",
        "model_family": "TEST_CERTIFIED_FITTED",
        "model_version": "test-model-v1",
        "feature_schema_version": "test-schema-v1",
        "artifact_sha256": "a" * 64,
        "certification_status": "CERTIFIED",
        "independent_verification_status": "PASS",
    }
    row.update(changes)
    return row


class _Rows:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def table(self, name):
        assert name == "wow_team_event_specialist_certifications"
        return self

    def select(self, _fields):
        return self

    def eq(self, name, value):
        self.filters.append((name, value))
        return self

    def execute(self):
        if isinstance(self.rows, Exception):
            raise self.rows
        return SimpleNamespace(data=self.rows)


def _team_request():
    return SimpleNamespace(
        sport="MLB", league="MLB", official_event_id="official-event-1",
        event_start_time_utc="2026-11-14T18:00:00Z",
        latest_material_update_timestamp=None,
    )


def _registration(scorer=None):
    return bridge.TeamEventBridgeRegistration(
        sport="MLB", adapter_name="TEST", controlling_specialist="TEST_CERTIFIED_SPECIALIST",
        required_inputs=(), scorer=scorer or (lambda *a, **k: {"ok": True}),
        standard_package_validation=False,
    )


def test_missing_client_and_reader_are_typed_nonpromotable_holds():
    scope = _scope()
    hold = assess_scoring_boundary(None, scope, reader=lambda db, scope: _assessment(scope))
    assert hold.blocker == TRIAD_DB_UNAVAILABLE
    assert hold.as_hold()["model_invoked"] is False
    assert hold.as_hold()["probability_publishable"] is False
    assert hold.as_hold()["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    # Noncallable readers are infrastructure/contract holds, not model absence.
    assert assess_scoring_boundary(object(), scope, reader=object()).blocker == TRIAD_READER_INVALID


@pytest.mark.parametrize("response", [
    True,
    "PASS",
    {"status": "READY_FOR_INDEPENDENT_REVIEW"},
    {"assessment": {"bundle_complete": True}},
])
def test_reader_cannot_pass_with_unverifiable_or_malformed_response(response):
    hold = assess_scoring_boundary(object(), _scope(), reader=lambda _db, _scope: response)
    assert hold.blocker == TRIAD_READER_INVALID
    assert hold.can_execute is False


def test_reader_error_is_distinct_from_evidence_failure():
    def broken_reader(_db, _scope):
        raise TimeoutError("no registry access")

    hold = assess_scoring_boundary(object(), _scope(), reader=broken_reader)
    assert hold.blocker == TRIAD_READER_FAILED
    assert "no registry access" not in str(hold.as_hold())


def test_exact_readonly_bundle_can_only_be_ready_for_independent_review():
    scope = _scope()
    verdict = assess_scoring_boundary(object(), scope, reader=lambda _db, _scope: _assessment(scope))
    assert verdict.status == "READY_FOR_INDEPENDENT_REVIEW"
    assert verdict.blocker is None
    assert verdict.probability_publishable is False
    assert verdict.rank_eligible is False
    assert verdict.can_execute is False


def test_reader_identity_swap_is_not_accepted():
    scope = _scope()
    verdict = assess_scoring_boundary(
        object(), scope,
        reader=lambda _db, _scope: _assessment(scope, sport="NBA"),
    )
    assert verdict.blocker == TRIAD_READER_INVALID


def test_reader_preserves_exact_typed_component_blocker():
    scope = _scope()
    verdict = assess_scoring_boundary(
        object(), scope,
        reader=lambda _db, _scope: _assessment(
            scope, status="BLOCKED", bundle_complete=False,
            blockers=["MODEL_BUNDLE_CALIBRATOR_MISSING", "MODEL_BUNDLE_PREGAME_CORPUS_MISSING"],
        ),
    )
    assert verdict.blocker == "MODEL_BUNDLE_CALIBRATOR_MISSING"
    assert verdict.blockers == (
        "MODEL_BUNDLE_CALIBRATOR_MISSING", "MODEL_BUNDLE_PREGAME_CORPUS_MISSING",
    )
    assert verdict.as_hold()["model_evaluated"] is False


@pytest.mark.parametrize("rows,expected", [
    ([], TRIAD_CERTIFICATION_UNAVAILABLE),
    ([_certificate(), _certificate()], TRIAD_CERTIFICATION_AMBIGUOUS),
    ([_certificate(independent_verification_status="PENDING")], TRIAD_CERTIFICATION_UNAVAILABLE),
    ([_certificate(model_version="")], TRIAD_SCOPE_IDENTITY_UNAVAILABLE),
    (RuntimeError("db offline"), TRIAD_CERTIFICATION_READ_FAILED),
])
def test_team_certification_identity_is_exact_and_fails_closed(rows, expected):
    db = _Rows(rows)
    verdict = assess_team_event_boundary(
        _team_request(), event_api=SimpleNamespace(get_client=lambda: db),
        registration=_registration(), reader=lambda _db, _scope: _assessment(_scope),
    )
    assert verdict.blocker == expected
    assert verdict.model_invoked is False


def test_team_scope_is_from_server_owned_certificate_not_user_guess():
    db = _Rows([_certificate()])
    observed = []

    def reader(client, scope):
        observed.append((client, scope))
        return _assessment(scope)

    verdict = assess_team_event_boundary(
        _team_request(), event_api=SimpleNamespace(get_client=lambda: db),
        registration=_registration(), reader=reader,
    )
    assert verdict.status == "READY_FOR_INDEPENDENT_REVIEW"
    assert observed[0][0] is db
    assert observed[0][1].model_family == "TEST_CERTIFIED_FITTED"
    assert ("sport", "MLB") in db.filters
    assert ("controlling_specialist", "TEST_CERTIFIED_SPECIALIST") in db.filters


def test_team_scorer_is_not_invoked_after_triad_hold(monkeypatch):
    invoked = []

    def scorer(*a, **kw):
        invoked.append(True)
        return {"probability_publishable": True}

    registration = _registration(scorer)
    monkeypatch.setitem(bridge.TEAM_EVENT_BRIDGES, "MLB", registration)
    monkeypatch.setattr(
        bridge, "assess_team_event_boundary",
        lambda *a, **kw: TriadBoundaryVerdict(
            status="BLOCKED", blocker="MODEL_BUNDLE_PREGAME_CORPUS_MISSING",
            blockers=("MODEL_BUNDLE_PREGAME_CORPUS_MISSING",),
        ),
    )
    with pytest.raises(HTTPException) as exc:
        bridge.score_registered_team_event_request(
            _team_request(), event_api=SimpleNamespace(),
        )
    assert exc.value.detail["code"] == "MODEL_BUNDLE_PREGAME_CORPUS_MISSING"
    assert exc.value.detail["model_invoked"] is False
    assert invoked == []


def test_prop_action_missing_server_player_identity_fails_before_reader():
    row = SimpleNamespace(
        sport="NBA", event_id="evt-1", event_start_time="2026-11-14T18:00:00Z",
        direction="OVER", line=7.0, player="Stephen Curry",
    )
    verdict = assess_prop_action_boundary(
        row, market_api=SimpleNamespace(), route={"model_family": "NBA"},
        canonical_stat="THREE_POINTERS_MADE",
    )
    assert verdict.blocker == TRIAD_SCOPE_IDENTITY_UNAVAILABLE
    assert verdict.model_invoked is False


def test_terminal_reducer_keeps_triads_separate_from_model_unavailable():
    decision = reduce_prop_terminal(
        proposed_label="MODEL_EVIDENCE_TRIAD_BLOCKED",
        blockers=("MODEL_BUNDLE_PREGAME_CORPUS_INVALID_OR_UNVERIFIED",),
        model_evaluated=False,
    )
    assert decision.terminal_label == "MODEL_EVIDENCE_TRIAD_BLOCKED"
    assert decision.verdict_class == "MODEL_EVIDENCE_TRIAD_BLOCKED"
    assert decision.infrastructure_blocked is True
    assert decision.model_evaluated is False
    assert decision.pick_rejected is False
