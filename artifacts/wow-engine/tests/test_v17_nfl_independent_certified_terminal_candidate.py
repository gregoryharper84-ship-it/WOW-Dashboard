"""Structural regression for a protected, independently approved NFL reducer.

The migration is a candidate; tests never apply DDL or issue certification.
"""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "migrations" / "20261122_nfl_independent_certified_terminal_authority.sql"


def _sql():
    return SQL_PATH.read_text()


def test_complete_source_function_and_singular_reducer_only():
    sql = _sql()
    assert sql.lower().count("create or replace function public.wow_v17_reduce_nfl_event_terminal_label") == 1
    assert sql.count("$function$") == 2
    assert sql.count("if r.event_start_time_utc <= now()") == 1
    assert sql.count("end;\n$function$;") == 1
    assert "v_certified := coalesce(" in sql
    assert "and v_distinct_games>=v_health.minimum_forward_required," in sql


def test_certification_is_external_exact_identity_and_not_retroactive():
    sql = _sql()
    for token in (
        "wow_v17_active_team_event_certification('NFL','NFL')",
        "v_cert->>'independent_verification_status'='PASS'",
        "v_cert->>'certification_status'='CERTIFIED'",
        "v_cert->>'artifact_sha256'=a.artifact_checksum",
        "v_cert->>'model_version'=r.model_version",
        "v_cert->>'feature_schema_version'=a.feature_schema_version",
        "v_cert->>'calibration_artifact_id'=c.calibrator_id::text",
        "v_cert->>'calibration_sha256'=v_calibration_sha",
        "v_calibration_sha ~ '^[0-9a-f]{64}$'",
        "v_certified_at<=r.model_timestamp",
        "v_health.schema_version='NFL_FORWARD_CALIBRATION_HEALTH_V2'",
        "v_health.minimum_forward_required>=100",
        "v_exact_grades=v_distinct_games",
        "NFL_INDEPENDENT_SPECIALIST_CERTIFICATION_UNPROVEN",
        "'V17_TERMINAL_REDUCER'",
        "'can_execute',false",
    ):
        assert token in sql
    assert sql.index("if not v_certified then") < sql.index("if cardinality(v_blockers)=0")


def test_no_blind_mutation_or_registry_publication_override():
    sql = _sql()
    live = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    for forbidden in (
        "drop constraint", "alter table", "update public.wow_nfl_event_predictions",
        "insert into public.wow_team_event_specialist_certifications",
        "grant ", "set can_execute", "probability_publishable=true",
    ):
        assert forbidden not in live.lower()
    assert not re.search(r"\b(create|replace)\s+function\s+public\.wow_v17_nfl_team_event_governance_bridge", live, re.I)
