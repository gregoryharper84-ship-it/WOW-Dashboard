"""Source-integration safeguards for opt-in Reporter within the existing worker."""
from pathlib import Path
from v17.log_incident_intake import SERVICE, WORKER_SERVICE
from v17.engineering_log_reporter import ReporterHold, persist_candidates, _read_redacted_json


def test_supervisor_wires_one_opt_in_reporter_not_new_writer():
    source = (Path(__file__).parent / "v17" /
              "engineering_resident_supervisor.py").read_text()
    assert 'os.getenv("WOW_V17_LOG_REPORTER_ENABLED", "false") == "true"' in source
    assert "resident_reporter_cycle(" in source
    assert "dispatch_once(client=client, redis_client=redis_client" in source
    assert "type(reporter_exc).__name__" in source


def test_reporter_does_not_own_repair_approval_or_lease():
    from v17 import engineering_log_reporter as reporter
    source = Path(reporter.__file__).read_text()
    assert "WOW_ENGINEERING_LOG_REPORTER" in source
    assert 'on_conflict="ticket_id", ignore_duplicates=True' in source
    assert '"REPORTER_CANONICAL_ISSUE_ADMISSION_REQUIRED"' in source
    assert '"dispatch_authorized": False' in source
    assert '"can_execute": False' in source
    assert "def resident_reporter_cycle(" in source
    assert "def read_latest_authenticated_report(" in source


def test_reporter_has_distinct_supervisor_and_source_contracts():
    from v17 import engineering_log_reporter as reporter
    assert reporter.ARTIFACT_FILE == "wow-v17-log-intake.json"
    assert reporter.REPORTER_HEARTBEAT_ID == "WOW_ENGINEERING_LOG_REPORTER"
    assert WORKER_SERVICE != SERVICE
