import pytest

from mlb_1ip_bf_model import (
    BFObservation,
    RECENT_HISTORY_LIMIT,
    bf_bucket,
    binary_metrics,
    counts_from_buckets,
    fit_league_prior,
    history_buckets,
    history_counts,
    multiclass_brier,
    multiclass_log_loss,
    score_pitcher,
    update_history_buckets,
)


def _rows(n=120, *, pitcher_mod=5):
    out = []
    for i in range(n):
        bf = 3 if i % 3 == 0 else 4 if i % 3 == 1 else 5
        out.append(BFObservation(
            pitcher_id=100 + (i % pitcher_mod),
            bf=bf,
            event_time=f"2024-{(i // 28) + 1:02d}-{(i % 28)+1:02d}T00:00:00Z",
            game_pk=i + 1,
        ))
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


def test_history_is_capped_to_live_recent_10_contract():
    rows = _rows(30, pitcher_mod=1)
    histories = history_buckets(rows)
    assert len(histories[100]) == RECENT_HISTORY_LIMIT == 10
    expected = [bf_bucket(r.bf) for r in rows[-10:]]
    assert histories[100] == expected
    counts = history_counts(rows)[100]
    assert sum(counts.values()) == 10
    assert counts == counts_from_buckets(histories, 100)


def test_score_pitcher_shrinks_to_league_prior_without_history():
    prior = {"3": 0.4, "4": 0.35, "5_PLUS": 0.25}
    scored = score_pitcher(pitcher_id=1, league_prior=prior, pitcher_counts={}, alpha=10)
    assert scored["P_BF_3"] == prior["3"]
    assert scored["P_BF_4"] == prior["4"]
    assert scored["P_BF_GE_5"] == prior["5_PLUS"]
    assert scored["P_MORE_3_5"] == prior["4"] + prior["5_PLUS"]
    assert scored["P_MORE_4_5"] == prior["5_PLUS"]
    assert scored["history_limit"] == 10
    assert scored["probability_publishable"] is False
    assert scored["rank_eligible"] is False
    assert scored["can_execute"] is False


def test_score_pitcher_updates_from_recent_history_without_losing_normalization():
    prior = {"3": 0.4, "4": 0.35, "5_PLUS": 0.25}
    history = {7: {"3": 8, "4": 1, "5_PLUS": 1}}
    scored = score_pitcher(pitcher_id=7, league_prior=prior, pitcher_counts=history, alpha=10)
    total = scored["P_BF_3"] + scored["P_BF_4"] + scored["P_BF_GE_5"]
    assert abs(total - 1.0) < 1e-12
    assert scored["P_BF_3"] > prior["3"]
    assert scored["pitcher_history_n"] == 10


def test_score_rejects_history_beyond_certified_window():
    prior = {"3": 0.4, "4": 0.35, "5_PLUS": 0.25}
    with pytest.raises(ValueError, match="MLB_1IP_BF_HISTORY_EXCEEDS_CERTIFIED_WINDOW"):
        score_pitcher(
            pitcher_id=7,
            league_prior=prior,
            pitcher_counts={7: {"3": 9, "4": 1, "5_PLUS": 1}},
            alpha=10,
        )


def test_history_update_is_post_prediction_primitive_and_trims_oldest():
    rows = _rows(12, pitcher_mod=1)
    histories = history_buckets(rows[:10])
    before = list(histories[100])
    row = rows[10]
    update_history_buckets(histories, row)
    assert len(histories[100]) == 10
    assert histories[100][:-1] == before[1:]
    assert histories[100][-1] == bf_bucket(row.bf)


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
