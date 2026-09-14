from __future__ import annotations

from types import SimpleNamespace

import kalshi_weather_v2.bounded_cohort as bounded
import kalshi_weather_v2.empirical_runtime as runtime
from kalshi_weather_v2.shadow_cohort import HourlyCohortTarget


def _target(name: str = "miami") -> HourlyCohortTarget:
    return HourlyCohortTarget(
        index_city=name,
        expected_location=name.title(),
        forecast_latitude=25.0,
        forecast_longitude=-80.0,
        forecast_reference_note="test only",
    )


def test_capture_only_pass_does_not_settle_backlog(monkeypatch):
    closed = {"value": False}

    class FakeHttp:
        def close(self):
            closed["value"] = True

    monkeypatch.setattr(bounded, "ReadOnlyJsonClient", FakeHttp)
    monkeypatch.setattr(bounded, "_existing_sample_keys", lambda _db: set())
    monkeypatch.setattr(
        bounded,
        "_discover_target_contracts",
        lambda _http, _target, _decision_time: (),
    )

    result = bounded.run_bounded_capture_only_cohort_once(
        db_client_fn=lambda: SimpleNamespace(),
        targets=(_target(),),
        now="2026-09-14T14:00:00Z",
    )

    assert result.status == "EMPIRICAL_SHADOW_COHORT_PASS"
    assert result.targets_checked == 1
    assert result.contracts_discovered == 0
    assert result.predictions_settled == 0
    assert result.settlement_failures == ()
    assert closed["value"] is True


def test_scheduler_defaults_to_one_rotating_target(monkeypatch):
    monkeypatch.delenv("WOW_KALSHI_WEATHER_EMPIRICAL_MAX_TARGETS", raising=False)
    selected = runtime._scheduled_shadow_targets()
    all_targets = runtime._automated_shadow_targets()

    assert len(all_targets) == 3
    assert len(selected) == 1
    assert selected[0] in all_targets


def test_scheduler_can_expand_target_slice_explicitly(monkeypatch):
    monkeypatch.setenv("WOW_KALSHI_WEATHER_EMPIRICAL_MAX_TARGETS", "3")
    selected = runtime._scheduled_shadow_targets()

    assert len(selected) == 3
    assert {target.index_city for target in selected} == {
        "miami",
        "chicago",
        "la-coastal",
    }
