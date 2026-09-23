from __future__ import annotations

from pathlib import Path


ADDENDUM = Path(__file__).with_name("WOW_V17_CUSTOM_GPT_PRIZEPICKS_SKILL_ADDENDUM.txt")


def _contract() -> str:
    return ADDENDUM.read_text(encoding="utf-8")


def test_multipage_pdf_requires_every_page_and_page_level_fallback() -> None:
    text = _contract()
    assert "enumerate and inspect every source page" in text
    assert "page-level render/screenshot path" in text
    assert "Do not stop after page 1" in text


def test_unreadable_pages_remain_typed_and_block_false_full_reconciliation() -> None:
    text = _contract()
    assert "SOURCE_PAGE_UNREADABLE:<page_number>" in text
    assert "source_pages_total" in text
    assert "source_pages_readable" in text
    assert "source_pages_unreadable" in text
    assert "Never claim `omitted rows = 0`" in text


def test_source_ingestion_failure_is_not_model_unavailability() -> None:
    text = _contract()
    assert "source-ingestion blocker, not model unavailability" in text


def test_prop_table_columns_are_explicit() -> None:
    text = _contract()
    for column in (
        "`Player`",
        "`Matchup`",
        "`PrizePicks line`",
        "`Offer`",
        "`Available side(s)`",
        "`Current/live note`",
    ):
        assert column in text
    assert "do not concatenate column names into a single malformed header" in text
