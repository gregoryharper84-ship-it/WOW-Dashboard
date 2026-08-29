"""Regression tests for binding WNBA Full Model evidence-packet enforcement."""
from __future__ import annotations

from datetime import datetime, timezone


def _box_rows(n: int = 10):
    out = []
    for i in range(n):
        out.append({
            "game_date": f"2026-08-{10+i:02d}",
            "opponent": f"OPP{i}",
            "minutes": 32.0,
            "points": 18 + (i % 4),
            "rebounds": 5 + (i % 3),
            "assists": 4 + (i % 2),
            "field_goal_attempts": 15 + (i % 3),
            "usage_rate": 0.24,
            "starter_flag": True,
            "role": "STARTER",
            "source_timestamp": "2026-08-28T17:00:00Z",
        })
    return out


def _row(stat: str = "PTS", line: float = 18.5):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "sport": "WNBA",
        "player_name": "Evidence Player",
        "opponent": "Evidence Opponent",
        "event_id": "WNBA-EVT-1",
        "stat_key": stat,
        "line": line,
        "side": "MORE",
        "role_status": "STARTER",
        "starter_flag": True,
        "role_timestamp": now,
        "status_timestamp": now,
        "gates": {
            "role_timestamp": {
                "passed": True,
                "role_staleness": "FRESH",
                "status_staleness": "FRESH",
            },
            "wnba_opportunity_gate": {
                "gate_passed": True,
                "gate_label": "PASS",
                "expected_minutes": 32.0,
                "usage_stability_score": 82,
                "shot_attempt_stability_score": 84,
                "opportunity_stability_score": 83,
                "role_state": "SECONDARY_CREATOR",
                "role_confidence": 0.9,
            },
        },
        "blockers": [],
        "can_execute": False,
    }


def _enr(stat: str = "PTS"):
    data = {
        "game_log": [17, 20, 21, 19, 22, 18, 24, 20, 19, 23],
        "box_score_log": _box_rows(),
        "projected_minutes": 32.0,
        "projected_pace": 81.5,
        "opponent_defense": {"def_rating": 103.5},
        "rest_days": 2,
        "blowout_probability": 0.12,
        "game_script": {"expected_margin": 4.0, "minutes_adjustment": 0.0},
    }
    if stat == "2PM":
        data["two_point_attempts_per_minute"] = 0.31
        data["script_adjusted_two_point_opportunity"] = 10.1
    return data


