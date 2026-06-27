"""
market_buckets.py  —  Kalshi market bucket classification
WOW v16 Kalshi Exchange Layer

Buckets separate markets by confidence in resolution + tracking maturity.

TRUSTED_TEST  — verified settlement, good liquidity, tracked history.
               Ready for model development and paper-trade tracking.
WATCH         — solid settlement wording but insufficient history or spread.
               Track prices and settlement without model sizing.
TEST_ONLY     — acceptable for paper-trade / learning only.
               Not for real capital even when model is ready.
SCOUT         — new / unknown. Gather data. No model probability yet.
REJECT        — settlement ambiguity, too thin, bad rules, or unbridgeable
               model gap. Do not track or model.

Classification rules (applied in priority order):
  1. If settlement_grade = F or settlement_risk = REJECT → REJECT
  2. If liquidity_grade = F → REJECT
  3. If settlement_grade ≥ B and liquidity_grade ≥ B and has_history → TRUSTED_TEST
  4. If settlement_grade ≥ B and liquidity_grade ≥ C → WATCH
  5. If settlement_grade ≥ C and liquidity_grade ≥ D → TEST_ONLY
  6. No settlement grade or no history → SCOUT
  Otherwise → REJECT
"""
from __future__ import annotations

from typing import Any

# Grade ranks (higher = better)
_GRADE_RANK = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}


def classify(
    settlement_grade:  str | None,
    liquidity_grade:   str | None,
    has_history:       bool    = False,
    category:          str     = "other",
    settlement_risk:   str     = "MEDIUM",
    adjusted_edge:     float | None = None,
    notes:             str     = "",
) -> dict[str, Any]:
    """
    Classify a Kalshi market into a bucket.

    Parameters
    ----------
    settlement_grade  — A/B/C/D/F from settlement_risk.grade_contract()
    liquidity_grade   — A/B/C/D/F from orderbook_normalizer.normalize()
    has_history       — True if this market/category has settled ≥3 prior contracts
    category          — market category
    settlement_risk   — LOW/MEDIUM/HIGH/REJECT from settlement_risk.grade_contract()
    adjusted_edge     — optional; used to upgrade/downgrade at margin
    notes             — freeform

    Returns
    -------
    dict: market_bucket, rationale, can_model, can_paper_trade
    """
    sg = _GRADE_RANK.get(settlement_grade or "F", 0)
    lg = _GRADE_RANK.get(liquidity_grade  or "F", 0)
    rationale: list[str] = []

    # ── Rule 1: Hard reject on settlement ────────────────────────────────────
    if settlement_risk == "REJECT" or sg == 0:
        rationale.append(f"Settlement grade={settlement_grade} or risk=REJECT → REJECT")
        return _result("REJECT", rationale, notes)

    # ── Rule 2: Hard reject on thin book ─────────────────────────────────────
    if lg == 0:
        rationale.append(f"Liquidity grade=F → REJECT (book too thin to price)")
        return _result("REJECT", rationale, notes)

    # ── Rule 3: TRUSTED_TEST ──────────────────────────────────────────────────
    if sg >= 4 and lg >= 4 and has_history:
        rationale.append(
            f"Settlement≥B, Liquidity≥B, has_history=True → TRUSTED_TEST"
        )
        if adjusted_edge is not None and adjusted_edge < 0.02:
            rationale.append(f"adjusted_edge={adjusted_edge:.3f} below 2% → downgrade to WATCH")
            return _result("WATCH", rationale, notes)
        return _result("TRUSTED_TEST", rationale, notes)

    # ── Rule 4: WATCH ─────────────────────────────────────────────────────────
    if sg >= 4 and lg >= 3:
        rationale.append(f"Settlement≥B, Liquidity≥C → WATCH")
        return _result("WATCH", rationale, notes)

    # ── Rule 5: TEST_ONLY ─────────────────────────────────────────────────────
    if sg >= 3 and lg >= 2:
        rationale.append(f"Settlement≥C, Liquidity≥D → TEST_ONLY (paper trade only)")
        return _result("TEST_ONLY", rationale, notes)

    # ── Rule 6: SCOUT ─────────────────────────────────────────────────────────
    if sg >= 2:
        rationale.append(f"Settlement grade D, or no history → SCOUT")
        return _result("SCOUT", rationale, notes)

    rationale.append(f"Insufficient grades (settlement={settlement_grade}, liquidity={liquidity_grade}) → REJECT")
    return _result("REJECT", rationale, notes)


def _result(bucket: str, rationale: list[str], notes: str) -> dict[str, Any]:
    can_model       = bucket in ("TRUSTED_TEST", "WATCH")
    can_paper_trade = bucket in ("TRUSTED_TEST", "WATCH", "TEST_ONLY")
    return {
        "market_bucket":    bucket,
        "can_model":        can_model,
        "can_paper_trade":  can_paper_trade,
        "rationale":        rationale,
        "notes":            notes,
        "can_approve_bets": False,
    }
