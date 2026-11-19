"""Read-only NFL producer-receipt cohort split; never self-certifies historical records."""
from pathlib import Path
import re

SQL = Path(__file__).resolve().parents[1] / "scripts" / "v17_nfl_producer_receipt_era_audit.sql"


def test_audits_immutable_prediction_grade_join_without_dropping_legacy_rows():
    s = SQL.read_text()
    assert "public.wow_nfl_forward_verified_grades" in s
    assert "public.wow_nfl_event_predictions" in s
    assert "LEFT JOIN" in s
    assert "p.event_prediction_id = g.event_prediction_id" in s
    assert "receipt_missing" in s
    assert "receipt_present" in s


def test_structural_verification_does_not_claim_digest_or_numerical_replay():
    s = SQL.read_text()
    for part in (
        "NFL_CALIBRATION_OPERATION_V1",
        "nfl_event_model_v17.score_feature_row",
        "evidence_sha256",
        "model_version",
        "source_snapshot_id",
        "calibration_version",
        "receipt_identity_mismatch",
        "producer_receipt_digest_verified",
        "actual_producer_receipt_validated",
    ):
        assert part in s
    assert "FALSE AS producer_receipt_digest_verified" in s
    assert "FALSE AS actual_producer_receipt_validated" in s


def test_never_writes_or_issues_certification():
    s = re.sub(r"--[^\n]*", "", SQL.read_text())
    for kw in ("INSERT", "UPDATE", "DELETE", "MERGE", "ALTER", "DROP", "CREATE", "GRANT", "REVOKE", "CALL", "TRUNCATE"):
        assert not re.search(r"\b"+kw+r"\b", s, re.I)
    assert "FALSE AS certification_authorized" in s
    assert "FALSE AS probability_publishable" in s
    assert "FALSE AS can_execute" in s
