import copy
import json

import pytest

from v17 import scout_brain_transport_sanitizer as sanitizer


def _handoff():
    return {
        "can_execute": False,
        "run_id": "r1",
        "model_handoff": {
            "team_event_candidates": [
                {
                    "can_execute": False,
                    "official_event_id": "evt-1",
                    "route": "LLP_TEAM_BETTING_ENGINE",
                    "market_evidence": [{"bookmaker": "active", "price": -110}],
                    "market_evidence_historical": [{"bookmaker": "old-1"}, {"bookmaker": "old-2"}],
                    "market_evidence_stale": [{"bookmaker": "stale"}],
                    "market_evidence_summary": {"active_rows": 1, "historical_rows": 2, "stale_rows": 1},
                }
            ],
            "prop_candidates": [
                {
                    "can_execute": False,
                    "official_event_id": "prop-1",
                    "route": "WOW_PROP_LANE",
                    "market_evidence": {"bookmaker": "active-prop", "price": -105},
                    "market_evidence_historical": [{"bookmaker": "old-prop"}],
                }
            ],
        },
    }


def test_transport_sanitizer_removes_only_diagnostic_arrays():
    body = _handoff()
    before = copy.deepcopy(body)

    counts = sanitizer.sanitize_handoff(body)

    team = body["model_handoff"]["team_event_candidates"][0]
    prop = body["model_handoff"]["prop_candidates"][0]
    assert team["market_evidence"] == before["model_handoff"]["team_event_candidates"][0]["market_evidence"]
    assert prop["market_evidence"] == before["model_handoff"]["prop_candidates"][0]["market_evidence"]
    assert team["market_evidence_summary"] == before["model_handoff"]["team_event_candidates"][0]["market_evidence_summary"]
    assert "market_evidence_historical" not in team
    assert "market_evidence_stale" not in team
    assert "market_evidence_historical" not in prop
    assert counts == {
        "market_evidence_historical": 3,
        "market_evidence_stale": 1,
        "candidates": 2,
    }
    assert body["can_execute"] is False
    assert team["can_execute"] is False
    assert prop["can_execute"] is False


def test_transport_sanitizer_shrinks_repeated_metadata_materially():
    body = _handoff()
    team = body["model_handoff"]["team_event_candidates"][0]
    team["market_evidence_historical"] = [{"blob": "h" * 1000} for _ in range(200)]
    team["market_evidence_stale"] = [{"blob": "s" * 1000} for _ in range(200)]
    before = len(json.dumps(body, separators=(",", ":")).encode())

    sanitizer.sanitize_handoff(body)
    after = len(json.dumps(body, separators=(",", ":")).encode())

    assert before > 400_000
    assert after < 10_000


def test_transport_sanitizer_fails_closed_on_top_level_governance():
    body = _handoff()
    body["can_execute"] = True
    with pytest.raises(RuntimeError, match="SCOUT_HANDOFF_GOVERNANCE_INVALID"):
        sanitizer.sanitize_handoff(body)


def test_transport_sanitizer_fails_closed_on_candidate_governance():
    body = _handoff()
    body["model_handoff"]["team_event_candidates"][0]["can_execute"] = True
    with pytest.raises(RuntimeError, match="SCOUT_HANDOFF_GOVERNANCE_INVALID"):
        sanitizer.sanitize_handoff(body)
