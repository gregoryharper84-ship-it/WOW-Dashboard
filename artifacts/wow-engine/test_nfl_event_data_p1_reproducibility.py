from pathlib import Path


def test_p1_readiness_requires_immutable_raw_object_reference():
    sql = (
        Path(__file__).parent
        / "migrations"
        / "20260901_nfl_event_data_p1_reproducibility.sql"
    ).read_text()
    normalized = " ".join(sql.split()).lower()
    assert "raw_object_uri is not null" in normalized
    assert "captured_but_unpreserved" in normalized
    assert "coalesce(season, -1)" in normalized
    assert "historical_data_ready" in normalized
    assert "'model_status', 'model_unavailable'" in normalized
    assert "'probability_publishable', false" in normalized
    assert "'can_execute', false" in normalized
