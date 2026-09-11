from pathlib import Path

from kalshi_weather_v2.shadow_cohort import build_calibration_report


class _Query:
    def select(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        class Result:
            data = []
        return Result()


class _Client:
    def table(self, _name):
        return _Query()


def test_empty_empirical_report_cannot_promote_capability():
    report = build_calibration_report(client=_Client())
    assert report["probability_publishable"] is False
    assert report["capability_promotion_allowed"] is False
    assert report["can_execute"] is False


def test_precommitted_acceptance_thresholds_are_not_implicit():
    text = Path(__file__).with_name("EMPIRICAL_ACCEPTANCE_THRESHOLDS.md").read_text()
    assert "50 unique weather-target residuals total" in text
    assert "20 chronologically later holdout residuals" in text
    assert "<= 0.75°F" in text
    assert "<= 2.5°F" in text
    assert "Brier score" in text
    assert "market price was not used as a model input" in text
