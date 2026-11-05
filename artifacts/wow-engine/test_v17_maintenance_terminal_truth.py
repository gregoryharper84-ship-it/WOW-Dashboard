import json
from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parents[2]


def _workflow(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text()


def test_basketball_maintenance_cannot_go_green_with_blocked_rows():
    text = _workflow("wow-v17-basketball-model-maintenance.yml")
    assert 'blocked_rows = [row for row in payload.get("rows", []) if row.get("status") == "BLOCKED"]' in text
    assert 'if payload.get("status") == "BLOCKED" or blocked_rows:' in text
    assert 'raise RuntimeError("BASKETBALL_MAINTENANCE_BLOCKED:' in text


def test_ncaaf_maintenance_cannot_go_green_when_governed_payload_is_blocked():
    text = _workflow("wow-v17-ncaaf-model-maintenance.yml")
    assert 'if payload.get("status") == "BLOCKED":' in text
    assert 'raise RuntimeError("NCAAF_MAINTENANCE_BLOCKED:' in text


def test_first_six_orchestrator_cannot_hide_blocked_ancillary_lanes():
    text = _workflow("wow-v17-first-six-model-maintenance.yml")
    assert 'blocked_ancillary = {' in text
    assert 'if blocked_ancillary:' in text
    assert 'raise RuntimeError("FIRST_SIX_ANCILLARY_MAINTENANCE_BLOCKED:' in text


def test_terminal_truth_preserves_non_execution_controls():
    for name in (
        "wow-v17-basketball-model-maintenance.yml",
        "wow-v17-ncaaf-model-maintenance.yml",
        "wow-v17-first-six-model-maintenance.yml",
    ):
        text = _workflow(name)
        assert 'WOW_CAN_EXECUTE: "false"' in text
        assert 'WOW_DRY_RUN_ONLY: "true"' in text


def test_ncaaf_compiler_diagnostics_are_visible_without_raw_evidence_payloads():
    text = _workflow("wow-v17-ncaaf-model-maintenance.yml")
    maintenance = text.split("- name: Run governed NCAAF model maintenance", 1)[1]
    maintenance = maintenance.split("  exact-candidate-replay-once:", 1)[0]
    for key in (
        "fresh_acquisition_complete",
        "source_snapshot_n",
        "source_snapshot_persisted_n",
        "feature_status",
        "games_seen",
        "evidence_rows_seen",
        "blocked_games",
        "features_persisted",
        "features_existing",
        "complete_feature_rows",
        "feature_blocker_counts",
        "feature_blocker_samples",
    ):
        assert f'"{key}":' in maintenance
    assert 'for sample in raw_samples[:3]:' in maintenance
    assert 'str(value)[:80]' in maintenance
    assert '"payload":' not in maintenance
    assert 'assert payload.get("can_execute") is False' in maintenance
    assert 'if payload.get("status") == "BLOCKED":' in maintenance


def test_ncaaf_compiler_sample_logging_executes_with_strict_bounds(capsys):
    """Exercise the workflow's real log projection, not a duplicate mock."""
    workflow = _workflow("wow-v17-ncaaf-model-maintenance.yml")
    start_marker = '          raw_samples = feature.get("blocker_samples")'
    end_marker = '          if payload.get("status") == "BLOCKED":'
    assert workflow.count(start_marker) == 1
    tail = workflow.split(start_marker, 1)[1]
    assert tail.count(end_marker) >= 1
    script = dedent(start_marker + tail.split(end_marker, 1)[0])

    # Server-supplied samples may contain bulky fields not authorized for logs.
    # The log projection must not accidentally emit these extras.
    samples = [
        {
            "official_event_id": "E" * 80 + str(i),
            "code": "X" * 150,
            "blockers": ["B" * 120 + str(j) for j in range(12)],
            "payload": {"secret": "NEVER_LOG_SECRET"},
            "provider_raw_body": "NEVER_LOG_RAW_BODY",
        }
        for i in range(6)
    ]
    feature = {
        "status": "BLOCKED",
        "games_seen": 4231,
        "evidence_rows_seen": 0,
        "blocked_games": 4231,
        "blocker_counts": {"NCAAF_MODEL_FEATURE_EVIDENCE_INCOMPLETE": 4231},
        "blocker_samples": samples,
        "complete_feature_rows": 0,
    }
    payload = {
        "status": "CANDIDATE_EVIDENCE_UPDATED",
        "feature_compilation": feature,
        "can_execute": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "blockers": [],
    }
    exec(compile(script, "<ncaaf-workflow-diagnostics>", "exec"), {
        "feature": feature, "payload": payload, "json": json,
    })
    logged = capsys.readouterr().out
    assert "NEVER_LOG_SECRET" not in logged
    assert "NEVER_LOG_RAW_BODY" not in logged
    message = json.loads(logged)
    assert message["feature_blocker_counts"] == feature["blocker_counts"]
    assert message["blocked_games"] == 4231
    assert message["evidence_rows_seen"] == 0
    assert message["can_execute"] is False
    projected = message["feature_blocker_samples"]
    assert len(projected) == 3
    for sample in projected:
        assert set(sample) == {"official_event_id", "code", "blockers"}
        assert len(sample["official_event_id"]) <= 40
        assert len(sample["code"]) <= 100
        assert len(sample["blockers"]) == 8
        assert all(len(value) <= 80 for value in sample["blockers"])
