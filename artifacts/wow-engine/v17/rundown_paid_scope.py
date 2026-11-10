"""Fail-closed scope admission for paid TheRundown winner odds acquisition.

Provider IDs must be operator-verified and explicitly pinned.  A missing
market or affiliate dimension is not permission to request a broader slate.
This module never derives sporting probability or changes terminal authority.
"""
from __future__ import annotations

import os

from v17 import market_evidence_sources as sources

SCOPE_BLOCKER = "RUNDOWN_PAID_SCOPE_UNRESOLVED"
AFFILIATE_IDS_ENV = "WOW_RUNDOWN_WINNER_AFFILIATE_IDS"


def _numeric_ids(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Reject malformed, empty, or non-provider identifiers."""
    ids = tuple(str(value).strip() for value in values)
    if not ids or any(not value.isdecimal() or int(value) <= 0 for value in ids):
        return ()
    return tuple(dict.fromkeys(ids))


def winner_paid_scope() -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Return (market_ids, affiliate_ids) only when BOTH are resolved.

    Market IDs reuse the existing operator-pinned V2 mapping; affiliate IDs
    require an explicit operator-pinned allowlist.  No guessed defaults.
    """
    market_ids = _numeric_ids(sources.rundown_winner_market_ids())
    affiliate_ids = _numeric_ids(os.environ.get(AFFILIATE_IDS_ENV, "").split(","))
    if not market_ids or not affiliate_ids:
        return None
    return market_ids, affiliate_ids
