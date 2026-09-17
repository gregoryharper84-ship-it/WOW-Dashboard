from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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


def test_binary_research_screen_judges_final_calibrated_artifact():
    metrics=SimpleNamespace(
        raw_brier=.2314, calibrated_brier=.2327, baseline_brier=.2449,
        raw_log_loss=.6543, calibrated_log_loss=.6579, baseline_log_loss=.6829,
        ece=.0368,
    )
    screen=training.evaluate_binary_research_screen(metrics)
    assert screen["passed"] is True
    assert screen["checks"]["calibrated_beats_baseline_brier"] is True
    assert screen["checks"]["calibrated_beats_baseline_log_loss"] is True
    assert screen["checks"]["calibrated_ece_within_limit"] is True
    assert screen["automatic_certification"] is False
    assert screen["automatic_promotion"] is False
    assert screen["probability_publishable"] is False
    assert screen["can_execute"] is False


def test_binary_research_screen_still_blocks_nonsharp_calibrated_candidate():
    metrics=SimpleNamespace(
        raw_brier=.24, calibrated_brier=.251, baseline_brier=.249,
        raw_log_loss=.68, calibrated_log_loss=.696, baseline_log_loss=.692,
        ece=.04,
    )
    screen=training.evaluate_binary_research_screen(metrics)
    assert screen["passed"] is False
    assert screen["checks"]["calibrated_beats_baseline_brier"] is False
    assert screen["checks"]["calibrated_beats_baseline_log_loss"] is False


def test_multiclass_research_screen_uses_calibrated_baseline_and_ece():
    metrics=SimpleNamespace(
        raw_brier=.665, calibrated_brier=.649, baseline_brier=.659,
        raw_log_loss=1.132, calibrated_log_loss=1.085, baseline_log_loss=1.087,
        calibrated_ece=.034,
    )
    screen=training.evaluate_multiclass_research_screen(metrics)
    assert screen["passed"] is True
    assert all(screen["checks"].values())
    assert screen["probability_publishable"] is False
    assert screen["can_execute"] is False


def test_postmortem_snapshot_preserves_attribution_without_probability_rewrite():
    payload=training.postmortem_feature_snapshot(
        prediction_id="p1",home_state={"streak_without_driver":1,"results_process_divergence":1},
        away_state={"form_schedule_suppressed":1},champion_probability=.62,challenger_probability=.58)
    assert "HOME:STREAK_WITHOUT_DRIVER" in payload["diagnostic_flags"]
    assert "HOME:RESULTS_PROCESS_DIVERGENCE" in payload["diagnostic_flags"]
    assert payload["probability_rewritten"] is False and payload["can_execute"] is False


class _Query:
    def __init__(self, table, calls):
        self.table = table
        self.calls = calls

    def upsert(self, payload, **kwargs):
        self.calls.append((self.table, payload, kwargs))
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class _Client:
    def __init__(self):
        self.calls = []

    def table(self, name):
        return _Query(name, self.calls)


def test_candidate_persistence_uses_registry_valid_review_state_and_immutable_safe_insert():
    client = _Client()
    candidate = SimpleNamespace(
        artifact_payload={"model": "test"},
        dataset_hash="a" * 64,
        calibrator_payload={"method": "TEST"},
        metrics=SimpleNamespace(train_n=300, calibration_n=100, test_n=100),
        research_screen_pass=True,
    )

    version = training._persist_artifact(
        client,
        sport="NFL",
        league="NFL",
        family="NFL_DYNAMIC_TEAM_STATE_LOGIT_V2",
        schema="NFL_DYNAMIC_TEAM_STATE_FEATURES_V2",
        training_code_sha="b" * 40,
        candidate=candidate,
        metrics={"can_execute": False},
    )

    table, payload, kwargs = client.calls[-1]
    assert table == "wow_d1_candidate_artifacts"
    assert payload["source_review_status"] == "REQUIRED"
    assert payload["automatic_certification"] is False
    assert payload["automatic_promotion"] is False
    assert payload["probability_publishable"] is False
    assert payload["can_execute"] is False
    assert kwargs["on_conflict"] == "model_artifact_version"
    assert kwargs["ignore_duplicates"] is True
    assert version.startswith("NFL_DYNAMIC_TEAM_STATE_LOGIT_V2_")


def test_team_state_screen_can_override_generic_candidate_screen_without_promotion():
    client = _Client()
    candidate = SimpleNamespace(
        artifact_payload={"model": "test"},
        dataset_hash="c" * 64,
        calibrator_payload={"method": "TEST"},
        metrics=SimpleNamespace(train_n=300, calibration_n=100, test_n=100),
        research_screen_pass=False,
    )

    training._persist_artifact(
        client,
        sport="NBA",
        league="NBA",
        family="NBA_DYNAMIC_TEAM_STATE_LOGIT_V2",
        schema="NBA_DYNAMIC_TEAM_STATE_FEATURES_V2",
        training_code_sha="d" * 40,
        candidate=candidate,
        metrics={"can_execute": False},
        research_screen_pass=True,
    )

    _, payload, _ = client.calls[-1]
    assert payload["research_screen_pass"] is True
    assert payload["lifecycle_state"] == "CANDIDATE"
    assert payload["promoted"] is False
    assert payload["active"] is False
    assert payload["probability_publishable"] is False
    assert payload["can_execute"] is False