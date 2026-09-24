from pathlib import Path


def test_moneyline_market_value_doc_keeps_price_separate_from_probability():
    text = (Path(__file__).parents[1] / "docs" / "v17_moneyline_market_value_semantics.md").read_text()
    assert "`BEST` is the most favorable currently observed price" in text
    assert "not a sporting probability" in text
    assert "Cross-book prices must not be paired" in text
    assert "probability ranking is not mutated by market evidence" in text
    assert "`can_execute=false`" in text
