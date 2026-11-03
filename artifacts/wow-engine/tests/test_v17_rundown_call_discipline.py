"""Rundown data-point cost controls: never request every market at every book."""
from types import SimpleNamespace

import pytest

from v17 import market_evidence_scout_bridge as scout_bridge
from v17 import market_evidence_snapshot as snapshot
from v17 import market_evidence_sources as sources
from v17 import rundown_ml_board as board


def test_evidence_affiliates_default_to_catalog_verified_three_books(monkeypatch):
    monkeypatch.delenv("WOW_RUNDOWN_EVIDENCE_AFFILIATE_IDS", raising=False)
    assert sources.rundown_evidence_affiliate_ids() == ("3", "19", "23")


def test_evidence_affiliates_override_and_explicit_all(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_EVIDENCE_AFFILIATE_IDS", "3, 22")
    assert sources.rundown_evidence_affiliate_ids() == ("3", "22")
    monkeypatch.setenv("WOW_RUNDOWN_EVIDENCE_AFFILIATE_IDS", "all")
    assert sources.rundown_evidence_affiliate_ids() is None
    monkeypatch.setenv("WOW_RUNDOWN_EVIDENCE_AFFILIATE_IDS", " , ")
    assert sources.rundown_evidence_affiliate_ids() == ("3", "19", "23")


def _capture(calls):
    def fake(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(ok=True, data=[], code=None)
    return fake


def test_ml_board_requests_only_moneyline_main_lines_at_three_books(monkeypatch):
    monkeypatch.delenv("WOW_RUNDOWN_EVIDENCE_AFFILIATE_IDS", raising=False)
    monkeypatch.setenv("WOW_RUNDOWN_WINNER_MARKET_IDS", "1")
    calls = []
    monkeypatch.setattr(board.live, "get_sport_date_odds_snapshot", _capture(calls))
    monkeypatch.setattr(board, "install_rundown_v2_auth_repair", lambda: None, raising=False)
    board._collect_rundown("baseball_mlb")
    assert calls, "ML board made no Rundown request"
    for kwargs in calls:
        assert kwargs["market_ids"] == ("1",)
        assert kwargs["affiliate_ids"] == ("3", "19", "23")
        assert kwargs["main_line"] is True
        assert kwargs["hide_closed"] is True


def test_ml_board_never_guesses_market_ids_when_unconfigured(monkeypatch):
    monkeypatch.delenv("WOW_RUNDOWN_WINNER_MARKET_IDS", raising=False)
    monkeypatch.delenv("WOW_RUNDOWN_MARKET_ID_MAP_JSON", raising=False)
    calls = []
    monkeypatch.setattr(board.live, "get_sport_date_odds_snapshot", _capture(calls))
    monkeypatch.setattr(board, "install_rundown_v2_auth_repair", lambda: None, raising=False)
    board._collect_rundown("baseball_mlb")
    assert all(kwargs["market_ids"] is None for kwargs in calls)
    assert all(kwargs["affiliate_ids"] == ("3", "19", "23") for kwargs in calls)


@pytest.mark.parametrize("module", [scout_bridge, snapshot])
def test_evidence_lanes_are_book_and_main_line_narrowed(module):
    source = open(module.__file__).read()
    # every rundown_market_evidence call in these lanes carries both filters
    blocks = source.split("live.rundown_market_evidence(")[1:]
    assert blocks
    for block in blocks:
        assert "affiliate_ids=sources.rundown_evidence_affiliate_ids()" in block[:400]
        assert "main_line=True" in block[:400]
