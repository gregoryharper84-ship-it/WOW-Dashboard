"""Execute the first-six maintenance orchestrator with a fake transport.

Regression for #987: the NCAAF team-state scope derives the immutable dynamic
feature ledger from wow_ncaaf_training_games, so the NCAAF hydrate lane must be
posted before that scope or the ledger is always one hydrate behind.
"""
from __future__ import annotations

import io
import json
import textwrap
import urllib.request
from pathlib import Path

import pytest

PROGRAM = "LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1"


def _orchestrator_source() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    text = (repo_root / ".github/workflows/wow-v17-first-six-model-maintenance.yml").read_text()
    marker = "Advance governed model-development lanes and team-state challengers"
    body = text.split(marker, 1)[1].split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    return textwrap.dedent(body)


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run(monkeypatch, event_name: str, *, ncaaf_status: str = "OK") -> list[str]:
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):  # noqa: ARG001
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if "oidc" in url:
            return _Response(json.dumps({"value": "t"}).encode())
        path = url.split("https://wow.test", 1)[1]
        calls.append(path)
        if "/team-state-challenger-maintenance/" in path:
            payload = {
                "program": PROGRAM, "can_execute": False, "probability_publishable": False,
                "automatic_certification": False, "automatic_promotion": False,
                "rows": [{"can_execute": False, "probability_publishable": False}],
            }
        else:
            payload = {"status": ncaaf_status if "ncaaf" in path else "OK",
                       "code": "X" if ncaaf_status == "BLOCKED" else None,
                       "can_execute": False, "probability_publishable": False}
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    monkeypatch.setenv("WOW_ACTION_ORIGIN", "https://wow.test")
    monkeypatch.setenv("GITHUB_EVENT_NAME", event_name)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://oidc.test/token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "r")
    exec(compile(_orchestrator_source(), "first_six_orchestrator", "exec"), {"__name__": "__main__"})
    return calls


@pytest.mark.parametrize("event_name", ["schedule", "push"])
def test_ncaaf_hydrate_runs_before_ncaaf_team_state_scope(monkeypatch, event_name):
    calls = _run(monkeypatch, event_name)
    hydrate = "/internal/v17/ncaaf-model-maintenance"
    scope = "/internal/v17/team-state-challenger-maintenance/NCAAF"
    assert calls.count(hydrate) == 1
    assert calls.index(hydrate) < calls.index(scope)


def test_scheduled_run_still_posts_every_lane_exactly_once(monkeypatch):
    calls = _run(monkeypatch, "schedule")
    for path in (
        "/internal/v17/basketball-model-maintenance",
        "/internal/v17/ncaab-model-maintenance",
        "/internal/v17/soccer-model-maintenance",
        "/internal/v17/tennis-model-maintenance",
        "/internal/v17/team-state-challenger-maintenance/NFL_EVENT_V2",
        "/internal/v17/team-state-challenger-maintenance/NCAAB",
    ):
        assert calls.count(path) == 1, path
    assert calls.index("/internal/v17/team-state-challenger-maintenance/NFL_EVENT_V2") < calls.index(
        "/internal/v17/team-state-challenger-maintenance/NCAAF"
    )


def test_blocked_ncaaf_hydrate_still_fails_the_run_after_team_state(monkeypatch, capsys):
    with pytest.raises(RuntimeError, match="FIRST_SIX_ANCILLARY_MAINTENANCE_BLOCKED"):
        _run(monkeypatch, "push", ncaaf_status="BLOCKED")
    out = capsys.readouterr().out
    assert "::error title=FIRST_SIX_ANCILLARY_MAINTENANCE_BLOCKED::" in out
    assert '"NCAAF"' in out.split("::error title=FIRST_SIX_ANCILLARY_MAINTENANCE_BLOCKED::", 1)[1]


def test_pull_request_runs_cannot_cancel_live_maintenance():
    repo_root = Path(__file__).resolve().parents[2]
    text = (repo_root / ".github/workflows/wow-v17-first-six-model-maintenance.yml").read_text()
    group_line = next(line for line in text.splitlines() if line.strip().startswith("group:"))
    assert "github.event_name == 'pull_request' && github.ref || 'live'" in group_line
    assert "cancel-in-progress: true" in text
