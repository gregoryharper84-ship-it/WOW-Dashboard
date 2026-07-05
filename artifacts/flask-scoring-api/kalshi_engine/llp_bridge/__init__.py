"""
llp_bridge  —  WOW-PATCH-2026-07-05-LLP-KALSHI-SPORTS-BRIDGE v2
WOW v16 Kalshi Exchange Layer — LLP sports/winner-market adapter

Read-only bridge between LLP games and Kalshi sports/winner markets.

Scope (Steps 1-5, approved with amendments — see WOW-SHARED-NOTES.md):
  1. KalshiInventoryAdapter  — public market data only, no auth, no order endpoints.
  2. KalshiMarketMapper      — LLP game -> Kalshi ticker, exact match only.
  3. KalshiPriceNormalizer   — YES/NO orderbook -> probability, executable-side price.
  4. /wow/kalshi/health      — sports signal extension (see app.py).
  5. POST /wow/llp/kalshi/ml-evaluate — stub endpoint (see app.py + ml_evaluate.py).

HARD RULES (non-negotiable, baked into every module here):
  - dry_run_only=True and can_execute=False in every response, no exceptions.
  - No order placement, authenticated trading, or execution hooks of any kind.
  - Fuzzy ticker match caps at LLP_SCOUT — never approval.
  - Missing fee/friction data caps at LLP_WATCH — never LLP_PLAYABLE/LLP_APPROVED.
  - A row cannot exceed LLP_SCOUT unless ticker, event, market title, and
    settlement rule are all captured and unambiguous.
  - Do not mark this bridge "CONNECTED" anywhere — that status requires
    INVENTORY_READY + a real ticker + Grade A/B orderbook + passing regression
    tests, none of which can happen while Kalshi sports inventory is empty.
"""
