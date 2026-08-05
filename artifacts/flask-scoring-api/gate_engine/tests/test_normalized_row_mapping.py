"""
Regression tests for task #99: /normalize-legs 500 crash — NormalizedRow
treated as a dict by callers using .get() / **row / 'x' in row, none of
which worked when NormalizedRow only implemented __getitem__.

Reproduced pre-fix: row.get(...) raised AttributeError,
{**row, ...} raised TypeError: not a mapping, and 'x' in row raised KeyError
via the legacy __getitem__-only iteration protocol instead of returning bool.
"""
from gate_engine.normalizer import NormalizedRow


def _sample_row(**overrides) -> NormalizedRow:
    row = NormalizedRow(
        raw_player="LeBron James",
        raw_prop_type="points",
        raw_line=25.5,
        sport="NBA",
    )
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


def test_getitem_unchanged():
    row = _sample_row(resolution_status="resolved")
    assert row["resolution_status"] == "resolved"


def test_get_with_existing_key():
    row = _sample_row(resolution_status="resolved")
    assert row.get("resolution_status") == "resolved"


def test_get_with_missing_key_returns_default():
    row = _sample_row()
    assert row.get("does_not_exist", "fallback") == "fallback"
    assert row.get("does_not_exist") is None


def test_contains_operator():
    row = _sample_row()
    assert "resolution_status" in row
    assert "definitely_not_a_field" not in row


def test_dict_conversion():
    row = _sample_row(resolution_status="resolved")
    d = dict(row)
    assert d["resolution_status"] == "resolved"
    assert d == row.to_dict()


def test_double_star_unpacking():
    row = _sample_row(resolution_status="resolved")
    merged = {**row, "extra_field": "x"}
    assert merged["resolution_status"] == "resolved"
    assert merged["extra_field"] == "x"


def test_iteration_yields_keys():
    row = _sample_row()
    keys = list(row)
    assert "resolution_status" in keys
    assert "raw_player" in keys


def test_len_matches_to_dict():
    row = _sample_row()
    assert len(row) == len(row.to_dict())


def test_keys_values_items():
    row = _sample_row(resolution_status="resolved")
    assert "resolution_status" in row.keys()
    assert "resolved" in row.values()
    assert ("resolution_status", "resolved") in row.items()


def test_attribute_access_still_works():
    """The Mapping base must not break normal dataclass attribute access."""
    row = _sample_row(resolution_status="resolved")
    row.flags.append("TEST_FLAG")
    assert row.resolution_status == "resolved"
    assert "TEST_FLAG" in row.flags
