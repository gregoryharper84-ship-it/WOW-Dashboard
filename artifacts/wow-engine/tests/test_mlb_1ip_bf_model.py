from mlb_1ip_bf_model import (
    BFObservation,
    bf_bucket,
    binary_metrics,
    fit_league_prior,
    history_counts,
    multiclass_brier,
    multiclass_log_loss,
    score_pitcher,
    update_history,
)


def _rows(n=120):
    out = []
    for i in range(n):
        bf = 3 if i % 3 == 0 else 4 if i % 3 == 1 else 5
        out.append(BFObservation(pitcher_id=100 + (i % 5), bf=bf, event_time=f"2024-01-{(i % 28)+1:02d}T00:00:00Z", game_pk=i+1))
    return out


def test_bf_bucket():
    assert bf_bucket(3) == "3"
    assert bf_bucket(4) == "4"
    assert bf_bucket(5) == "5_PLUS"
    assert bf_bucket(8) == "5_PLUS"


def test_fit_league_prior_normalizes():
    prior = fit_league_prior(_rows())
    assert abs(sum(prior.values()) - 1.0) < 1e-12
    assert set(prior) == {"3", "4", "5_PLUS"}


def test_score_pitcher_shrinks_to_league_prior_without_history():
    prior = {"3": 0.4, "4": 0.35, "5_PLUS": 0.25}
    scored = score_pitcher(pitcher_id=1, league_prior=prior, pitcher_counts={}, alpha=10)
    assert scored["P_BF_3"] == prior["3"]
    assert scored["P_BF_4"] == prior["4"]
    assert scored["P_BF_GE_5"] == prior["5_PLUS"]
    assert scored["P_MORE_3_5"] == prior["4"] + prior["5_PLUS"]
    assert scored["P_MORE_4_5"] == prior["5_PLUS"]
    assert scored["probability_publishable"] is False
    assert scored["rank_eligible"] is False
    assert scored["can_execute"] is False


def test_score_pitcher_updates_from_history_without_losing_normalization():
    prior = {"3": 0.4, "4": 0.35, "5_PLUS": 0.25}
    history = {7: {"3": 8, "4": 1, "5_PLUS": 1}}
    scored = score_pitcher(pitcher_id=7, league_prior=prior, pitcher_counts=history, alpha=10)
    total = scored["P_BF_3"] + scored["P_BF_4"] + scored["P_BF_GE_5"]
    assert abs(total - 1.0) < 1e-12
    assert scored["P_BF_3"] > prior["3"]
    assert scored["pitcher_history_n"] == 10


def test_history_update_is_post_prediction_primitive():
    rows = _rows()
    history = history_counts(rows[:100])
    row = rows[100]
    before = sum(history.get(row.pitcher_id, {}).values())
    update_history(history, row)
    after = sum(history[row.pitcher_id].values())
    assert after == before + 1


def test_metrics_are_finite():
    actual = ["3", "4", "5_PLUS"]
    predicted = [
        {"3": 0.6, "4": 0.2, "5_PLUS": 0.2},
        {"3": 0.2, "4": 0.6, "5_PLUS": 0.2},
        {"3": 0.2, "4": 0.2, "5_PLUS": 0.6},
    ]
    assert 0 <= multiclass_brier(actual, predicted) < 1
    assert multiclass_log_loss(actual, predicted) > 0

    binary = binary_metrics([1, 0, 1, 0], [0.8, 0.2, 0.7, 0.3])
    assert binary["n"] == 4.0
    assert 0 <= binary["brier"] < 1
    assert 0 <= binary["ece"] <= 1
