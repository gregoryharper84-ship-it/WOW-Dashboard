from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

import v17.daily_snapshot_oidc_bridge as bridge
from v17 import daily_snapshot_runtime as daily_runtime


class _Prod:
    event_api = object()

    def __init__(self):
        self.db = object()

    def get_client(self):
        return self.db


class _MarketApi:
    def __init__(self):
        self.prod = _Prod()


def _route(app: FastAPI):
    return next(
        route
        for route in app.router.routes
        if getattr(route, "path", None) == bridge.INTERNAL_DAILY_SNAPSHOT_ROUTE
    )


def test_internal_contract_covers_full_mlb_slate_without_widening_public_contract():
    internal = bridge.InternalDailySnapshotRequest(
        requested_slate_date="2026-09-15",
        requested_timezone="America/Chicago",
        lanes=["MONEYLINE"],
        max_team_events=24,
    )
    assert internal.max_team_events == 24
    assert bridge.INTERNAL_MAX_TEAM_EVENTS == 32

    with pytest.raises(ValidationError):
        bridge.InternalDailySnapshotRequest(
            requested_slate_date="2026-09-15",
            requested_timezone="America/Chicago",
            max_team_events=33,
        )

    with pytest.raises(ValidationError):
        daily_runtime.DailySnapshotRequest(
            requested_slate_date="2026-09-15",
            requested_timezone="America/Chicago",
            max_team_events=13,
        )


def test_bridge_installs_once_and_uses_server_owned_runtime(monkeypatch):
    app = FastAPI()
    market_api = _MarketApi()
    captured = {}

    monkeypatch.setattr(
        bridge,
        "authorize_action_key_or_multiscout_oidc",
        lambda authorization: "GITHUB_ACTIONS_OIDC",
    )

    def fake_run(req, *, db, market_api: object, event_api):
        captured.update(req=req, db=db, market_api=market_api, event_api=event_api)
        return {
            "run_id": "v17-daily-test",
            "run_status": "COMPLETED",
            "rows": [],
            "reconciliation": {"balanced": True},
            "probability_publishable": False,
            "can_execute": False,
        }

    monkeypatch.setattr(daily_runtime, "run_daily_snapshot", fake_run)

    assert bridge.install_daily_snapshot_oidc_bridge(app=app, market_api=market_api) is True
    assert bridge.install_daily_snapshot_oidc_bridge(app=app, market_api=market_api) is True
    assert sum(
        getattr(route, "path", None) == bridge.INTERNAL_DAILY_SNAPSHOT_ROUTE
        for route in app.router.routes
    ) == 1

    req = bridge.InternalDailySnapshotRequest(
        requested_slate_date="2026-09-15",
        requested_timezone="America/Chicago",
        lanes=["MONEYLINE"],
        max_team_events=24,
    )
    result = _route(app).endpoint(req, authorization="Bearer oidc-token")

    assert captured["req"] is req
    assert captured["db"] is market_api.prod.db
    assert captured["market_api"] is market_api
    assert captured["event_api"] is market_api.prod.event_api
    assert result["automation_auth"] == "GITHUB_ACTIONS_OIDC"
    assert result["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_bridge_fails_closed_on_invalid_automation_auth(monkeypatch):
    app = FastAPI()
    market_api = _MarketApi()
    bridge.install_daily_snapshot_oidc_bridge(app=app, market_api=market_api)

    def fail(_authorization):
        raise bridge.GitHubOIDCValidationError("bad-token")

    monkeypatch.setattr(bridge, "authorize_action_key_or_multiscout_oidc", fail)
    req = bridge.InternalDailySnapshotRequest(
        requested_slate_date="2026-09-15",
        requested_timezone="America/Chicago",
    )

    with pytest.raises(HTTPException) as caught:
        _route(app).endpoint(req, authorization="Bearer invalid")

    assert caught.value.status_code == 401
    assert caught.value.detail == {
        "code": "DAILY_SNAPSHOT_AUTOMATION_AUTH_INVALID",
        "can_execute": False,
    }


def test_bridge_cannot_upgrade_execution_or_probability_claim(monkeypatch):
    app = FastAPI()
    market_api = _MarketApi()
    monkeypatch.setattr(
        bridge,
        "authorize_action_key_or_multiscout_oidc",
        lambda authorization: "GITHUB_ACTIONS_OIDC",
    )
    monkeypatch.setattr(
        daily_runtime,
        "run_daily_snapshot",
        lambda *args, **kwargs: {
            "run_id": "v17-daily-held",
            "run_status": "COMPLETED_WITH_ACQUISITION_BLOCKERS",
            "rows": [],
            "probability_publishable": False,
            "can_execute": True,
        },
    )
    bridge.install_daily_snapshot_oidc_bridge(app=app, market_api=market_api)

    req = bridge.InternalDailySnapshotRequest(
        requested_slate_date="2026-09-15",
        requested_timezone="America/Chicago",
    )
    result = _route(app).endpoint(req, authorization="Bearer oidc-token")

    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert result["global_terminal_authority"] == "V17_TERMINAL_REDUCER"


def test_one_existing_thursday_daily_slot_is_reassigned_to_nfl_future_capture():
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[3]
        / ".github/workflows/wow-v17-daily-snapshot.yml"
    ).read_text(encoding="utf-8")
    # 4 scans/day stays 4 scans/day; Thursday 17 UTC is a separately
    # identifiable schedule, not an extra trigger or duplicated request.
    assert 'cron: "0 17 * * 0,1,2,3,5,6"' in workflow
    assert 'cron: "0 17 * * 4"' in workflow
    assert 'cron: "0 17 * * *"' not in workflow
    assert "WOW_NFL_NEXT_SUNDAY_CAPTURE:" in workflow
    assert "github.event.schedule == '0 17 * * 4'" in workflow
    assert 'os.environ.get("GITHUB_EVENT_NAME") == "schedule"' in workflow
    assert 'os.environ.get("WOW_NFL_NEXT_SUNDAY_CAPTURE") == "true"' in workflow
    assert 'days=(6 - today.weekday()) % 7' in workflow
    assert '"response_mode": "FULL" if next_sunday_capture else "COMPACT"' in workflow
    assert '"max_team_events": 24' in workflow
    # The existing independent post-deploy acceptance gate is NOT repurposed
    # as a scheduler. Its exact protected call condition remains unchanged.
    assert "if: inputs.post_deploy_acceptance == true" in workflow


