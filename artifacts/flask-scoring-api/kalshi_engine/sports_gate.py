"""
sports_gate.py  —  9-gate sports-winner filter for GET /wow/kalshi/category-scan
WOW v16.5 Category-Router / Singles-Governor Layer

Only FULL_GAME_OUTRIGHT_WINNER markets that pass all 9 gates may enter the
final ranked pool. A favorite with negative fee/uncertainty-adjusted edge is
rejected regardless of win probability (gate 9). An upset (calibrated_prob
below 65%) is rejected regardless of edge (gate 5).

Gate order (strict — first failure short-circuits):
  1. inventory_signal == INVENTORY_READY
  2. market_type == FULL_GAME_OUTRIGHT_WINNER
  3. Event/settlement VERIFIED (ticker + event_ticker + title + settlement_condition
     all present AND settlement_risk grade >= B)
  4. Starter/lineup CONFIRMED_OR_STRONGLY_PROBABLE
  5. calibrated_prob_lower_bound >= 0.65
  6. Independent model support: consensus_odds.status == AVAILABLE
     AND single_book_fallback == False
  7. market_prior_weight <= 0.50
  8. price_age_minutes <= 10 AND kalshi_orderbook_source == direct_api
  9. net_edge_lower_bound > 0 (fee/uncertainty-adjusted edge, both model
     AND consensus must independently clear the floor)
"""
from __future__ import annotations

from typing import Any

_MIN_CALIBRATED_PROB   = 0.65
_MAX_PRICE_AGE_MINUTES = 10.0
_MAX_PRIOR_WEIGHT      = 0.50

_WINNER_MARKET_TYPES: frozenset[str] = frozenset({
    "full_game_outright_winner",
    "game_winner",
    "moneyline",
    "winner",
})


