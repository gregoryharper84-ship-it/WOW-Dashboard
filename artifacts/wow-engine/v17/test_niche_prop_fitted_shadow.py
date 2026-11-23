"""Synthetic-only fitted-niche shadow tests: no licensed provider, no production probabilities."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import math

import pytest
from v17.niche_prop_fitted_shadow import (
    NicheShadowHold, fit_niche_shadow, predict_niche_shadow,
    FEATURE_KEYS,
)


ANCHOR = datetime(2024, 1, 1, tzinfo=timezone.utc)


def dt(d, h=0):
    return (ANCHOR + timedelta(days=d, hours=h)).isoformat()


def row(i, *, stat="FREE_THROW_ATTEMPTS", result=True, season="train"):
    features = {
        "projected_minutes": 18.0 + (i % 25),
        "stat_opportunities_per_min_prior": 0.04 + (i % 5) * .02,
        "opponent_adjustment_prior": .8 + (i % 7) * .07,
        "game_pace_prior": 88.0 + (i % 20),
        "usage_shift_prior": (i % 9 - 4) * .04,
    }
    line = 2.5 + (i % 4)
    # This number is synthetic test settlement, never a sports prediction.
    actual = line - 1 if result else line + 1
    return {
        "row_id": f"{season}-{i}",
        "category": "NBA_LOW_VOLUME_UNDER",
        "sport": "NBA",
        "stat_type": stat,
        "market_period": "FULL_GAME",
        "side": "LESS",
        "exact_line": line,
        "official_event_id": f"synthetic-official-event-{i}",
        "official_player_id": f"synthetic-player-{i}",
        "event_start_utc": dt(i, 12),
        "features_as_of_utc": dt(i, 2),
        "feature_snapshot_hash": hashlib.sha256(f"synthetic-snapshot-{i}".encode()).hexdigest(),
        "source_receipt_id": f"synthetic-claimed-source-{i}",
        "features": features,
        "actual_stat": actual,
        "settled_result": "WIN" if result else "LOSS",
        "settlement_at_utc": dt(i, 15),
        "settlement_source_ref": f"synthetic-settlement-{i}",
    }


def cohorts():
    training = [row(i, result=((i * 17 + 3) % 11 < 6)) for i in range(1, 95)]
    calibration = [row(i, result=((i * 13 + 3) % 11 < 5)) for i in range(100, 140)]
    test = [row(i, result=((i * 17 + 1) % 13 < 7)) for i in range(150, 190)]
    return training, calibration, test


def test_fit_is_deterministic_only_research_and_uses_untouched_test():
    train, cal, test = cohorts()
    saved = deepcopy((train, cal, test))
    fitted = fit_niche_shadow(train, cal, test)
    repeat = fit_niche_shadow(train, cal, test)
    assert (train, cal, test) == saved
    assert fitted == repeat
    assert fitted["status"] == "SHADOW_UNCERTIFIED"
    assert fitted["artifact"]["model_digest"] == repeat["artifact"]["model_digest"]
    assert fitted["artifact"]["feature_keys"][-1] == "exact_line"
    assert len(fitted["artifact"]["weights"]) == len(FEATURE_KEYS["NBA_LOW_VOLUME_UNDER"]) + 2
    assert set(("brier", "log_loss", "ece", "sample_count")) <= set(fitted["holdout_metrics"])
    assert fitted["holdout_metrics"]["sample_count"] == len(test)
    assert all(math.isfinite(fitted["holdout_metrics"][k]) for k in ("brier", "log_loss", "ece"))
    assert fitted["calibrated_probability"] is None
    assert fitted["certified_joint_probability"] is None
    assert fitted["can_execute"] is False


def test_predict_research_only_and_digest_bound_to_exact_features():
    train, cal, test = cohorts()
    artifact = fit_niche_shadow(train, cal, test)["artifact"]
    candidate = row(200)
    candidate = {k: v for k, v in candidate.items()
                 if k not in ("actual_stat", "settled_result", "settlement_at_utc", "settlement_source_ref")}
    p = predict_niche_shadow(artifact, candidate)
    assert 0 < p["candidate_probability"] < 1
    assert p["certified_probability"] is None
    assert p["calibrated_lower_bound"] is None
    assert p["status"] == "SHADOW_UNCERTIFIED"
    assert not p["can_execute"]
    assert p["pregame_receipt_hash"]
    altered = deepcopy(candidate)
    altered["features"]["projected_minutes"] += 5
    assert p["pregame_receipt_hash"] != predict_niche_shadow(artifact, altered)["pregame_receipt_hash"]
    swapped_player = dict(candidate, official_player_id="another-canonical-player")
    assert p["pregame_receipt_hash"] != predict_niche_shadow(artifact, swapped_player)["pregame_receipt_hash"]
    with pytest.raises(NicheShadowHold, match="PREGAME_INPUT_CONTAINS_RESULT"):
        predict_niche_shadow(artifact, row(201))


@pytest.mark.parametrize("tamper", (
    lambda a,b,c: b.__setitem__(0, dict(b[0], event_start_utc=a[-1]["event_start_utc"])),
    lambda a,b,c: c.__setitem__(0, dict(c[0], official_event_id=a[0]["official_event_id"])),
    lambda a,b,c: a.__setitem__(0, dict(a[0], features_as_of_utc=a[0]["event_start_utc"])),
    lambda a,b,c: a.__setitem__(0, dict(a[0], settled_result="LOSS" if a[0]["settled_result"]=="WIN" else "WIN")),
    lambda a,b,c: a.__setitem__(0, dict(a[0], stat_type="TURNOVERS")),
    lambda a,b,c: a.__setitem__(0, dict(a[0], features={"projected_minutes": 20.0})),
    lambda a,b,c: a.__setitem__(0, dict(a[0], features={**a[0]["features"], "leaked_final_box_score": 12})),
    lambda a,b,c: a.__setitem__(0, dict(a[0], features={**a[0]["features"], "projected_minutes": float("nan")})),
    lambda a,b,c: a.__setitem__(0, dict(a[0], settlement_source_ref="")),
))
def test_fail_closed_on_leakage_identity_lineage_and_malformed_data(tamper):
    a, b, c = cohorts()
    tamper(a,b,c)
    with pytest.raises(NicheShadowHold):
        fit_niche_shadow(a,b,c)


def test_no_shortcut_small_training_and_label_cherry_pick():
    a,b,c = cohorts()
    with pytest.raises(NicheShadowHold, match="MINIMUM_COHORT"):
        fit_niche_shadow(a[:10],b,c)
    for item in a:
        item["settled_result"] = "WIN"
        item["actual_stat"] = item["exact_line"]-1
    with pytest.raises(NicheShadowHold, match="CLASS_DIVERSITY"):
        fit_niche_shadow(a,b,c)


@pytest.mark.parametrize("route,stat", [
    ("NFL_1Q_TWO_KICKER_FG_OVER_0_5", "FIELD_GOALS_MADE_COMBO"),
    ("NFL_1Q_QB_PASS_ATTEMPTS_OVER", "PASS_ATTEMPTS"),
    ("NFL_1Q_QB_FANTASY_SCORE_OVER", "FANTASY_SCORE"),
])
def test_all_nfl_niche_specialists_fit_distinct_research_shadows(route, stat):
    def football(r):
        x = dict(r)
        x["sport"], x["category"], x["stat_type"] = "NFL", route, stat
        x["market_period"], x["side"] = "FIRST_QUARTER", "MORE"
        x["features"] = {key: (1.0 + (int(x["row_id"].rsplit("-", 1)[1]) % 7) * 0.1)
                         for key in FEATURE_KEYS[route]}
        if route == "NFL_1Q_TWO_KICKER_FG_OVER_0_5":
            x["exact_line"] = 0.5
            x["combo_participant_ids"] = ["synthetic-kicker-home", "synthetic-kicker-away"]
            x["combo_team_ids"] = ["HOME", "AWAY"]
            x["home_team_id"], x["away_team_id"] = "HOME", "AWAY"
        else:
            x["official_player_position"] = "QB"
        x["actual_stat"] = (x["exact_line"] + 1 if x["settled_result"] == "WIN"
                            else max(0, x["exact_line"] - 1))
        return x

    a,b,c = cohorts()
    trained = fit_niche_shadow(
        [football(x) for x in a],
        [football(x) for x in b],
        [football(x) for x in c],
    )
    assert trained["artifact"]["category"] == route
    assert trained["holdout_metrics"]["sample_count"] == len(c)
    assert trained["artifact"]["publication_allowed"] is False
