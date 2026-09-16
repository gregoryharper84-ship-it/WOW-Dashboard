from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "migrations" / "20260916_mlb_fantasy_score_candidate_builder.sql").read_text()


def test_builder_is_regular_season_real_data_only():
    assert "wow_mlb_retrosplits_rows" in SQL
    assert "season_phase = 'R'" in SQL
    assert "FANTASY_SCORE_HITTER_IDENTITY_COVERAGE_INSUFFICIENT" in SQL
    assert "FANTASY_SCORE_PITCHER_IDENTITY_COVERAGE_INSUFFICIENT" in SQL
    assert "v_mapped::numeric / v_expected < 0.99" in SQL


def test_builder_uses_whole_event_chronology_and_strictly_prior_residuals():
    assert "row_number() over(order by event_date,event_id)" in SQL
    assert "floor(v_event_n * 0.70)" in SQL
    assert "floor(v_event_n * 0.15)" in SQL
    assert "rows between unbounded preceding and 1 preceding" in SQL
    assert "player_shrinkage_prior_games',3" in SQL
    assert "DETERMINISTIC_MD5_ORDER_FIRST_2000" in SQL
    assert "limit 2000" in SQL


def test_hitter_scoring_profile_matches_verified_prizepicks_contract():
    assert "PRIZEPICKS_MLB_HITTER_FANTASY_SCORE_2025_07_11_VERIFIED_V1" in SQL
    for fragment in (
        "'singles',3",
        "'doubles',5",
        "'triples',8",
        "'home_runs',10",
        "'runs',2",
        "'rbi',2",
        "'walks',2",
        "'hbp',2",
        "'stolen_bases',5",
    ):
        assert fragment in SQL


def test_pitcher_scoring_profile_matches_verified_prizepicks_contract():
    assert "PRIZEPICKS_MLB_PITCHER_FANTASY_SCORE_2025_07_11_VERIFIED_V1" in SQL
    for fragment in (
        "'wins',6",
        "'quality_starts',4",
        "'strikeouts',3",
        "'outs_recorded',1",
        "'earned_runs',-3",
    ):
        assert fragment in SQL
    assert "p_out,0) >= 18" in SQL
    assert "p_er,0) <= 3" in SQL


def test_candidate_builder_cannot_grant_production_authority():
    assert "'CANDIDATE'" in SQL
    assert "false,false,false,false,p_activate" in SQL
    assert "promoted=false" in SQL
    assert "active=false" in SQL
    assert "probability_publishable=false" in SQL
    assert "can_execute=false" in SQL
    assert "'rank_eligible',false" in SQL
    assert "candidate_research_active=excluded.candidate_research_active" in SQL


def test_builder_requires_merged_code_identity_and_freezes_identity_snapshot():
    assert "p_training_code_sha" in SQL
    assert "^[0-9a-f]{40}$" in SQL
    assert "RETROSHEET_PLAYER_IDENTITY_20260916" in SQL
    assert "raw_sha256" in SQL
    assert "identity_source_sha256" in SQL
    assert "research_only = true" in SQL
    assert "can_execute = false" in SQL


def test_builder_function_is_not_publicly_executable():
    assert "security invoker" in SQL.lower()
    assert "revoke all on function public.wow_v17_build_mlb_fantasy_score_candidates(text,boolean) from public" in SQL
    assert "from anon, authenticated" in SQL
    assert "grant execute on function public.wow_v17_build_mlb_fantasy_score_candidates(text,boolean) to service_role" in SQL
