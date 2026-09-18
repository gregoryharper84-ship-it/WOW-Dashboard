from __future__ import annotations

from v17 import market_evidence_snapshot as snapshot
from v17 import market_evidence_sources as sources


def _ok(provider: str, capability: str, rows: int = 1) -> sources.MarketEvidenceResult:
    return sources.MarketEvidenceResult(
        True,
        provider,
        capability,
        data=[{"id": f"{provider.lower()}-{i}", "bookmakers": []} for i in range(rows)],
        status=200,
        code="MARKET_EVIDENCE_NORMALISED",
        observed_at="2026-09-14T22:00:00Z",
    )


def _blocked(provider: str, capability: str, status: int, code: str) -> sources.MarketEvidenceResult:
    return sources.MarketEvidenceResult(
        False,
        provider,
        capability,
        status=status,
        code=code,
        observed_at="2026-09-14T22:00:00Z",
    )


def _both_configured(monkeypatch):
    monkeypatch.setattr(
        snapshot,
        "_provider_health_map",
        lambda: {
            "RUNDOWN": {"provider": "RUNDOWN", "credential_configured": True},
            "SHARPAPI": {"provider": "SHARPAPI", "credential_configured": True},
        },
    )
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_429_RETRIES", "0")


def test_bounded_acceptance_is_ready_with_one_valid_provider_and_one_degraded_provider(monkeypatch):
    _both_configured(monkeypatch)
    calls = {"rundown": 0, "sharp": 0}

    def rundown(*args, **kwargs):
        calls["rundown"] += 1
        return _ok("RUNDOWN", "events", rows=3)

    def sharp(*args, **kwargs):
        calls["sharp"] += 1
        return _blocked("SHARPAPI", "odds", 400, "SHARPAPI_HTTP_400")

    monkeypatch.setattr(snapshot.live, "rundown_market_evidence", rundown)
    monkeypatch.setattr(snapshot.live, "sharpapi_market_evidence", sharp)

    report = snapshot.collect_acceptance("baseball_mlb", date="2026-09-14")

    assert calls == {"rundown": 1, "sharp": 1}
    assert report["status"] == snapshot.ACCEPTANCE_DEGRADED_READY
    assert report["ready_for_market_evidence"] is True
    assert report["captured_providers"] == ["RUNDOWN"]
    assert report["capture_failures"] == ["SHARPAPI"]
    assert report["provider_degradation"]["SHARPAPI"][0]["degradation_class"] == "SOURCE_ACQUISITION_DEGRADED"
    assert snapshot.acceptance_blockers(report) == []
    assert report["affects_fitted_model_availability"] is False
    assert report["prediction_authority"] is False
    assert report["can_execute"] is False
    assert report["secret_values_exposed"] is False


def test_auth_rejection_remains_a_hard_acceptance_blocker(monkeypatch):
    _both_configured(monkeypatch)
    monkeypatch.setattr(
        snapshot.live,
        "rundown_market_evidence",
        lambda *a, **k: _blocked("RUNDOWN", "events", 401, "RUNDOWN_HTTP_401"),
    )
    monkeypatch.setattr(snapshot.live, "sharpapi_market_evidence", lambda *a, **k: _ok("SHARPAPI", "odds"))

    report = snapshot.collect_acceptance("baseball_mlb", date="2026-09-14")

    assert report["status"] == snapshot.ACCEPTANCE_BLOCKED
    assert report["auth_blockers"] == ["RUNDOWN"]
    assert snapshot.acceptance_blockers(report) == ["AUTH_REJECTED:RUNDOWN"]


def test_zero_capture_from_all_configured_providers_fails_closed(monkeypatch):
    _both_configured(monkeypatch)
    monkeypatch.setattr(
        snapshot.live,
        "rundown_market_evidence",
        lambda *a, **k: _blocked("RUNDOWN", "events", 429, "RUNDOWN_HTTP_429"),
    )
    monkeypatch.setattr(
        snapshot.live,
        "sharpapi_market_evidence",
        lambda *a, **k: _blocked("SHARPAPI", "odds", 400, "SHARPAPI_HTTP_400"),
    )

    report = snapshot.collect_acceptance("baseball_mlb", date="2026-09-14")

    assert report["status"] == snapshot.ACCEPTANCE_BLOCKED
    assert report["provider_degradation"]["RUNDOWN"][0]["degradation_class"] == "RATE_LIMITED"
    assert snapshot.acceptance_blockers(report) == ["NO_PROVIDER_CAPTURE"]


def test_unconfigured_provider_is_not_sampled_or_reported_as_failure(monkeypatch):
    monkeypatch.setattr(
        snapshot,
        "_provider_health_map",
        lambda: {
            "RUNDOWN": {"provider": "RUNDOWN", "credential_configured": True},
            "SHARPAPI": {"provider": "SHARPAPI", "credential_configured": False},
        },
    )
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_429_RETRIES", "0")
    monkeypatch.setattr(snapshot.live, "rundown_market_evidence", lambda *a, **k: _ok("RUNDOWN", "events"))

    def sharp_must_not_run(*args, **kwargs):
        raise AssertionError("unconfigured SharpAPI must not be called")

    monkeypatch.setattr(snapshot.live, "sharpapi_market_evidence", sharp_must_not_run)

    report = snapshot.collect_acceptance("baseball_mlb", date="2026-09-14")

    assert report["configured_providers"] == ["RUNDOWN"]
    assert report["capture_failures"] == []
    assert report["status"] == snapshot.ACCEPTANCE_READY
    assert snapshot.acceptance_blockers(report) == []


def test_require_capture_cli_uses_bounded_acceptance_contract(monkeypatch, tmp_path):
    payload = {
        "status": snapshot.ACCEPTANCE_DEGRADED_READY,
        "configured_providers": ["RUNDOWN", "SHARPAPI"],
        "captured_providers": ["RUNDOWN"],
        "auth_blockers": [],
        "can_execute": False,
    }
    observed = {}

    def fake_collect(sport_key, *, date=None, opener=None):
        observed["sport_key"] = sport_key
        observed["date"] = date
        return payload

    monkeypatch.setattr(snapshot, "collect_acceptance", fake_collect)
    # This unit test owns only CLI routing/serialization. Provider-auth parity is
    # covered separately by test_v17_market_evidence_acceptance_auth_contract.py.
    monkeypatch.setattr(snapshot, "_enable_research_market_evidence", lambda: None)
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_ACCEPTANCE_SPORT", "baseball_mlb")
    out = tmp_path / "acceptance.json"

    rc = snapshot.main(["--require-capture", "--date", "2026-09-14", "--output", str(out)])

    assert rc == 0
    assert observed == {"sport_key": "baseball_mlb", "date": "2026-09-14"}
    assert out.exists()
