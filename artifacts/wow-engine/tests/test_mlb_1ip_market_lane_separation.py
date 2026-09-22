from types import SimpleNamespace

import mlb_1ip_ingress_runtime as ingress


def _row(**overrides):
    base = {
        "source_type": "PDF",
        "platform": "PRIZEPICKS",
        "player": "Zack Wheeler",
        "event_id": "401817032",
        "direction": "MORE",
        "line": 15.5,
        "market_side_a": None,
        "market_side_b": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_prizepicks_pdf_row_proves_exact_market_identity_without_payout_resolution():
    assert ingress._exact_market_evidence_present(_row()) is True


def test_normalized_row_without_board_provenance_does_not_invent_market_evidence():
    assert (
        ingress._exact_market_evidence_present(
            _row(source_type="NORMALIZED", platform=None)
        )
        is False
    )


def test_explicit_market_sides_remain_sufficient_evidence():
    assert (
        ingress._exact_market_evidence_present(
            _row(
                source_type="NORMALIZED",
                platform=None,
                market_side_a="MORE",
                market_side_b="LESS",
            )
        )
        is True
    )
