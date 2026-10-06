import numpy as np
import pandas as pd
import pytest

from v17.experiments.live_moneyline_challenger import (
    LiveChallengerError,
    fit_research_bundle,
    score_research_state,
)
from v17.experiments.live_moneyline_replay_data import MLB_FEATURES, NFL_FEATURES


def _frame(n: int, features, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    data = {name: rng.normal(size=n) for name in features}
    score = data[features[0]]
    time = data[features[1]]
    logits = 1.4 * score - 0.35 * time + rng.normal(scale=0.7, size=n)
    p = 1.0 / (1.0 + np.exp(-logits))
    data["home_win"] = rng.binomial(1, p)
    data["game_id"] = [f"g{seed}-{i // 12}" for i in range(n)]
    return pd.DataFrame(data)


@pytest.mark.parametrize("sport,features", [("NFL", NFL_FEATURES), ("MLB", MLB_FEATURES)])
def test_live_challenger_is_research_only_and_normalized(sport, features):
    bundle = fit_research_bundle(
        sport=sport,
        train=_frame(1800, features, 1),
        calibration=_frame(700, features, 2),
        validation=_frame(700, features, 3),
        feature_names=features,
    )
    assert bundle.probability_publishable is False
    assert bundle.promotion_authorized is False
    assert bundle.can_execute is False
    state = {name: 0.25 for name in features}
    result = score_research_state(bundle, state)
    assert 0 < result["research_home_probability"] < 1
    assert result["research_home_probability"] + result["research_away_probability"] == pytest.approx(1.0)
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["promotion_authorized"] is False
    assert result["can_execute"] is False


def test_live_challenger_rejects_market_or_external_probability_features():
    good = _frame(1800, ("score_diff", "seconds_remaining"), 4)
    cal = _frame(700, ("score_diff", "seconds_remaining"), 5)
    val = _frame(700, ("score_diff", "seconds_remaining"), 6)
    for bad in ("vegas_wp", "market_implied_probability", "pregame_probability", "moneyline_odds"):
        for frame in (good, cal, val):
            frame[bad] = 0.5
        with pytest.raises(LiveChallengerError, match="FORBIDDEN_FEATURE"):
            fit_research_bundle(
                sport="NFL",
                train=good,
                calibration=cal,
                validation=val,
                feature_names=("score_diff", "seconds_remaining", bad),
            )


def test_live_challenger_requires_exact_inference_feature_set():
    features = ("score_diff", "seconds_remaining")
    bundle = fit_research_bundle(
        sport="NFL",
        train=_frame(1800, features, 7),
        calibration=_frame(700, features, 8),
        validation=_frame(700, features, 9),
        feature_names=features,
    )
    with pytest.raises(LiveChallengerError, match="FEATURE_SET_MISMATCH"):
        score_research_state(bundle, {"score_diff": 1.0})


def test_live_feature_contracts_contain_no_market_or_public_win_probability():
    forbidden = ("wp", "odds", "market", "implied", "pregame_probability", "spread", "moneyline")
    for features in (NFL_FEATURES, MLB_FEATURES):
        for feature in features:
            assert not any(token in feature.casefold() for token in forbidden)
