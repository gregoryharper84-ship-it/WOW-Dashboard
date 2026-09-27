from __future__ import annotations

from types import SimpleNamespace

from v17.mlb_run_line_shadow import run_mlb_run_line_shadow_preflight, score_final_run_line_samples


class _Query:
    def __init__(self, rows): self.rows = rows
    def select(self, *_args, **_kwargs): return self
    def eq(self, *_args, **_kwargs): return self
    def limit(self, *_args, **_kwargs): return self
    def execute(self): return SimpleNamespace(data=self.rows)


class _DB:
    def table(self, name):
        if name == "wow_mlb_forward_score_snapshots":
            return _Query([{ "score_snapshot_id": "s1", "shadow_event_id": "e1", "distribution_id": "d1", "home_mu": 4.7, "away_mu": 4.1, "score_status": "PASS", "probability_publishable": False, "can_execute": False }])
        if name == "wow_mlb_v2b_distribution_state":
            return _Query([{ "distribution_id": "d1", "model_version": "MLB_DIST", "home_alpha_total": .3, "away_alpha_total": .35, "extra_inning_home_win_probability": .5, "extra_inning_training_games": 315, "training_end": "2025-08-09", "research_only": True, "probability_publishable": False, "can_execute": False }])
        raise AssertionError(name)


def test_final_run_line_sample_scoring_supports_half_and_whole_lines():
    half = score_final_run_line_samples([5, 4, 3, 7], [3, 4, 5, 6], home_run_line=-1.5)
    assert half == {"p_cover": .25, "p_push": 0.0, "p_not_cover": .75, "distribution_sample_n": 4}
    whole = score_final_run_line_samples([5, 4, 3, 7], [3, 4, 5, 6], home_run_line=-1.0)
    assert whole == {"p_cover": .25, "p_push": .25, "p_not_cover": .5, "distribution_sample_n": 4}


def test_preflight_fails_closed_without_extra_inning_final_margin_model():
    result = run_mlb_run_line_shadow_preflight(_DB(), score_snapshot_id="s1", home_run_line=-1.5)
    assert result["status"] == "BLOCKED"
    assert result["code"] == "MLB_RUN_LINE_EXTRA_INNING_MARGIN_MODEL_UNAVAILABLE"
    assert result["moneyline_probability_used"] is False
    assert result["market_probability_substitution_used"] is False
    assert result["run_line_used_as_feature"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
