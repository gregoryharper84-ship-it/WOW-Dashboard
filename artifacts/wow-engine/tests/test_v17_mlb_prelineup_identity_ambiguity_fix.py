from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "20261007_v17_mlb_prelineup_identity_evidence_ambiguity_fix.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def test_followup_uses_collision_proof_variable_names():
    sql = _sql()
    for name in (
        "v_source_name",
        "v_source_ref",
        "v_payload",
        "v_payload_hash",
        "v_evidence_id",
        "v_evidence_time",
        "v_next_attempt",
    ):
        assert name in sql

    declare_block = sql.split("declare", 1)[1].split("begin", 1)[0]
    assert " source_name " not in declare_block
    assert " source_ref " not in declare_block
    assert " evidence_id " not in declare_block
    assert " payload_hash " not in declare_block


def test_followup_keeps_exact_canonical_identity_checks():
    sql = _sql()
    assert "se.official_event_id is distinct from r.official_event_id" in sql
    assert "se.source_game_json->>'gamePk' is distinct from r.official_event_id" in sql
    assert "se.home_team is distinct from r.home_team" in sql
    assert "se.away_team is distinct from r.away_team" in sql
    assert "se.event_start_time is distinct from r.event_start_time" in sql
    assert "'OFFICIAL_EVENT_ID'" in sql
    assert "'OFFICIAL','RETRIEVED'" in sql


def test_followup_never_claims_probability_or_lineup_authority():
    sql = _sql()
    assert "'lineup_evidence_claimed', false" in sql
    assert "'probability_authority', false" in sql
    assert "'can_execute', false" in sql
    assert "wow_reduce_event_terminal_label" not in sql
    assert "probability_publishable=true" not in sql.lower()
    assert "rank_eligible=true" not in sql.lower()
