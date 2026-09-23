from __future__ import annotations

import pytest

import prop_auto_hydration as hydration


@pytest.mark.parametrize("stat_type", ["PITCHING_OUTS", "STRIKES_THROWN", "BALLS_THROWN"])
def test_min3_strikeout_candidate_does_not_relax_workload_history_floor(monkeypatch, stat_type):
    observed = {}

    def fake_workload_hydrator(**kwargs):
        observed["min_starts"] = kwargs["min_starts"]
        observed["stat_type"] = kwargs["stat_type"]
        raise hydration.PropAutoHydrationError("ADJACENT_ROUTE_SENTINEL", "stop after routing proof")

    monkeypatch.setattr(hydration, "hydrate_mlb_workload_evidence", fake_workload_hydrator)

    with pytest.raises(hydration.PropAutoHydrationError) as exc:
        hydration.auto_hydrate_prop_evidence(
            sport="MLB",
            player="Adjacent Route Pitcher",
            stat_type=stat_type,
            event_start_time="2026-09-24T00:00:00+00:00",
        )

    assert exc.value.code == "ADJACENT_ROUTE_SENTINEL"
    assert observed["stat_type"] == stat_type
    assert observed["min_starts"] == 10
    assert hydration.MIN_STARTS == 10
    assert hydration.PITCHER_STRIKEOUT_MIN_REQUIRED_STARTS == 3
