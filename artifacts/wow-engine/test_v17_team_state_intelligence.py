from datetime import datetime, timedelta, timezone

from v17.team_state_intelligence import (
    build_team_state, champion_challenger_metrics, coverage_report,
    paired_matchup_features, resolve_evidence_field,
)


def _history(base, *, strength=1.0):
    rows=[]
    for i in range(12):
        point=1.0+i*.4
        rows.append({"event_time":(base-timedelta(days=12-i)).isoformat(),"season":2026,"won":point>0,
                     "point_diff":point,"process_margin":point,"opponent_strength_prior":strength,
                     "travel_load":.2,"congestion":.1,"roster_ids":["a","b","c"],"lineup_ids":["a","b"],
                     "attack_style_index":1.1,"defense_style_index":.4,"pace_or_tempo_index":.8})
    return rows


def test_schedule_adjustment_rewards_tougher_opponents_and_regime_uses_current_season():
    base=datetime(2026,9,15,tzinfo=timezone.utc)
    state=build_team_state(_history(base,strength=2),target_time=base,expected_season_games=82,target_season=2026)
    assert state.schedule_adjusted_point_diff_l5 > state.point_diff_l5
    assert state.season_games_prior == 12
    assert state.season_regime_early == 1.0


def test_unexplained_streak_is_explicit_and_driver_is_fitted_feature():
    base=datetime(2026,9,15,tzinfo=timezone.utc); history=_history(base,strength=0)
    for row in history[-7:]: row["won"],row["point_diff"],row["process_margin"] = True,.2,.1
    state=build_team_state(history,target_time=base,target_season=2026); features=state.as_features()
    assert state.streak_without_driver == 1.0
    assert features["trend_driver_no_identified_driver"] == 1.0


def test_matchup_interactions_are_cross_team():
    base=datetime(2026,9,15,tzinfo=timezone.utc)
    home=build_team_state(_history(base),target_time=base,target_season=2026)
    ah=_history(base)
    for row in ah: row["attack_style_index"],row["defense_style_index"],row["pace_or_tempo_index"]=-.5,1.4,.4
    away=build_team_state(ah,target_time=base,target_season=2026); f=paired_matchup_features(home,away)
    assert {"home_attack_vs_away_defense","away_attack_vs_home_defense","matchup_style_edge","pace_mismatch"} <= set(f)
    assert f["pace_mismatch"] > 0


def test_champion_challenger_never_auto_promotes():
    rows=[{"outcome":i%2==0,"champion_probability":.55 if i%2==0 else .45,
           "challenger_probability":.65 if i%2==0 else .35} for i in range(120)]
    audit=champion_challenger_metrics(rows)
    assert audit["promotion_eligible_on_metrics"] is True
    assert audit["automatic_promotion"] is False
    assert audit["can_execute"] is False


def test_partial_coverage_blocks_slate_wide_none_qualified():
    audit=coverage_report([{"status":"COMPLETE"},{"status":"MODEL_INPUTS_INSUFFICIENT"},{}],expected_events=4)
    assert audit["coverage_status"] == "PARTIAL"
    assert audit["slate_wide_none_qualified_allowed"] is False


def test_data_resilience_fallback_and_conflict():
    now=datetime(2026,9,15,22,0,tzinfo=timezone.utc)
    result=resolve_evidence_field([
        {"source":"primary","status":"SOURCE_AUTH_FAILED","priority":0,"value":1,"timestamp":now.isoformat()},
        {"source":"fallback","status":"SOURCE_OK","priority":1,"value":2,"timestamp":now.isoformat()},
    ],now=now,max_age_seconds=60)
    assert result["resolved"] is True and result["source"] == "fallback"
    conflict=resolve_evidence_field([
        {"source":"a","status":"SOURCE_OK","priority":0,"value":1,"timestamp":now.isoformat()},
        {"source":"b","status":"SOURCE_OK","priority":0,"value":3,"timestamp":now.isoformat()},
    ],now=now,max_age_seconds=60)
    assert conflict["status"] == "SOURCE_CONFLICT" and conflict["resolved"] is False
