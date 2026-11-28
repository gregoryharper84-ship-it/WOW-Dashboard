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
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_ACCEPTANCE_SPORT", "baseball_mlb")
    out = tmp_path / "acceptance.json"

    rc = snapshot.main(["--require-capture", "--date", "2026-09-14", "--output", str(out)])

    assert rc == 0
    assert observed == {"sport_key": "baseball_mlb", "date": "2026-09-14"}
    assert out.exists()



def test_acceptance_fallback_skips_empty_slate_and_stops_on_first_valid_capture(monkeypatch):
    monkeypatch.setattr(snapshot, "DEFAULT_SPORTS", ("baseball_mlb", "americanfootball_nfl"))
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_ACCEPTANCE_MAX_SAMPLES", "4")
    calls = []

    def fake_collect(sport_key, *, date=None, opener=None):
        calls.append((sport_key, date))
        captured = ["RUNDOWN"] if sport_key == "americanfootball_nfl" else []
        return {
            "status": snapshot.ACCEPTANCE_DEGRADED_READY if captured else snapshot.ACCEPTANCE_BLOCKED,
            "configured_providers": ["RUNDOWN", "SHARPAPI"],
            "captured_providers": captured,
            "auth_blockers": [],
            "can_execute": False,
        }

    monkeypatch.setattr(snapshot, "collect_acceptance", fake_collect)
    report = snapshot.collect_acceptance_with_fallback(date="2026-10-05")

    assert calls == [
        ("baseball_mlb", "2026-10-05"),
        ("americanfootball_nfl", "2026-10-05"),
    ]
    assert report["captured_providers"] == ["RUNDOWN"]
    assert snapshot.acceptance_blockers(report) == []
    assert len(report["acceptance_samples"]) == 2


def test_free_core_market_entrypoints_do_not_touch_paid_provider_clients(monkeypatch):
    monkeypatch.setenv("WOW_V17_SOURCE_MODE", "FREE_CORE")
    monkeypatch.setenv("RUNDOWN_API_KEY", "fixture-not-a-real-key")
    monkeypatch.setenv("SHARPAPI_API_KEY", "fixture-not-a-real-key")
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("paid-provider-call")
        raise AssertionError("paid provider invoked in FREE_CORE")

    monkeypatch.setattr(snapshot.sources, "fetch", forbidden)
    monkeypatch.setattr(snapshot.sources, "rundown_sport_id", forbidden)
    monkeypatch.setattr(snapshot.live, "rundown_market_evidence", forbidden)
    monkeypatch.setattr(snapshot.live, "sharpapi_market_evidence", forbidden)
    monkeypatch.setattr(snapshot, "_provider_health_map", forbidden)
    for provider, capability in (("RUNDOWN", "sports"), ("RUNDOWN", "events"), ("SHARPAPI", "odds")):
        report = snapshot.probe(provider, capability, sport_key="americanfootball_nfl")
        assert report["status"] == "BLOCKED"
        assert report["reason_code"] == snapshot.source_policy.BLOCK_FREE_CORE
        assert report["paid_provider_network_attempted"] is False
    for report in (
        snapshot.collect(["americanfootball_nfl", "basketball_nba", "icehockey_nhl"]),
        snapshot.collect_acceptance("americanfootball_nfl"),
        snapshot.collect_acceptance_with_fallback(),
    ):
        assert report["status"] == "FREE_CORE_MARKET_OPTIONAL_READY"
        assert report["paid_provider_network_attempted"] is False
        assert report["prediction_authority"] is False
        assert report["can_execute"] is False
    assert calls == []


def test_nightly_workflow_disables_direct_paid_market_diagnostics():
    from pathlib import Path
    import yaml

    root = Path(__file__).resolve().parents[3]
    workflow = yaml.safe_load((root / ".github/workflows/wow-v17-nightly-multiscout.yml").read_text())
    jobs = workflow["jobs"]
    for job_id in ("market-evidence-acceptance", "nightly-discovery"):
        env = jobs[job_id]["env"]
        assert env["WOW_V17_SOURCE_MODE"] == "FREE_CORE"
        assert env["WOW_V17_PAID_CALL_BUDGET_PER_SCAN"] == "0"
        assert env["WOW_V17_PAID_FINAL_REFRESH_RESERVE"] == "0"
        assert env["WOW_MARKET_EVIDENCE_KILL_SWITCH"] == "true"
        assert "RUNDOWN_API_KEY" not in env
        assert "SHARPAPI_API_KEY" not in env
    steps = jobs["market-evidence-acceptance"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "--probe" not in commands
    assert "--require-capture" not in commands
    assert "baseball_mlb" not in commands
    assert "NOT_ATTEMPTED" in commands
    assert "paid_provider_network_attempted" in commands
