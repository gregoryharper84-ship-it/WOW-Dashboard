"""
price_normalizer.py  —  KalshiPriceNormalizer
WOW-PATCH-2026-07-05-LLP-KALSHI-SPORTS-BRIDGE v2, Step 3

YES/NO orderbook -> executable-side probability.

HARD RULE (per Greg's approved amendment #1):
  Edge math must use the EXECUTABLE-SIDE price:
    - YES candidate uses YES ask when available/derivable
      (derived as 1 - best_no_bid, per Kalshi's binary-contract identity).
    - Midpoint may be computed for DISPLAY ONLY. It must never feed edge math.

Orderbook staleness grading (per Greg's approved amendment #4, exact
thresholds from the task spec):
  age <60s        -> Grade A
  60s <= age <300s -> Grade B
  300s <= age <600s -> Grade C
  age >=600s       -> KALSHI_DATA_UNOBTAINABLE (too stale to price at all)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .. import orderbook_normalizer as _ob_norm


def _staleness_grade(age_seconds: float) -> str:
    if age_seconds < 60:
        return "A"
    if age_seconds < 300:
        return "B"
    if age_seconds < 600:
        return "C"
    return "KALSHI_DATA_UNOBTAINABLE"


class KalshiPriceNormalizer:
    """Normalizes a raw Kalshi orderbook into an executable-side probability."""

    def normalize_for_side(
        self,
        raw_orderbook:      dict[str, Any],
        ticker:             str,
        side:               str,               # "YES" or "NO"
        orderbook_timestamp_utc: Optional[str] = None,
        now_utc:            Optional[datetime]  = None,
    ) -> dict[str, Any]:
        """
        Returns:
          {
            ticker:                 str
            side:                   "YES" | "NO"
            executable_price:       float | None   # the ONLY price edge math may use
            midpoint_price:         float | None   # diagnostic/display only
            liquidity_grade:        str             # A-F from orderbook_normalizer
            staleness_seconds:      float | None
            staleness_grade:        "A" | "B" | "C" | "KALSHI_DATA_UNOBTAINABLE"
            usable:                 bool            # False if staleness or price missing
            blocking_reasons:       list[str]
            dry_run_only:           True
            can_execute:            False
          }
        """
        book = _ob_norm.normalize(raw_orderbook, ticker=ticker)
        blocking: list[str] = []

        side_upper = (side or "YES").upper()
        if side_upper == "YES":
            executable_price = book.get("best_yes_ask")
        else:
            executable_price = book.get("best_no_ask")

        midpoint_price = book.get("mid_price")  # display-only, never used below

        if executable_price is None:
            blocking.append(
                f"NO_EXECUTABLE_PRICE: best_{side_upper.lower()}_ask unavailable "
                f"from orderbook — midpoint cannot substitute for edge math."
            )

        # ── Staleness grading ────────────────────────────────────────────────
        staleness_seconds: Optional[float] = None
        staleness_grade = "KALSHI_DATA_UNOBTAINABLE"
        if orderbook_timestamp_utc:
            try:
                ob_ts = datetime.fromisoformat(orderbook_timestamp_utc.replace("Z", "+00:00"))
                if ob_ts.tzinfo is None:
                    ob_ts = ob_ts.replace(tzinfo=timezone.utc)
                now = now_utc or datetime.now(tz=timezone.utc)
                staleness_seconds = (now - ob_ts).total_seconds()
                staleness_grade = _staleness_grade(staleness_seconds)
            except (ValueError, TypeError) as exc:
                blocking.append(f"BAD_ORDERBOOK_TIMESTAMP: {exc}")
        else:
            blocking.append("NO_ORDERBOOK_TIMESTAMP: cannot grade staleness — treated as unobtainable")

        if staleness_grade == "KALSHI_DATA_UNOBTAINABLE":
            blocking.append(
                f"ORDERBOOK_STALE: age={staleness_seconds}s >= 600s "
                f"(or timestamp missing/invalid) — KALSHI_DATA_UNOBTAINABLE"
            )

        usable = executable_price is not None and staleness_grade != "KALSHI_DATA_UNOBTAINABLE"

        return {
            "ticker":            ticker,
            "side":              side_upper,
            "executable_price":  executable_price,
            "midpoint_price":    midpoint_price,
            "liquidity_grade":   book.get("liquidity_grade"),
            "staleness_seconds": staleness_seconds,
            "staleness_grade":   staleness_grade,
            "usable":            usable,
            "blocking_reasons":  blocking,
            "dry_run_only":      True,
            "can_execute":       False,
        }
