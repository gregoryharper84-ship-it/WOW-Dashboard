from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v17.telemetry_fsm import TelemetryStateMachine, TransitionRule


def test_event_driven_fsm_handles_delayed_penalty_and_extra_attacker_without_clock_heuristic():
    machine = TelemetryStateMachine(
        initial_state="EQUAL_STRENGTH",
        rules=[
            TransitionRule(
                "DELAYED_PENALTY_EXTRA_ATTACKER",
                "EQUAL_STRENGTH",
                "EXTRA_ATTACKER",
                lambda e: e.get("delayed_penalty_flag") is True and e.get("goalie_on_ice") is False,
            ),
            TransitionRule(
                "WHISTLE_ENFORCES_PENALTY",
                "EXTRA_ATTACKER",
                "PENALTY_ENFORCED",
                lambda e: e.get("whistle") is True,
            ),
        ],
    )
    receipt = machine.replay(
        [
            {"game_seconds_remaining": 900, "delayed_penalty_flag": True, "goalie_on_ice": False},
            {"game_seconds_remaining": 895, "whistle": True},
        ]
    )
    assert receipt["valid"] is True
    assert receipt["terminal_state"] == "PENALTY_ENFORCED"
    assert [x["rule_id"] for x in receipt["transitions"]] == [
        "DELAYED_PENALTY_EXTRA_ATTACKER",
        "WHISTLE_ENFORCES_PENALTY",
    ]


def test_ambiguous_transition_fails_typed():
    machine = TelemetryStateMachine(
        initial_state="A",
        rules=[
            TransitionRule("R1", "A", "B", lambda e: e.get("x") == 1),
            TransitionRule("R2", "A", "C", lambda e: e.get("x") == 1),
        ],
    )
    receipt = machine.replay([{"x": 1}])
    assert receipt["valid"] is False
    assert receipt["typed_failure"] == "FSM_AMBIGUOUS_TRANSITION"
