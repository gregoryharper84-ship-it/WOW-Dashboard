from datetime import datetime, timedelta, timezone

from v17 import team_state_challenger_training as training


def _events(n=90):
    base=datetime(2025,1,1,tzinfo=timezone.utc); teams=[f"T{i}" for i in range(10)]; rows=[]
    for i in range(n):
        home=teams[i%10]; away=teams[(i*3+1)%10]
        if home==away: away=teams[(teams.index(home)+1)%10]
        hs=100+(i%11); aws=96+((i*7)%13)
        rows.append({"event_id":f"E{i}","event_start_time":(base+timedelta(days=i)).isoformat(),
                     "season":2025 if i<45 else 2026,"home_team":home,"away_team":away,
                     "home_score":hs,"away_score":aws,"home_process_margin":(hs-aws)*.7,
                     "away_process_margin":(aws-hs)*.7,"source_manifest":{"source":"TEST_PRIOR_ONLY"}})
    return rows


def test_binary_rows_are_leakage_safe_and_cover_accuracy_feature_families():
    rows,metadata,features=training.build_dynamic_binary_rows(_events(),expected_season_games=82,min_prior_games=3)
    assert rows and all(row.feature_as_of < row.event_start_time for row in rows)
    required={"home_win_rate_l5","home_schedule_adjusted_point_diff_l5","home_change_point_score",
              "home_sustainability_score","home_roster_continuity","matchup_style_edge",
              "home_fragility_score","home_season_regime_early"}
    assert required <= set(features)
    assert all(meta["source_manifest"]["market_features_used"] is False for meta in metadata)


def test_target_outcome_does_not_change_its_pregame_features():
    a=_events(); b=[dict(row) for row in a]; target=60; b[target]["home_score"] += 50
    ra,_,_=training.build_dynamic_binary_rows(a,expected_season_games=82,min_prior_games=3)
    rb,_,_=training.build_dynamic_binary_rows(b,expected_season_games=82,min_prior_games=3)
    aa={row.event_id:row for row in ra}; bb={row.event_id:row for row in rb}; eid=f"E{target}"
    assert dict(aa[eid].features) == dict(bb[eid].features)


def test_postmortem_snapshot_preserves_attribution_without_probability_rewrite():
    payload=training.postmortem_feature_snapshot(
        prediction_id="p1",home_state={"streak_without_driver":1,"results_process_divergence":1},
        away_state={"form_schedule_suppressed":1},champion_probability=.62,challenger_probability=.58)
    assert "HOME:STREAK_WITHOUT_DRIVER" in payload["diagnostic_flags"]
    assert "HOME:RESULTS_PROCESS_DIVERGENCE" in payload["diagnostic_flags"]
    assert payload["probability_rewritten"] is False and payload["can_execute"] is False
