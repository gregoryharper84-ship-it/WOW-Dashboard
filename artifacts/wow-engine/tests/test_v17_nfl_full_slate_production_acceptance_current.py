from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/wow-v17-daily-snapshot.yml"


def _acceptance_section() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    marker = "nfl-full-slate-exact-once-acceptance:"
    assert marker in text
    return text[text.index(marker):]


def test_nfl_production_acceptance_uses_full_moneyline_and_exceeds_batch_ceiling():
    section = _acceptance_section()
    assert "WOW_NFL_ACCEPTANCE_SLATE_DATE" not in section
    assert 'WOW_NFL_ACCEPTANCE_LOOKAHEAD_DAYS: "42"' in section
    assert "select_future_nfl_acceptance_slate" in section
    assert "ESPN_SCOREBOARD_IDENTITY_ONLY" in section
    assert "candidate.weekday() != 6" in section
    assert '"lanes": ["MONEYLINE"]' in section
    assert '"max_team_events": 32' in section
    assert '"response_mode": "FULL"' in section
    assert "NFL_FULL_SLATE_DISCOVERY_COVERAGE_MISMATCH" in section
    assert "NFL_FULL_SLATE_ACCEPTANCE_DEFERRED_NO_GT12_FUTURE_SLATE" in section
    assert '"status": "DEFERRED_WITH_JUSTIFICATION"' in section
    assert "MODEL_INVOCATION_BUDGET_REACHED" in section
    assert "len(identities) == len(set(identities))" in section


def test_nfl_production_acceptance_is_serialized_after_daily_snapshot():
    section = _acceptance_section()
    assert "needs: daily-snapshot" in section
    assert "Run serialized future NFL full-slate exact-once acceptance" in section


def test_nfl_production_acceptance_preserves_terminal_and_execution_governance():
    section = _acceptance_section()
    assert 'WOW_CAN_EXECUTE: "false"' in section
    assert 'WOW_DRY_RUN_ONLY: "true"' in section
    assert '"V17_TERMINAL_REDUCER"' in section
    assert 'row.get("can_execute") is False' in section
    assert 'result.get("can_execute") is False' in section
    assert '"probability_values_logged": False' in section


def test_acceptance_requires_discovery_reconciliation_and_unique_event_identity():
    section = _acceptance_section()
    assert 'cross_reconciliation.get("row_reconciliation") == "PASS"' in section
    assert 'cross_reconciliation.get("events_accounted") == cross_reconciliation.get("events_discovered")' in section
    assert "NFL_FULL_SLATE_EVENT_ID_MISSING" in section
    assert '"model_invocation_budget_terminal_rows": 0' in section



def test_nfl_production_acceptance_selector_is_identity_only_and_non_aging():
    section = _acceptance_section()
    assert "site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard" in section
    assert '"User-Agent": "WOW-V17-NFL-Acceptance-Identity/1.0"' in section
    assert '"schedule_selector": "ESPN_SCOREBOARD_IDENTITY_ONLY"' in section
    assert '"requested_slate_date": acceptance_slate_date' in section
    assert "WOW_NFL_ACCEPTANCE_SLATE_DATE" not in section
    assert '"can_execute": False' in section
    assert '"probability_values_logged": False' in section


def _run_workflow_selector(monkeypatch, events):
    """Execute the *actual workflow function*, never a rewritten selector."""
    import ast
    import json
    import os
    import textwrap
    import urllib.parse
    from datetime import datetime, timedelta
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    section = _acceptance_section()
    embedded = section.split("          python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    parsed = ast.parse(textwrap.dedent(embedded))
    fun = next(
        obj for obj in parsed.body
        if isinstance(obj, ast.FunctionDef) and obj.name == "select_future_nfl_acceptance_slate"
    )

    class FrozenDateTime:
        @staticmethod
        def now(zone):
            # Friday before a future NFL Sunday; no live clock or network.
            return datetime(2026, 10, 23, 12, tzinfo=ZoneInfo("America/Chicago")).astimezone(zone)

        @staticmethod
        def fromisoformat(value):
            return datetime.fromisoformat(value)

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *unused):
            return False

        def read(self, *_args):
            return json.dumps(self.payload).encode()

    def urlopen(url, timeout):
        slate = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["dates"][0]
        return FakeResponse({"events": events if slate == "20261025" else []})

    fake_urllib = SimpleNamespace(
        parse=urllib.parse,
        request=SimpleNamespace(Request=lambda url, headers: url, urlopen=urlopen),
        error=SimpleNamespace(URLError=OSError),
    )
    env = {
        "ZoneInfo": ZoneInfo,
        "datetime": FrozenDateTime,
        "timedelta": timedelta,
        "urllib": fake_urllib,
        "json": json,
        "os": SimpleNamespace(environ={
            "WOW_USER_TIMEZONE": "America/Chicago",
            "WOW_NFL_ACCEPTANCE_LOOKAHEAD_DAYS": "7",
        }),
    }
    exec(compile(ast.Module(body=[fun], type_ignores=[]), str(WORKFLOW), "exec"), env)
    return env["select_future_nfl_acceptance_slate"]()


def _future_sunday_events(n=13):
    return [
        {"id": str(800000 + index), "date": "2026-10-25T18:00:00Z",
         "status": {"type": {"completed": False, "state": "pre"}}}
        for index in range(n)
    ]


def test_selector_accepts_only_real_unique_pregame_sunday_schedule(monkeypatch):
    assert _run_workflow_selector(monkeypatch, _future_sunday_events()) == ("2026-10-25", 13)
    assert _run_workflow_selector(monkeypatch, _future_sunday_events(12)) == (None, 0)


def test_selector_rejects_in_progress_fixtures_not_misclassified_as_pregame(monkeypatch):
    cases = (
        {"status": {"type": {"completed": False, "state": "in"}}},
        {"date": "2026-10-26T01:00:00Z"},  # Monday in Chicago
        {"date": "2026-10-20T18:00:00Z"},  # past
        {"date": "2026-10-25T18:00:00"},  # naive
    )
    for changed in cases:
        games = _future_sunday_events()
        games[-1] = {**games[-1], **changed}
        assert _run_workflow_selector(monkeypatch, games) == (None, 0)


def test_selector_refuses_duplicated_or_missing_schedule_identity(monkeypatch):
    games = _future_sunday_events()
    games[-1] = {**games[-1], "id": games[0]["id"]}
    assert _run_workflow_selector(monkeypatch, games) == (None, 0)
    games[-1] = {**games[-1], "id": ""}
    assert _run_workflow_selector(monkeypatch, games) == (None, 0)
