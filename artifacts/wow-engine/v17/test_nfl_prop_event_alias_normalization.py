"""Regression: the ESPN provider event id must not block NFL prop scoring.

The audit found a manual scoring attempt using ESPN id ``401873036`` while the
canonical scoring id was ``2026_08_CHI_SEA``. The alias is now rewritten only
when the hydrated evidence proves that provider id belongs to the verified
canonical event; any other mismatch still fails closed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import pick_request_runtime as runtime

ALIAS = "401873036"
CANONICAL = "2026_08_CHI_SEA"


def _row(**role):
    return SimpleNamespace(evidence=SimpleNamespace(role_status=role or None))


def _stub_validator(monkeypatch, event_id: str):
    monkeypatch.setattr(
        runtime,
        "_ORIGINAL_VALIDATE_EVIDENCE",
        lambda _row, stat: {
            "event_id": event_id,
            "sport": "NFL",
            "stat_type": stat,
            "player": "Jaxon Smith-Njigba",
        },
    )


def _role(**overrides):
    role = {
        "status": "ACTIVE_CURRENT_ESPN_ROSTER",
        "provider_event_ids": {"ESPN": ALIAS},
        "verified_canonical_event_id": CANONICAL,
    }
    role.update(overrides)
    return role


def test_provider_alias_is_normalized_to_canonical_scoring_id(monkeypatch: pytest.MonkeyPatch):
    _stub_validator(monkeypatch, ALIAS)
    normalized = runtime._validate_evidence(_row(**_role()), "RECEIVING_YARDS")

    assert normalized["event_id"] == CANONICAL
    assert normalized["event_id_alias_normalized_from"] == ALIAS


def test_canonical_id_passes_through_unchanged(monkeypatch: pytest.MonkeyPatch):
    _stub_validator(monkeypatch, CANONICAL)
    normalized = runtime._validate_evidence(_row(**_role()), "RECEIVING_YARDS")

    assert normalized["event_id"] == CANONICAL
    assert "event_id_alias_normalized_from" not in normalized


def test_unrelated_event_id_still_fails_closed(monkeypatch: pytest.MonkeyPatch):
    _stub_validator(monkeypatch, "999999999")
    with pytest.raises(ValueError, match="NFL_CANONICAL_EVENT_ID_MISMATCH"):
        runtime._validate_evidence(_row(**_role()), "RECEIVING_YARDS")


def test_bound_canonical_conflict_is_preserved(monkeypatch: pytest.MonkeyPatch):
    _stub_validator(monkeypatch, ALIAS)
    with pytest.raises(ValueError, match="CANONICAL_EVENT_ID_MISMATCH"):
        runtime._validate_evidence(
            _row(**_role(canonical_event_id="2026_08_OTHER_GAME")), "RECEIVING_YARDS"
        )


def test_alias_normalization_requires_verified_canonical_identity(monkeypatch: pytest.MonkeyPatch):
    _stub_validator(monkeypatch, ALIAS)
    role = _role(verified_canonical_event_id=None)
    with pytest.raises(ValueError, match="NFL_CANONICAL_EVENT_ID_UNVERIFIED"):
        runtime._validate_evidence(_row(**role), "RECEIVING_YARDS")