def test_missing_game_log_blocks_model_publication():
    from gate_engine.wnba import full_model_evidence_gate as eg
    packet = eg.build(_row(), {k: v for k, v in _enr().items() if k != "game_log"})
    assert packet["status"] == "RUN_INCOMPLETE"
    assert packet["failure_class"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert packet["probability_publication_allowed"] is False
    assert any("GAME_LOG" in b for b in packet["model_blockers"])


def test_missing_box_score_log_cannot_be_substituted_by_numeric_game_log():
    from gate_engine.wnba import full_model_evidence_gate as eg
    enr = _enr()
    enr.pop("box_score_log")
    packet = eg.build(_row(), enr)
    assert packet["model_input_ready"] is False
    assert any("BOX_SCORE_LOG" in b for b in packet["model_blockers"])


def test_game_log_and_box_score_log_types_are_distinct_and_visible():
    from gate_engine.wnba import full_model_evidence_gate as eg
    packet = eg.build(_row(), _enr())
    ledger = packet["historical_ledger"]
    assert ledger["game_log_type"] == "list[number]"
    assert ledger["box_score_log_type"] == "list[dict]"
    assert ledger["l5_values"] == ledger["l10_values"][-5:]
    assert ledger["sample_count"] == 10


def test_less_than_ten_exact_observations_blocks_full_model():
    from gate_engine.wnba import full_model_evidence_gate as eg
    enr = _enr()
    enr["game_log"] = enr["game_log"][-9:]
    packet = eg.build(_row(), enr)
    assert packet["model_input_ready"] is False
    assert any("EXACT_L10_INCOMPLETE" in b for b in packet["model_blockers"])


def test_stale_or_missing_role_timestamp_blocks_probability_publication():
    from gate_engine.wnba import full_model_evidence_gate as eg
    row = _row()
    row["role_timestamp"] = None
    row["gates"]["role_timestamp"] = {
        "passed": False,
        "role_staleness": "STALE",
        "status_staleness": "FRESH",
    }
    packet = eg.build(row, _enr())
    assert packet["probability_publication_allowed"] is False
    assert any("ROLE_TIMESTAMP" in b for b in packet["model_blockers"])


def test_role_valid_ess_is_exposed_and_passes_for_matching_sample():
    from gate_engine.wnba import full_model_evidence_gate as eg
    packet = eg.build(_row(), _enr())
    rv = packet["role_valid_sample"]
    assert rv["status"] == "PASS"
    assert rv["effective_sample_size"] == 10.0
    assert len(rv["rows"]) == 10


def test_missing_game_script_context_blocks_model_but_market_is_separate():
    from gate_engine.wnba import full_model_evidence_gate as eg
    enr = _enr()
    enr.pop("game_script")
    packet = eg.build(_row(), enr)
    assert packet["model_input_ready"] is False
    assert any("GAME_SCRIPT" in b for b in packet["model_blockers"])


def test_missing_market_comparison_does_not_block_complete_sporting_model():
    from gate_engine.wnba import full_model_evidence_gate as eg
    packet = eg.build(_row(), _enr())
    assert packet["model_input_ready"] is True
    assert packet["market_comparison"]["status"] == "UNAVAILABLE"
    assert packet["market_comparison"]["blocks_model_probability"] is False
    assert packet["market_blockers"] == ["MARKET_COMPARISON_UNAVAILABLE"]


def test_integer_line_exposes_more_less_and_push_results():
    from gate_engine.wnba import full_model_evidence_gate as eg
    enr = _enr()
    enr["game_log"] = [17, 18, 18, 19, 20, 18, 16, 21, 18, 22]
    packet = eg.build(_row(line=18.0), enr)
    exact = packet["historical_ledger"]["l10_exact_line_results"]
    assert exact["push"] == 4
    assert exact["more"] + exact["less"] + exact["push"] == 10


def test_generative_gate_does_not_call_model_when_evidence_incomplete(monkeypatch):
    from gate_engine import wnba_generative_gate as gate
    row = _row()
    enr = _enr()
    enr.pop("game_log")

    def _should_not_run(*args, **kwargs):
        raise AssertionError("generative model must not run with incomplete evidence")

    monkeypatch.setattr(gate._gen, "score", _should_not_run)
    gate.run(row, enr)
    assert row["gates"]["wnba_generative"]["model_status"] == "NOT_STARTED"
    assert row["gates"]["wnba_generative"]["failure_class"] == "RUN_INVALID_ACQUISITION_INCOMPLETE"
    assert row["probability_publication_allowed"] is False
    assert row["terminal_label"] == "REJECT_DATA_QUALITY"


def test_2pm_requires_2pm_opportunity_and_reports_model_unavailable():
    from gate_engine import wnba_generative_gate as gate
    row = _row(stat="2PM", line=4.5)
    gate.run(row, _enr("2PM"))
    result = row["gates"]["wnba_generative"]
    assert result["model_status"] == "MODEL_UNAVAILABLE"
    assert "WNBA_2PM_CONTROLLING_MODEL_UNSUPPORTED" in result["blockers"]
    assert result["probability_publication_allowed"] is False


def test_visible_packet_contains_required_full_model_audit_fields():
    from gate_engine.wnba import full_model_evidence_gate as eg
    packet = eg.build(_row(), _enr())
    for key in (
        "exact_board_identity",
        "historical_ledger",
        "role_status",
        "role_timestamp_gate",
        "role_valid_sample",
        "opportunity_ledger",
        "matchup_game_script_model",
        "market_comparison",
        "source_timestamps",
        "terminal_ceiling",
        "model_blockers",
        "market_blockers",
    ):
        assert key in packet
    assert packet["evidence_packet_visible"] is True
    assert packet["can_execute"] is False
