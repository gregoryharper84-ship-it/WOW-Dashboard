"""Read-only source-to-model-to-certification audit contract."""
from pathlib import Path
import re

SQL = Path(__file__).resolve().parents[1] / "scripts" / "v17_nfl_pregame_evidence_chain_audit.sql"


def test_audit_reads_existing_authoritative_tables():
    sql = SQL.read_text()
    for table in (
        "wow_nfl_forward_verified_grades",
        "wow_nfl_event_predictions",
        "wow_nfl_source_snapshots",
        "wow_nfl_event_fitted_model_artifacts",
        "wow_calibrators",
        "wow_nfl_forward_calibration_health",
        "wow_team_event_specialist_certifications",
    ):
        assert "public." + table in sql


def test_exact_version_and_pregame_provenance_are_required():
    sql = SQL.read_text()
    for invariant in (
        "p.model_version = g.model_version",
        "p.model_artifact_id = g.model_artifact_id",
        "p.official_event_id = g.official_event_id",
        "p.created_at = g.prediction_created_at",
        "g.prediction_created_at < g.event_start_time_utc",
        "p.model_timestamp < g.event_start_time_utc",
        "p.model_timestamp >= p.latest_material_update_timestamp",
        "s.fetched_at <= p.model_timestamp",
        "g.artifact_checksum = a.artifact_checksum",
        "c.health_status = 'PASS'",
        "cert.artifact_sha256 = a.artifact_checksum",
        "cert.independent_verification_status = 'PASS'",
    ):
        assert invariant in sql


def test_audit_never_certifies_publishes_or_executes():
    sql = SQL.read_text()
    uncommented = re.sub(r"--[^\n]*", "", sql)
    for token in ("INSERT", "UPDATE", "DELETE", "MERGE", "ALTER", "DROP",
                  "CREATE", "GRANT", "REVOKE", "TRUNCATE", "CALL", "EXECUTE"):
        assert not re.search(r"\b" + token + r"\b", uncommented, re.I), token
    for token in ("FALSE AS audit_authorizes_certification",
                  "FALSE AS audit_authorizes_publication",
                  "FALSE AS can_execute",
                  "INDEPENDENT_CERTIFICATION_RECEIPT_UNPROVEN"):
        assert token in sql

def test_certification_cannot_link_another_calibrator_or_unverified_digest():
    sql = SQL.read_text()
    assert "cert.calibration_artifact_id = c.calibrator_id::text" in sql
    assert "cert.calibration_method = c.calibration_method" in sql
    assert "cert.calibration_sha256 = c.fit_metrics_json->>'calibration_artifact_sha256'" in sql
    assert "NULLIF(cert.calibration_sha256,'') IS NOT NULL" not in sql


def test_health_absence_never_erases_observed_grade_audit():
    sql = SQL.read_text()
    assert "LEFT JOIN latest_health h ON TRUE" in sql
    assert "CROSS JOIN latest_health h" not in sql
