from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OBSERVABILITY = (ROOT / "v17_observability.py").read_text()


def test_quota_aware_discovery_installs_after_cross_sport_resilience():
    import_marker = "from v17.quota_aware_degraded_discovery import ("
    resilience_call = "install_cross_sport_resilience()"
    quota_call = "install_quota_aware_degraded_discovery()"

    assert import_marker in OBSERVABILITY
    assert resilience_call in OBSERVABILITY
    assert quota_call in OBSERVABILITY
    assert OBSERVABILITY.index(resilience_call) < OBSERVABILITY.index(quota_call)
