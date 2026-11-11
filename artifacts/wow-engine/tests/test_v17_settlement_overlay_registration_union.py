"""Regression: one sport's settlement overlay must not remove another's routes.

In the affected rapid suite, import/installation order erased the four NFL
routes when the WNBA or MLB overlay rebuilt SUPPORTED_SETTLEMENT_ROUTES.
No change to the actual official-stat settlement implementations is needed.
"""
from v17 import prop_exact_route_settlement as settlement
from v17 import nfl_prop_settlement_overlay as nfl
from v17 import wnba_composite_settlement_overlay as wnba
from v17 import mlb_1ip_exact_settlement_overlay as mlb


def test_wnba_install_preserves_previously_registered_nfl_routes(monkeypatch):
    monkeypatch.setattr(
        settlement, "SUPPORTED_SETTLEMENT_ROUTES", frozenset(nfl.NFL_SUPPORTED)
    )
    wnba.install()
    assert nfl.NFL_SUPPORTED.issubset(settlement.SUPPORTED_SETTLEMENT_ROUTES)
    assert wnba.COMPOSITE_ROUTES.issubset(settlement.SUPPORTED_SETTLEMENT_ROUTES)


def test_mlb_1ip_install_preserves_previously_registered_nfl_routes(monkeypatch):
    monkeypatch.setattr(
        settlement, "SUPPORTED_SETTLEMENT_ROUTES", frozenset(nfl.NFL_SUPPORTED)
    )
    mlb.install()
    assert nfl.NFL_SUPPORTED.issubset(settlement.SUPPORTED_SETTLEMENT_ROUTES)
    assert mlb.ROUTE in settlement.SUPPORTED_SETTLEMENT_ROUTES
    assert mlb.ROUTE not in settlement.SEPARATE_SETTLEMENT_ROUTES


def test_chained_overlay_reinstalls_never_remove_other_sports(monkeypatch):
    monkeypatch.setattr(
        settlement, "SUPPORTED_SETTLEMENT_ROUTES",
        frozenset(nfl.NFL_SUPPORTED),
    )
    mlb.install()
    wnba.install()
    first = settlement.SUPPORTED_SETTLEMENT_ROUTES
    wnba.install()
    mlb.install()
    assert settlement.SUPPORTED_SETTLEMENT_ROUTES == first
    assert nfl.NFL_SUPPORTED.issubset(first)
    assert wnba.COMPOSITE_ROUTES.issubset(first)
    assert mlb.ROUTE in first
