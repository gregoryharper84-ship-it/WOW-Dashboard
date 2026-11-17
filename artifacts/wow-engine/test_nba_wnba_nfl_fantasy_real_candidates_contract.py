from pathlib import Path

SQL = (Path(__file__).resolve().parent / "migrations" / "20260916_nba_wnba_nfl_fantasy_score_real_candidates.sql").read_text()


def test_sources_are_hash_pinned_and_event_level():
    assert "7a4868da67ae7f6ebfba23389e04220be2e3f69d2e56a3942c02aa56f1becc89" in SQL
    assert "f4f1fca50f6517b8006683d2da416f526e8141b6829f0800b04f46a93f4622b8" in SQL
    assert "3ddc45a84f759aa348ce465ae001752c530575455717657cdfe1f8abfcdb4759" in SQL
    assert "e5e0615b3d96a3eaebfaee91e55afb4a4e7fe0caf057454177bcd7d6ad4bcfc2" in SQL
    assert "stats_player_week_2024.csv" in SQL
    assert "stats_player_week_2025.csv" in SQL
    assert "whole_event_chronological_split',true" in SQL
    assert "strictly_prior_residuals',true" in SQL


def test_official_scoring_arithmetic_is_frozen():
    # Basketball: PTS + 1.2 REB + 1.5 AST + 3 BLK + 3 STL - TOV.
    assert "pts + reb*1.2 + ast*1.5 + blk*3 + stl*3 - tov" in SQL
    # NFL full-PPR plus the two 6-point categories. Those categories are folded
    # into the established 10th bridge component by a score-equivalent transform.
    assert "return_td*6 + fum_rec_td*6" in SQL
    assert "raw_2pt + 3*(special_teams_return_td + fumble_recovery_td)" in SQL
    assert "'receptions',1" in SQL
    assert "'two_point_conversions',2" in SQL


def test_candidate_authority_ceiling_is_fail_closed():
    lowered = SQL.lower()
    assert "'candidate-not-certified-'" in lowered
    assert "'candidate'" in lowered
    assert "false,false,false,false,p_activate" in lowered
    assert "'probability_publishable',false" in lowered
    assert "'rank_eligible',false" in lowered
    assert "'can_execute',false" in lowered
    assert "never grants publication, ranking, or execution authority" in lowered


def test_research_activation_is_separate_from_production_authority():
    assert "candidate_research_active=false" in SQL
    assert "candidate_research_active=excluded.candidate_research_active" in SQL
    assert "promoted=false,active=false" in SQL
    assert "probability_publishable=false,can_execute=false" in SQL


def test_sha_pinned_nba_wnba_csv_57_column_contract():
    # Both pinned 2026 SportsDataverse ESPN-derived NBA/WNBA CSV files
    # were independently observed to have 57 columns in header + data.
    # The previous ==50 guard selected ZERO rows from both cached files.
    header = (
        "game_id,season,season_type,game_date,game_date_time,athlete_id,"
        "athlete_display_name,team_id,team_name,team_location,"
        "team_short_display_name,minutes,field_goals_made,field_goals_attempted,"
        "three_point_field_goals_made,three_point_field_goals_attempted,"
        "free_throws_made,free_throws_attempted,offensive_rebounds,"
        "defensive_rebounds,rebounds,assists,steals,blocks,turnovers,fouls,"
        "plus_minus,points,starter,ejected,did_not_play,reason,active,"
        "athlete_jersey,athlete_short_name,athlete_headshot_href,"
        "athlete_position_name,athlete_position_abbreviation,"
        "team_display_name,team_uid,team_slug,team_logo,team_abbreviation,"
        "team_color,team_alternate_color,home_away,team_winner,team_score,"
        "opponent_team_id,opponent_team_name,opponent_team_location,"
        "opponent_team_display_name,opponent_team_abbreviation,"
        "opponent_team_logo,opponent_team_color,opponent_team_alternate_color,"
        "opponent_team_score"
    ).split(",")
    assert len(header) == 57
    assert header[20] == "rebounds"   # SQL array a[21]
    assert header[21] == "assists"    # SQL array a[22]
    assert header[27] == "points"     # SQL array a[28]
    assert "array_length(a,1)=57 and a[3]='2'" in SQL
    assert "array_length(a,1)=50" not in SQL
    for component_index in (21, 22, 28):
        assert f"a[{component_index}]" in SQL


def test_schema_repair_preserves_other_lanes_and_fail_closed_authority():
    assert "array_length(a,1)=150 and a[9]='REG'" in SQL
    assert "where array_length(a,1)=57" in SQL
    assert "never grants publication, ranking, or execution authority" in SQL.lower()
    assert "'can_execute',false" in SQL.lower()
