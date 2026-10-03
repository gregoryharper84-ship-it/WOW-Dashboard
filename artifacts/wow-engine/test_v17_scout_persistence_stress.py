from __future__ import annotations

import json

import pytest

from v17 import scout_persistence_stress as stress


def evidence(i: int) -> dict:
    return {
        "bookmaker": f"book-{i % 5}",
        "market_key": "h2h",
        "outcome_name": f"team-{i % 2}",
        "price": -110,
        "link": f"https://example.invalid/{i}",
    }


def candidate(event: str, rows: int = 20) -> dict:
    return {
        "can_execute": False,
        "sport_key": "basketball_nba",
        "official_event_id": event,
        "route": "LLP_TEAM_BETTING_ENGINE",
        "market_evidence": [evidence(i) for i in range(rows)],
    }


def test_10x_simulation_reconciles_every_request_and_final_candidate():
    rows = [candidate("evt-1", 40), candidate("evt-2", 40)]
    payloads = stress.build_payloads(
        rows,
        multiplier=10,
        max_evidence_rows=20,
        max_bytes=256 * 1024,
    )
    result = stress.run_stress(
        payloads,
        concurrency=8,
        multiplier=10,
        transport=lambda payload: {"ok": True, "can_execute": False},
    )
    assert result.requests == len(payloads)
    assert result.successes == result.requests
    assert result.errors == 0
    assert result.evidence_rows == 800
    assert result.candidates_finalized == 20
    assert result.multiplier == 10
    assert result.concurrency == 8
    assert result.as_dict()["can_execute"] is False
    assert result.as_dict()["terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_production_supabase_host_is_forbidden():
    with pytest.raises(ValueError, match="PRODUCTION_STRESS_TARGET_FORBIDDEN"):
        stress._validate_staging_url(
            "https://iczfhsmjrrafhvcpmqhr.supabase.co/functions/v1/wow-v17-scout-brain-persist"
        )


def test_unapproved_remote_host_is_forbidden(monkeypatch):
    monkeypatch.delenv("WOW_SCOUT_STRESS_ALLOWED_HOST", raising=False)
    with pytest.raises(ValueError, match="STRESS_HOST_NOT_EXPLICITLY_ALLOWED"):
        stress._validate_staging_url("https://staging.example.com/persist")


def test_explicit_staging_host_is_allowed(monkeypatch):
    monkeypatch.setenv("WOW_SCOUT_STRESS_ALLOWED_HOST", "staging.example.com")
    stress._validate_staging_url("https://staging.example.com/persist")


def test_failed_transport_is_counted_not_hidden():
    payloads = stress.build_payloads(
        [candidate("evt-1", 5)],
        multiplier=2,
        max_evidence_rows=10,
        max_bytes=256 * 1024,
    )
    calls = {"n": 0}

    def transport(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("synthetic timeout")
        return {"ok": True, "can_execute": False}

    result = stress.run_stress(
        payloads,
        concurrency=1,
        multiplier=2,
        transport=transport,
    )
    assert result.requests == 2
    assert result.errors == 1
    assert result.successes == 1
