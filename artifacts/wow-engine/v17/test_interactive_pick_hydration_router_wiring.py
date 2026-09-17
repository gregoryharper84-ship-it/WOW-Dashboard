from __future__ import annotations

import v17.interactive_pick_hydration as subject
from pick_request_runtime_core import PickRequestRow


def _raw_evidence() -> dict:
    return {
        "captured_at": "2026-09-17T12:00:00+00:00",
        "game_log": [18.0] * 10,
        "box_score_log": [{"game": index, "outs": 18} for index in range(10)],
        "role_status": {"status": "STARTER"},
        "role_timestamp": "2026-09-17T12:00:00+00:00",
        "opportunity_ledger": {"status": "PASS"},
        "source_timestamps": {"official": "2026-09-17T12:00:00+00:00"},
        "rate_provenance": "OFFICIAL_TEST",
    }


def test_interactive_hydration_uses_canonical_runtime_hydrator_seam(monkeypatch):
    called = {}

    def routed_hydrator(**kwargs):
        called.update(kwargs)
        return _raw_evidence()

    monkeypatch.setattr(subject.pick_core, "auto_hydrate_prop_evidence", routed_hydrator)

    row = PickRequestRow(
        event_id="777",
        event_start_time="2026-09-18T00:00:00+00:00",
        sport="MLB",
        player="Workload Pitcher",
        stat_type="PITCHING_OUTS",
        line=17.5,
        direction="MORE",
        source_type="NORMALIZED",
    )

    evidence = subject._hydrate(row)

    assert called["sport"] == "MLB"
    assert called["stat_type"] == "PITCHING_OUTS"
    assert called["player"] == "Workload Pitcher"
    assert evidence.game_log == [18.0] * 10
    assert evidence.rate_provenance == "OFFICIAL_TEST"
