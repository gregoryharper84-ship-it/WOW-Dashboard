from __future__ import annotations

import pytest

from agent_runtime.coordinator_v17 import classify_model_terminal


@pytest.mark.parametrize(
    ("status", "blockers", "expected"),
    [
        ("BLOCKED", ["MODEL_UNAVAILABLE"], ("MODEL_UNAVAILABLE", "MODEL_UNAVAILABLE")),
        ("BLOCKED", ["MODEL_INPUTS_INSUFFICIENT"], ("MODEL_INPUTS_INSUFFICIENT", "RESEARCH_INTEREST")),
        ("BLOCKED", ["MODEL_OUTPUT_INVALID"], ("MODEL_OUTPUT_INVALID", "RESEARCH_INTEREST")),
        ("BLOCKED", ["EVENT_MODEL_BRIDGE_INVALID_RESPONSE"], ("MODEL_OUTPUT_INVALID", "RESEARCH_INTEREST")),
        ("TIMED_OUT", ["WORKER_TIMED_OUT"], ("MODEL_SCORER_FAILED", "RESEARCH_INTEREST")),
        ("DEAD_LETTERED", ["WORKER_DEAD_LETTERED"], ("MODEL_SCORER_FAILED", "RESEARCH_INTEREST")),
        ("BLOCKED", ["MODEL_SCORER_FAILED"], ("MODEL_SCORER_FAILED", "RESEARCH_INTEREST")),
        ("BLOCKED", ["CONTROLLING_MODEL_PROVIDER_NOT_WIRED"], ("CONTROLLING_MODEL_PROVIDER_NOT_WIRED", "RESEARCH_INTEREST")),
        ("BLOCKED", [], ("MODEL_SCORER_FAILED", "RESEARCH_INTEREST")),
    ],
)
def test_classify_model_terminal_preserves_v17_failure_type(status, blockers, expected):
    assert classify_model_terminal(status, blockers) == expected


def test_only_true_capability_unavailable_gets_model_unavailable_ceiling():
    cases = [
        ("BLOCKED", ["MODEL_INPUTS_INSUFFICIENT"]),
        ("TIMED_OUT", ["WORKER_TIMED_OUT"]),
        ("DEAD_LETTERED", ["WORKER_DEAD_LETTERED"]),
        ("BLOCKED", ["MODEL_OUTPUT_INVALID"]),
        ("BLOCKED", ["CONTROLLING_MODEL_PROVIDER_NOT_WIRED"]),
    ]
    for status, blockers in cases:
        label, ceiling = classify_model_terminal(status, blockers)
        assert label != "MODEL_UNAVAILABLE"
        assert ceiling == "RESEARCH_INTEREST"


def test_explicit_backend_typed_failure_is_not_rewritten():
    label, ceiling = classify_model_terminal("BLOCKED", ["CUSTOM_TYPED_SCORER_COMPLETION_FAILURE"])
    assert label == "CUSTOM_TYPED_SCORER_COMPLETION_FAILURE"
    assert ceiling == "RESEARCH_INTEREST"