def test_weekly_nfl_capture_requires_real_pregame_prediction_receipts():
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[3]
        / ".github/workflows/wow-v17-daily-snapshot.yml"
    ).read_text(encoding="utf-8")
    assert 'if next_sunday_capture:' in workflow
    assert 'NFL_FUTURE_PROSPECTIVE_CAPTURE_INSUFFICIENT' in workflow
    assert 'NFL_FUTURE_PREGAME_IDENTITY_INVALID' in workflow
    assert 'detail.get("prediction_id")' in workflow
    assert 'len(future_nfl_predictions) < 5' in workflow
    assert 'event_time <= receipt_checked_at_utc' in workflow
    assert '"nfl_future_prediction_receipts"' in workflow
    assert '"model_terminal_certification": "NOT_PROVEN_BY_PROSPECTIVE_CAPTURE"' in workflow
    assert '"probability_values_logged": False' in workflow


@pytest.mark.parametrize("persisted_receipts,expected_error", [
    (5, None),
    (4, "NFL_FUTURE_PROSPECTIVE_CAPTURE_INSUFFICIENT"),
])
def test_weekly_nfl_script_requires_persisted_not_merely_discovered(
    monkeypatch, capsys, persisted_receipts, expected_error,
):
    """Execute the actual workflow Python with mocked OIDC/HTTPS, no network."""
    import io
    import json
    import textwrap
    import urllib.request
    from datetime import datetime, time, timezone
    from pathlib import Path
    from zoneinfo import ZoneInfo

    workflow = (
        Path(__file__).resolve().parents[3]
        / ".github/workflows/wow-v17-daily-snapshot.yml"
    ).read_text(encoding="utf-8")
    daily_job = workflow.split("  daily-snapshot:", 1)[1].split(
        "  nfl-full-slate-exact-once-acceptance:", 1
    )[0]
    script = textwrap.dedent(
        daily_job.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    )
    compiled = compile(script, "wow-v17-daily-snapshot:inline", "exec")

    for key, value in {
        "GITHUB_EVENT_NAME": "schedule",
        "WOW_NFL_NEXT_SUNDAY_CAPTURE": "true",
        "WOW_USER_TIMEZONE": "America/Chicago",
        "WOW_ACTION_ORIGIN": "https://test.example",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.test.example/token",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "fixture-only",
    }.items():
        monkeypatch.setenv(key, value)

    called = []

    def fake_urlopen(request, timeout=None):
        if request.full_url.startswith("https://oidc.test.example/token"):
            return io.BytesIO(json.dumps({"value": "fixture-oidc"}).encode())
        assert request.full_url == "https://test.example/internal/v17/daily-snapshot"
        assert request.get_method() == "POST"
        body = json.loads(request.data.decode())
        called.append(body)
        assert body["max_team_events"] == 24
        assert body["response_mode"] == "FULL"
        assert body["lanes"] == ["MONEYLINE"]
        assert body["requested_timezone"] == "America/Chicago"
        sunday = datetime.fromisoformat(body["requested_slate_date"]).date()
        kickoff = datetime.combine(
            sunday, time(23, 59, tzinfo=ZoneInfo("America/Chicago"))
        ).astimezone(timezone.utc).isoformat()
        rows = [
            {
                "lane": "MONEYLINE",
                "identity": {
                    "sport": "NFL",
                    "official_event_id": f"nfl-future-event-{i}",
                    "commence_time_utc": kickoff,
                },
                "can_execute": False,
                "result": {"detail": {
                    "model_version": "FIXTURE_FITTED_SPECIALIST",
                    **({"prediction_id": f"immutable-pred-{i}"}
                       if i < persisted_receipts else {}),
                }},
            }
            for i in range(5)
        ]
        output = {
            "run_id": "fixture-daily",
            "run_status": "COMPLETED",
            "requested_slate_date": body["requested_slate_date"],
            "rows": rows,
            "reconciliation": {
                "balanced": True,
                "rows_completed": 0,
                "rows_held": 5,
                "rows_rejected": 0,
            },
            "lane_reconciliation": {
                "MONEYLINE": {"requested_row_limit": 24},
            },
            "cross_sport_discovery_audit": {
                "reconciliation": {"row_reconciliation": "PASS"},
            },
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "automation_auth": "GITHUB_ACTIONS_OIDC",
            "can_execute": False,
        }
        return io.BytesIO(json.dumps(output).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    if expected_error:
        with pytest.raises(AssertionError, match=expected_error):
            exec(compiled, {"__name__": "__main__"})
    else:
        exec(compiled, {"__name__": "__main__"})
        receipt = json.loads(capsys.readouterr().out.strip())
        assert receipt["capture_mode"] == "NFL_NEXT_SUNDAY"
        assert receipt["nfl_future_prediction_receipts"] == 5
        assert receipt["model_terminal_certification"] == "NOT_PROVEN_BY_PROSPECTIVE_CAPTURE"
        assert receipt["probability_values_logged"] is False
        assert receipt["can_execute"] is False
    assert len(called) == 1
