from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_governance_parity_is_row_scoped_not_global_publish_true_false():
    source = (ROOT / "v17" / "team_event_governance_parity_route.py").read_text()
    assert 'base["probability_publishable"] = None' in source
    assert '"probability_publishable_scope"] = "ROW_SCOPED_ONLY"' in source
    assert '"row_publication_requires_terminal_reducer": True' in source
    assert 'base["global_terminal_authority"] = "V17_TERMINAL_REDUCER"' in source
    assert 'base["can_execute"] = False' in source
