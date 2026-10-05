import v17


def test_mlb_bridge_acceptance_defers_above_default_memory_threshold(monkeypatch):
    monkeypatch.delenv("WOW_V17_MLB_BRIDGE_SELF_ACCEPTANCE_MAX_MEMORY_RATIO", raising=False)
    monkeypatch.setattr(v17, "_read_cgroup_memory_bytes", lambda: (446_201_860, 536_870_900))

    under_pressure, ratio = v17._mlb_bridge_acceptance_under_memory_pressure()

    assert under_pressure is True
    assert ratio is not None
    assert ratio > 0.80


def test_mlb_bridge_acceptance_runs_below_default_memory_threshold(monkeypatch):
    monkeypatch.delenv("WOW_V17_MLB_BRIDGE_SELF_ACCEPTANCE_MAX_MEMORY_RATIO", raising=False)
    monkeypatch.setattr(v17, "_read_cgroup_memory_bytes", lambda: (393_654_270, 536_870_900))

    under_pressure, ratio = v17._mlb_bridge_acceptance_under_memory_pressure()

    assert under_pressure is False
    assert ratio is not None
    assert ratio < 0.80


def test_missing_cgroup_metrics_do_not_disable_acceptance(monkeypatch):
    monkeypatch.setattr(v17, "_read_cgroup_memory_bytes", lambda: None)

    under_pressure, ratio = v17._mlb_bridge_acceptance_under_memory_pressure()

    assert under_pressure is False
    assert ratio is None
