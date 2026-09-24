from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "v17"
    / "sql"
    / "20260924_v17_mlb_context_only_weather_refresh_repair.sql"
)
SQL = SQL_PATH.read_text()


def test_shared_weather_hash_is_semantic_and_excludes_retrieval_clock():
    assert "WOW_V17_SHARED_ENVIRONMENTAL_SEMANTIC_HASH_V1" in SQL
    assert "extensions.digest" in SQL
    assert "'sha256'" in SQL
    assert "'official_weather',v_weather" in SQL
    assert "'model_input_semantics','CONTEXT_ONLY_UNLESS_CERTIFIED_FEATURE_PRESENT'" in SQL
    # retrieved_at is retained in evidence payload/provenance, but it is absent
    # from the replacement semantic hash document itself.
    semantic_hash = SQL.split("v_hash := encode(", 1)[1].split(";$q$", 1)[0]
    assert "retrieved_at" not in semantic_hash


def test_final_refresh_reads_both_current_and_score_time_evidence_semantics():
    assert "payload_hash,retrieved_at,evidence_payload into e" in SQL
    assert "left join public.wow_event_evidence source_evidence" in SQL
    assert "source_evidence.evidence_id=scored.evidence_id" in SQL
    assert "scored.payload_hash,source_evidence.evidence_payload" in SQL


def test_weather_hash_mismatch_is_exempt_only_when_both_sides_are_context_only():
    assert "k='WEATHER_STATUS'" in SQL
    assert "e.evidence_payload->>'model_input_semantics'" in SQL
    assert "e.evidence_payload->>'model_role'" in SQL
    assert "sc.evidence_payload->>'model_input_semantics'" in SQL
    assert "sc.evidence_payload->>'model_role'" in SQL
    assert SQL.count("like 'CONTEXT_ONLY%'") == 2
    assert "e.evidence_payload->>'probability_adjustment_applied'" in SQL
    assert "sc.evidence_payload->>'probability_adjustment_applied'" in SQL


def test_missing_score_time_evidence_and_other_material_mismatches_still_rerun():
    assert "k||'_SCORING_HASH_MISSING'" in SQL
    assert "k||'_MATERIAL_CHANGE_AFTER_MODEL'" in SQL
    assert "material_change:=true" in SQL
    # The material-change append remains inside a NOT(context-only-weather) guard,
    # so non-weather kinds and future certified/numeric weather remain strict.
    assert "if not (" in SQL


def test_patch_fails_closed_if_live_function_contract_drifts():
    assert "V17_SHARED_ENVIRONMENT_SEMANTIC_HASH_VERSION_PATCH_NOT_APPLIED" in SQL
    assert "V17_SHARED_ENVIRONMENT_SEMANTIC_HASH_PATCH_NOT_APPLIED" in SQL
    assert "V17_FINAL_REFRESH_CURRENT_EVIDENCE_PAYLOAD_PATCH_NOT_APPLIED" in SQL
    assert "V17_FINAL_REFRESH_SCORING_EVIDENCE_PAYLOAD_PATCH_NOT_APPLIED" in SQL
    assert "V17_FINAL_REFRESH_CONTEXT_ONLY_WEATHER_MATERIALITY_PATCH_NOT_APPLIED" in SQL


def test_repair_does_not_authorize_execution_or_change_probability_math():
    assert "can_execute=true" not in SQL
    assert "p_min_lower_bound_gap" not in SQL
    assert "calibrated_home_probability=" not in SQL
    assert "calibrated_away_probability=" not in SQL
    assert "raw_home_probability=" not in SQL
    assert "raw_away_probability=" not in SQL
