"""Always-on SQL source regression fence, even when PostgreSQL test dependencies are absent.

These are source invariants, NOT substitutes for real PostgreSQL integration,
independent model certification, or a V17 terminal acceptance receipt.
"""
from pathlib import Path

SQL = (Path(__file__).resolve().parents[1] / "v17" / "sql_candidates" /
       "20261105_multisport_nfl_receipt_admission_UNDEPLOYED.sql")


def parts():
    source = SQL.read_text(encoding="utf-8")
    helper = source.split("CREATE OR REPLACE FUNCTION public.wow_v17_nfl_receipt_certified(", 1)[1].split("END $cert$;", 1)[0]
    issuer = source.split("CREATE OR REPLACE FUNCTION public.wow_v17_admit_nfl_event_receipt(", 1)[1].split("END $f$;", 1)[0]
    reader = source.split("CREATE OR REPLACE FUNCTION public.wow_v17_verify_multisport_event_receipt(", 1)[1].split("END $f$;", 1)[0]
    return source, helper, issuer, reader


def test_forward_grades_and_independent_artifact_certificate_required():
    _, helper, _, _ = parts()
    for required in (
        "wow_nfl_forward_calibration_health",
        "h.graded_n", "FORWARD_EVIDENCE_PASS",
        "GREATEST(h.minimum_forward_required,100)",
        "h.graded_n IS DISTINCT FROM matching_forward_grades",
        "p.model_artifact_id=a.artifact_id",
        "p.calibration_version=s.calibration_version",
        "g.model_version=s.model_version",
        "wow_team_event_specialist_certifications",
        "independent_verification_status='PASS'",
        "certification_status='CERTIFIED'",
        "cert.artifact_sha256=a.artifact_checksum",
        "cert.calibration_sha256=cal_sha",
        "a.probability_publishable IS DISTINCT FROM false",
    ):
        assert required in helper, required


def test_registry_is_not_its_own_probability_publisher():
    source, helper, issuer, _ = parts()
    assert "a.probability_publishable IS DISTINCT FROM false" in helper
    assert "a.probability_publishable IS DISTINCT FROM false" in issuer
    assert "a.probability_publishable IS DISTINCT FROM true" not in source
    assert "cert.probability_publishable=true" in helper
    assert "s.model_probability_publishable IS DISTINCT FROM true" in issuer


def test_issuer_and_readback_both_recheck_independent_certification():
    _, _, issuer, reader = parts()
    assert "public.wow_v17_nfl_receipt_certified(p_score_snapshot_id) IS DISTINCT FROM true" in issuer
    assert "public.wow_v17_nfl_receipt_certified(r.score_snapshot_id) IS DISTINCT FROM true" in reader
    assert "public.wow_v17_reduce_nfl_event_terminal_label" in issuer
    assert "public.wow_v17_reduce_nfl_event_terminal_label" in reader


def test_mlb_existing_ledger_not_weakened_and_acl_fails_closed():
    source, _, _, _ = parts()
    assert "CREATE TABLE IF NOT EXISTS public.wow_v17_multisport_event_prediction_receipts" in source
    assert "ALTER TABLE public.wow_event_predictions" not in source
    assert "UPDATE public.wow_event_predictions" not in source
    assert "DELETE FROM public.wow_event_predictions" not in source
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "REVOKE EXECUTE ON FUNCTION public.wow_v17_admit_nfl_event_receipt(uuid) FROM PUBLIC" in source
    assert "REVOKE ALL ON public.wow_v17_multisport_event_prediction_receipts FROM service_role" in source
    assert "GRANT EXECUTE ON FUNCTION public.wow_v17_admit_nfl_event_receipt(uuid) TO service_role" in source
    assert "GRANT INSERT ON public.wow_v17_multisport_event_prediction_receipts" not in source
    assert "trg_v17_multisport_receipt_immutable" in source


def test_reducer_receipt_origin_is_external_and_mandatory():
    source, _, issuer, reader = parts()
    assert "reduction->>'reducer_receipt_id'" in issuer
    assert "reduction->>'reducer_receipt_sha256'" in issuer
    assert "reducer_id:=pg_catalog.gen_random_uuid()" not in source
    assert "reducer_hash:=pg_catalog.encode(" not in source
    assert "LLP_REDUCER_ORIGIN_RECEIPT_UNAVAILABLE" in issuer
    assert "to_regprocedure(" in issuer
    assert "to_regprocedure(" in reader
    assert "wow_terminal_reducer.verify_nfl_reducer_receipt" in issuer
    assert "wow_terminal_reducer.verify_nfl_reducer_receipt" in reader
    assert "CREATE FUNCTION wow_terminal_reducer.verify_nfl_reducer_receipt" not in source
    assert "CREATE TABLE wow_terminal_reducer.origin_receipts" not in source


def test_caller_cannot_submit_probability_or_terminal_approval():
    _, _, issuer, _ = parts()
    assert issuer.strip().startswith("p_score_snapshot_id uuid)")
    assert "pg_catalog.gen_random_uuid()" in issuer
    assert "FOR SHARE" in issuer
    assert "pg_catalog.pg_advisory_xact_lock" in issuer


def test_null_provenance_fields_are_rejected_not_sql_unknown():
    _, _, issuer, reader = parts()
    assert "reduction->>'status' IS DISTINCT FROM 'PASS'" in issuer
    assert "r.terminal_reducer_payload->>'reducer_receipt_sha256'" in reader
    assert "wow_terminal_reducer.verify_nfl_reducer_receipt" in reader
    assert "wow_terminal_reducer.verify_nfl_reducer_receipt" in issuer
    assert "r.terminal_reducer_payload IS DISTINCT FROM present" in reader
    assert "r.model_artifact_sha256 IS DISTINCT FROM a.artifact_checksum" in reader
