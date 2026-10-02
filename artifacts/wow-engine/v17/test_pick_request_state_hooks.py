from types import SimpleNamespace

import v17.pick_request_state_hooks as hooks


def test_validate_hook_forwards_normalized_payload_to_readiness_recorder(monkeypatch):
    calls = {}

    def original_validate(row, canonical_stat):
        calls["original"] = (row, canonical_stat)
        return {"event_id": "evt-1", "sport": "NFL", "player": "Player 1"}

    def record(row, normalized):
        calls["record"] = (row, normalized)

    monkeypatch.setattr(hooks, "_INSTALLED", False)
    monkeypatch.setattr(hooks.pick_runtime, "_validate_evidence", original_validate)
    monkeypatch.setattr(hooks.pick_runtime, "_terminal", lambda *a, **k: {"row_key": "row-1"})
    monkeypatch.setattr(
        hooks.pick_runtime,
        "_completed_scored_outcome",
        lambda *a, **k: {"row_key": "row-1", "model_evaluated": True},
    )
    monkeypatch.setattr(hooks, "record_inputs_ready", record)
    monkeypatch.setattr(hooks, "record_terminal_outcome", lambda outcome: None)
    monkeypatch.setattr(hooks, "record_model_outcome", lambda outcome: None)

    hooks.install_pick_request_state_hooks()

    row = SimpleNamespace(row_key="row-1")
    normalized = hooks.pick_runtime._validate_evidence(row, "RECEIVING_YARDS")

    assert normalized["event_id"] == "evt-1"
    assert calls["record"] == (row, normalized)
    assert getattr(hooks.pick_runtime._validate_evidence, "_v17_pick_state_hook") is True