def check(candidate: dict[str, Any], inventory_signal: str) -> dict[str, Any]:
    """
    Run all 9 sports-winner gates against a single sports candidate.

    Parameters
    ----------
    candidate — dict assembled by the category-scan orchestrator:
      ticker                    str
      event_ticker              str | None
      market_title              str | None
      settlement_condition      str | None
      market_type               str
      trading_active            bool | None
      kalshi_orderbook_source   str
      price_age_minutes         float | None
      calibrated_prob_lower_bound  float | None
      lineup_status             str | None   (CONFIRMED | STRONGLY_PROBABLE | UNKNOWN)
      consensus_odds            dict | None  (output of consensus_odds.get_...)
      market_prior_weight       float | None
      net_edge_lower_bound      float | None
      settlement_grade_result   dict | None  (from settlement_risk.grade_contract)
      portfolio_check_passed    bool
      portfolio_rejection_reason str | None

    inventory_signal — live result of KalshiInventoryAdapter.check_sports_inventory()
                       Must be exactly "INVENTORY_READY" or gate 1 blocks.

    Returns
    -------
    dict with:
      passed            bool
      failure_category  str | None
      failure_gate      int | None
      gate_verdicts     list[dict]
      calibrated_prob_lower_bound  float | None
      net_edge_lower_bound  float | None
    """
    verdicts: list[dict[str, Any]] = []

    def _fail(gate: int, code: str, detail: str) -> dict[str, Any]:
        verdicts.append({"gate": gate, "passed": False, "code": code, "detail": detail})
        return {
            "passed":           False,
            "failure_category": code,
            "failure_gate":     gate,
            "gate_verdicts":    verdicts,
            "calibrated_prob_lower_bound": candidate.get("calibrated_prob_lower_bound"),
            "net_edge_lower_bound":        candidate.get("net_edge_lower_bound"),
        }

    def _pass_gate(gate: int, detail: str) -> None:
        verdicts.append({"gate": gate, "passed": True, "detail": detail})

    # ── Gate 1: inventory_signal == INVENTORY_READY ───────────────────────────
    if inventory_signal != "INVENTORY_READY":
        return _fail(1, "INVENTORY_NOT_READY",
                     f"Live inventory signal='{inventory_signal}' — sports modeling "
                     f"blocked before any market evaluation; INVENTORY_READY required.")
    _pass_gate(1, f"inventory_signal={inventory_signal}")

    # ── Gate 2: market_type == FULL_GAME_OUTRIGHT_WINNER ─────────────────────
    raw_mtype = (candidate.get("market_type") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw_mtype not in _WINNER_MARKET_TYPES:
        return _fail(2, "NOT_FULL_GAME_OUTRIGHT_WINNER",
                     f"market_type='{raw_mtype}' is not a full-game outright winner market. "
                     f"Derivatives, F5, run-totals, and props are blocked.")
    _pass_gate(2, f"market_type='{raw_mtype}' is FULL_GAME_OUTRIGHT_WINNER-compatible")

    # ── Gate 3: Event/settlement VERIFIED ────────────────────────────────────
    ticker              = candidate.get("ticker") or ""
    event_ticker        = candidate.get("event_ticker") or ""
    market_title        = candidate.get("market_title") or ""
    settlement_cond     = candidate.get("settlement_condition") or ""
    settlement_complete = all([ticker, event_ticker, market_title, settlement_cond])

    if not settlement_complete:
        missing = [n for n, v in (
            ("ticker", ticker), ("event_ticker", event_ticker),
            ("market_title", market_title), ("settlement_condition", settlement_cond),
        ) if not v]
        return _fail(3, "SETTLEMENT_INCOMPLETE",
                     f"Settlement identity incomplete — missing: {missing}.")

    grade_result = candidate.get("settlement_grade_result") or {}
    s_risk = grade_result.get("settlement_risk", "MEDIUM")
    s_grade = grade_result.get("resolution_clarity_grade", "C")
    _GRADE_RANK = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}
    if s_risk in ("HIGH", "REJECT") or _GRADE_RANK.get(s_grade, 0) < 4:
        return _fail(3, "SETTLEMENT_AMBIGUOUS",
                     f"Settlement grade={s_grade}, risk={s_risk} — "
                     f"ambiguous wording blocks final pool entry; grade B+ required.")
    _pass_gate(3, f"Settlement VERIFIED: ticker={ticker}, grade={s_grade}, risk={s_risk}")

    # ── Gate 4: Starter/lineup CONFIRMED_OR_STRONGLY_PROBABLE ────────────────
    lineup = (candidate.get("lineup_status") or "UNKNOWN").upper()
    if lineup not in ("CONFIRMED", "STRONGLY_PROBABLE", "CONFIRMED_OR_STRONGLY_PROBABLE"):
        return _fail(4, "LINEUP_NOT_CONFIRMED",
                     f"lineup_status='{lineup}' — starter/lineup not confirmed or strongly "
                     f"probable; cannot model a game-winner with uncertain lineup.")
    _pass_gate(4, f"lineup_status='{lineup}'")

    # ── Gate 5: calibrated_prob_lower_bound >= 0.65 ───────────────────────────
    cal_prob = candidate.get("calibrated_prob_lower_bound")
    if cal_prob is None or cal_prob < _MIN_CALIBRATED_PROB:
        return _fail(5, "UPSET_REJECTED",
                     f"calibrated_prob_lower_bound={cal_prob} < {_MIN_CALIBRATED_PROB} — "
                     f"upsets never occupy a final pool slot regardless of edge signal.")
    _pass_gate(5, f"calibrated_prob_lower_bound={cal_prob:.3f} >= {_MIN_CALIBRATED_PROB}")

    # ── Gate 6: Independent model support ────────────────────────────────────
    consensus = candidate.get("consensus_odds") or {}
    cons_status      = consensus.get("status", "NOT_CALLED")
    single_book_fb   = bool(consensus.get("single_book_fallback", True))
    if cons_status != "AVAILABLE":
        return _fail(6, f"ODDS_CONSENSUS_{cons_status}",
                     f"consensus_odds.status='{cons_status}' — independent sportsbook "
                     f"no-vig consensus required; NOT_CALLED/FAILED/STALE block gate 6.")
    if single_book_fb:
        return _fail(6, "ODDS_CONSENSUS_SINGLE_BOOK",
                     "single_book_fallback=True — a single raw ML price is never treated "
                     "as independent fair probability; consensus needs 2+ books.")
    _pass_gate(6, f"consensus_odds.status=AVAILABLE, single_book_fallback=False")

    # ── Gate 7: market_prior_weight <= 0.50 ───────────────────────────────────
    prior_weight = candidate.get("market_prior_weight")
    if prior_weight is not None and prior_weight > _MAX_PRIOR_WEIGHT:
        return _fail(7, "MARKET_PRIOR_WEIGHT_TOO_HIGH",
                     f"market_prior_weight={prior_weight:.3f} > {_MAX_PRIOR_WEIGHT} — "
                     f"market price dominates the hybrid estimate; model is not independent.")
    _pass_gate(7, f"market_prior_weight={prior_weight} <= {_MAX_PRIOR_WEIGHT}")

    # ── Gate 8: price_age_minutes <= 10 AND source == direct_api ─────────────
    price_age   = candidate.get("price_age_minutes")
    ob_source   = (candidate.get("kalshi_orderbook_source") or "no_ticker")
    trading_act = candidate.get("trading_active")

    if trading_act is False:
        return _fail(8, "MARKET_NOT_TRADING",
                     "trading_active=False — market is closed; no executable price.")
    if ob_source != "direct_api":
        return _fail(8, "KALSHI_ORDERBOOK_SOURCE_NOT_DIRECT_API",
                     f"kalshi_orderbook_source='{ob_source}' — only server-side direct_api "
                     f"fetches satisfy the live-price gate; caller-supplied / screenshot "
                     f"prices are display-only and can never reach the final pool.")
    if price_age is None or price_age > _MAX_PRICE_AGE_MINUTES:
        return _fail(8, "STALE_PRICE",
                     f"price_age_minutes={price_age} > {_MAX_PRICE_AGE_MINUTES}min — "
                     f"orderbook freshness gate failed.")
    _pass_gate(8, f"source=direct_api, price_age={price_age:.1f}min, trading_active={trading_act}")

    # ── Gate 9: net_edge_lower_bound > 0 ─────────────────────────────────────
    net_edge = candidate.get("net_edge_lower_bound")
    if net_edge is None or net_edge <= 0:
        return _fail(9, "EDGE_BELOW_FLOOR",
                     f"net_edge_lower_bound={net_edge} — fee/uncertainty-adjusted edge is not "
                     f"positive; favorite with negative edge is rejected regardless of win "
                     f"probability (gate 9 is unconditional).")
    _pass_gate(9, f"net_edge_lower_bound={net_edge:.4f} > 0")

    # ── All gates passed ──────────────────────────────────────────────────────
    return {
        "passed":                      True,
        "failure_category":            None,
        "failure_gate":                None,
        "gate_verdicts":               verdicts,
        "calibrated_prob_lower_bound": cal_prob,
        "net_edge_lower_bound":        net_edge,
    }
