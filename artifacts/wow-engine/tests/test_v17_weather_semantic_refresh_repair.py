from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "v17"
    / "sql"
    / "20260923_v17_weather_semantic_refresh_repair.sql"
)
SQL = SQL_PATH.read_text()


def _between(start: str, end: str) -> str:
    return SQL.split(start, 1)[1].split(end, 1)[0]


def test_weather_hash_is_semantic_and_excludes_retrieval_metadata():
    helper = _between(
        "create or replace function public.wow_v17_weather_semantic_hash",
        "create or replace function public.wow_v17_weather_payload_is_context_only",
    )
    assert "WOW_V17_WEATHER_SEMANTIC_HASH_V1" in helper
    assert "official_weather" in helper
    assert "model_input_semantics" in helper
    assert "probability_adjustment_applied" in helper
    assert "retrieved_at" not in helper
    assert "source_ref" not in helper
    assert "source_name" not in helper


def test_context_only_weather_is_only_non_material_exception():
    predicate = _between(
        "create or replace function public.wow_v17_evidence_change_requires_rescore",
        "create or replace function public.wow_v17_hydrate_shared_environmental_evidence",
    )
    assert "WEATHER_STATUS" in predicate
    assert "wow_v17_weather_payload_is_context_only(p_latest_payload)" in predicate
    assert "wow_v17_weather_payload_is_context_only(p_scoring_payload)" in predicate
    assert "then false" in predicate
    assert "else true" in predicate


def test_certified_weather_probability_adjustment_stays_material():
    context_helper = _between(
        "create or replace function public.wow_v17_weather_payload_is_context_only",
        "create or replace function public.wow_v17_evidence_change_requires_rescore",
    )
    assert "probability_adjustment_applied" in context_helper
    assert "= 'true' then false" in context_helper
    assert "CONTEXT_ONLY_UNLESS_CERTIFIED_FEATURE_PRESENT" in context_helper
    assert "CONTEXT_ONLY_NOT_NUMERIC_IN_CERTIFIED_V2D_BASELINE" in context_helper


def test_shared_provider_dedupes_with_semantic_hash_but_keeps_audit_timestamp():
    provider = _between(
        "create or replace function public.wow_v17_hydrate_shared_environmental_evidence",
        "create or replace function public.wow_v17_reconcile_probability_only_snapshot",
    )
    assert "'retrieved_at',v_now" in provider
    assert "v_hash := public.wow_v17_weather_semantic_hash" in provider
    assert "and payload_hash=v_hash" in provider
    assert "semantic_hash_version" in provider
    assert "'probability_adjustment_applied',false" in provider
    assert "'can_execute',false" in provider


def test_reconcile_counts_only_probability_material_mismatches():
    reconcile = _between(
        "create or replace function public.wow_v17_reconcile_probability_only_snapshot",
        "create or replace function public.wow_v17_probability_only_final_refresh",
    )
    assert "l.payload_hash is distinct from s.payload_hash" in reconcile
    assert "wow_v17_evidence_change_requires_rescore" in reconcile
    assert "material_mismatch_count" in reconcile
    assert "probability_invalidated=false" in reconcile
    assert "rerun_required=false" in reconcile
    assert "'can_execute',false" in reconcile


def test_final_refresh_still_fails_closed_on_missing_or_stale_required_evidence():
    refresh = SQL.split(
        "create or replace function public.wow_v17_probability_only_final_refresh", 1
    )[1]
    assert "'_NOT_CALLED'" in refresh
    assert "'_NOT_FRESH'" in refresh
    assert "age_seconds>e.freshness_ttl_seconds" in refresh
    assert "'_SCORING_HASH_MISSING'" in refresh
    assert "material_change:=true" in refresh
    assert "v_status:=case" in refresh
    assert "when material_change then 'RERUN_REQUIRED'" in refresh
    assert "when cardinality(reasons)>0 then 'FAIL'" in refresh
    assert "wow_v17_evidence_change_requires_rescore" in refresh
    assert "'can_execute',false" in refresh


def test_repair_does_not_relax_probability_publication_or_execution():
    assert "probability_publishable=false" in SQL
    assert "probability_publishable=true" not in SQL
    assert "can_execute=true" not in SQL
