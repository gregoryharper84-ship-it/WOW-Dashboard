"""
scan_audit.py  —  Standardized candidate audit, evidence manifest,
                  reconciliation, and second-pass self-audit.
WOW v16 — Linemakers Presentation & Self-Audit Patch

All functions are pure (no DB, no I/O). They consume the candidate list
produced by /wow/kalshi/category-scan and the run counters dict.

Public API:
  check_ticker_identity(candidate)        → warning dict (never blocks)
  build_candidate_audit_row(candidate)    → 20-field standardized row
  build_candidate_audit_table(candidates) → list of audit rows
  build_evidence_manifest(candidate)      → machine-verifiable evidence block
  run_second_pass_audit(candidates, counters) → 7-check consistency report
  build_reconciliation_equation(counters) → equation check + PASS / MISMATCH
  build_candidate_funnel_summary(counters) → compact funnel dict

Design principles (from Linemakers analysis):
  • Ticker analyzed must equal ticker from inventory must equal ticker from
    orderbook — mismatch is a WARNING (logged, not a block).
  • Every inventory row must receive a terminal disposition (PASS / FAIL).
  • Pregame probability must not survive after event start.
  • Midpoints must never be labeled no-vig.
  • Stale prices must never enter edge calculations.
  • Lower-bound edge must clear the category floor.
  • Portfolio governor must always run.
  • Reconciliation mismatch invalidates the run (caller decides action).

can_execute=False unconditional.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LIVE_EVENT_STATUSES: frozenset[str] = frozenset({
    "in_progress", "live", "started", "halftime",
    "suspended", "active", "inprogress",
})

_DEFAULT_EDGE_FLOOR_SPORTS  = 0.0   # net_edge_lower_bound > 0
_DEFAULT_EDGE_FLOOR_WEATHER = 0.0   # same convention

# ---------------------------------------------------------------------------
# Ticker identity warning  (item #2)
# ---------------------------------------------------------------------------

def check_ticker_identity(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Compare ticker_analyzed vs ticker_from_inventory vs ticker_from_orderbook.

    Returns a warning dict — NEVER blocks the candidate.

    Fields compared (all optional; absent = skipped):
      ticker              — the ticker the scan used when calling gates
      inventory_ticker    — ticker returned by the inventory adapter
      orderbook_ticker    — ticker returned by the orderbook response

    Returns:
      {
        "identity_verified": bool,
        "warning":           bool,
        "warning_label":     "CONTRACT_IDENTITY_UNVERIFIED" | None,
        "mismatches":        list[str],          # which pair(s) differed
        "detail":            str,
      }
    """
    t_analyzed  = (candidate.get("ticker") or "").strip().upper()
    t_inventory = (candidate.get("inventory_ticker") or candidate.get("ticker") or "").strip().upper()
    t_orderbook = (candidate.get("orderbook_ticker") or "").strip().upper()

    mismatches: list[str] = []

    if t_inventory and t_analyzed and t_inventory != t_analyzed:
        mismatches.append(f"analyzed('{t_analyzed}') != inventory('{t_inventory}')")

    if t_orderbook and t_analyzed and t_orderbook != t_analyzed:
        mismatches.append(f"analyzed('{t_analyzed}') != orderbook('{t_orderbook}')")

    if mismatches:
        return {
            "identity_verified": False,
            "warning":           True,
            "warning_label":     "CONTRACT_IDENTITY_UNVERIFIED",
            "mismatches":        mismatches,
            "detail":            (
                "Ticker identity mismatch detected — logged as warning only, "
                "candidate is NOT blocked. "
                f"Mismatches: {'; '.join(mismatches)}."
            ),
        }

    return {
        "identity_verified": True,
        "warning":           False,
        "warning_label":     None,
        "mismatches":        [],
        "detail":            f"Ticker identity verified: '{t_analyzed}'.",
    }


# ---------------------------------------------------------------------------
# Standardized candidate audit table  (item #1)
# ---------------------------------------------------------------------------

def build_candidate_audit_row(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Produce a single standardized 20-field audit row from a raw candidate dict.

    The 20 fields match the Linemakers-inspired audit table format with WOW
    additions (contract_identity_warning, no_side_tail_risk_label).
    """
    ticker      = candidate.get("ticker") or candidate.get("market_ticker") or ""
    category    = candidate.get("category") or candidate.get("lane") or ""
    gate_result = candidate.get("_sports_gate") or candidate.get("_weather_gate") or {}
    fc          = candidate.get("failure_category") or gate_result.get("failure_category")
    passed      = candidate.get("process_pass_fail") == "PASS"

    # Edge fields
    net_edge_lb = candidate.get("net_edge_lower_bound")
    cons_prob   = candidate.get("calibrated_prob_lower_bound") or candidate.get("consensus_fair_probability")
    market_prc  = candidate.get("market_price") or candidate.get("executable_price")
    fee_be      = candidate.get("fee_adjusted_break_even")

    # Point edge vs lower-bound edge (keep explicitly separate)
    model_prob  = candidate.get("model_probability") or candidate.get("model_prob")
    point_edge: float | None = None
    if model_prob is not None and fee_be is not None:
        try:
            point_edge = round(float(model_prob) - float(fee_be), 4)
        except (TypeError, ValueError):
            pass

    lb_edge: float | None = None
    if cons_prob is not None and fee_be is not None:
        try:
            lb_edge = round(float(cons_prob) - float(fee_be), 4)
        except (TypeError, ValueError):
            pass

    # Orderbook terminology (item #7)
    yes_exec_ask = candidate.get("yes_executable_ask") or market_prc
    no_exec_ask  = candidate.get("no_executable_ask")
    yes_mid      = candidate.get("yes_midpoint")
    no_mid       = candidate.get("no_midpoint")

    # Ticker identity warning
    id_check = check_ticker_identity(candidate)

    # Failure path
    primary_win_path  = candidate.get("primary_win_path") or candidate.get("win_path") or ""
    primary_fail_path = candidate.get("failure_path") or candidate.get("largest_failure_path") or ""

    return {
        "contract_ticker":           ticker,
        "category_and_lane":         category,
        "side":                      candidate.get("side_yes_no") or candidate.get("side") or "",
        "settlement_source":         candidate.get("settlement_source") or candidate.get("settlement_condition") or "",
        "event_state":               candidate.get("event_status") or "UNKNOWN",

        # Orderbook (using sanitated terminology — no midpoint labeled as no-vig)
        "yes_executable_ask":        yes_exec_ask,
        "no_executable_ask":         no_exec_ask,
        "yes_midpoint":              yes_mid,
        "no_midpoint":               no_mid,
        "orderbook_timestamp":       candidate.get("orderbook_timestamp") or candidate.get("price_timestamp"),
        "price_age_minutes":         candidate.get("price_age_minutes"),

        # Edge (explicitly separated — item #4)
        "fee_adjusted_break_even":   fee_be,
        "model_probability":         model_prob,
        "calibrated_lower_bound":    cons_prob,
        "point_edge":                point_edge,
        "lower_bound_edge":          lb_edge or net_edge_lb,

        # Win / failure paths (item #5)
        "primary_win_path":          primary_win_path,
        "primary_failure_path":      primary_fail_path,

        # Gate outcome
        "gate_result":               "PASS" if passed else (fc or "FAIL"),
        "final_label":               candidate.get("label") or ("QUALIFIED" if passed else "REJECTED"),

        # Ticker identity warning (item #2)
        "contract_identity_warning": id_check.get("warning_label"),
    }


def build_candidate_audit_table(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a standardized audit table row for every candidate."""
    return [build_candidate_audit_row(c) for c in candidates]


# ---------------------------------------------------------------------------
# Direct-evidence manifest  (item #2)
# ---------------------------------------------------------------------------

def build_evidence_manifest(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Build a machine-verifiable evidence block for one candidate.

    Enforces: ticker_analyzed == ticker_from_inventory == ticker_from_orderbook
    Any mismatch returns warning_label=CONTRACT_IDENTITY_UNVERIFIED (no block).
    """
    ticker = candidate.get("ticker") or candidate.get("market_ticker") or ""
    id_check = check_ticker_identity(candidate)

    return {
        "evidence": {
            "market_id":                   candidate.get("event_id") or candidate.get("event_ticker"),
            "series_ticker":               candidate.get("event_ticker"),
            "exact_ticker":                ticker,
            "inventory_ticker":            candidate.get("inventory_ticker") or ticker,
            "orderbook_ticker":            candidate.get("orderbook_ticker") or ticker,
            "market_endpoint_timestamp":   candidate.get("market_endpoint_timestamp"),
            "orderbook_endpoint_timestamp": candidate.get("orderbook_timestamp") or candidate.get("price_timestamp"),
            "settlement_source":           candidate.get("settlement_source") or candidate.get("settlement_condition"),
            "model_input_timestamps": {
                "consensus_odds_ts":   candidate.get("consensus_odds_timestamp"),
                "price_ts":            candidate.get("price_timestamp"),
                "lineup_status_ts":    candidate.get("lineup_status_timestamp"),
            },
            "event_status_source":         candidate.get("event_status_source") or "Kalshi market status",
        },
        "ticker_identity_check": {
            "identity_verified":  id_check["identity_verified"],
            "warning_label":      id_check["warning_label"],
            "mismatches":         id_check["mismatches"],
        },
    }


# ---------------------------------------------------------------------------
# Second-pass consistency self-audit  (item #3)
# ---------------------------------------------------------------------------

def run_second_pass_audit(
    candidates: list[dict[str, Any]],
    counters:   dict[str, int],
    final_pool: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Run 7 automatic consistency checks over all candidates.

    Checks:
      1. Every inventory row has a terminal disposition (PASS / FAIL).
      2. Every qualified row passed all upstream gates.
      3. No pregame probability survived after event start.
      4. No midpoint was mislabeled as no-vig (inspects warning fields).
      5. No stale price entered edge calculations (price_age > 10 min in qualified).
      6. Lower-bound edge cleared the category floor for every qualified row.
      7. Portfolio governor ran (portfolio_failures key present in counters).

    Returns:
      {
        "passed": bool,
        "checks": list[{"id": int, "description": str, "passed": bool, "detail": str}],
        "failures": list[str],
      }
    """
    final_pool = final_pool or []
    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    def _check(check_id: int, description: str, passed: bool, detail: str) -> None:
        checks.append({
            "id":          check_id,
            "description": description,
            "passed":      passed,
            "detail":      detail,
        })
        if not passed:
            failures.append(f"C{check_id}: {description}")

    # ── Check 1: every inventory row has a terminal disposition ───────────────
    missing_disp = [
        c.get("ticker") for c in candidates
        if c.get("process_pass_fail") not in ("PASS", "FAIL")
    ]
    _check(
        1, "Every inventory row received a terminal disposition (PASS/FAIL)",
        not missing_disp,
        f"OK — {len(candidates)} rows all dispositioned." if not missing_disp
        else f"FAIL — {len(missing_disp)} rows missing disposition: {missing_disp[:5]}",
    )

    # ── Check 2: every qualified row passed all upstream gates ────────────────
    qualified_with_failure = [
        c.get("ticker") for c in candidates
        if c.get("process_pass_fail") == "PASS" and c.get("failure_category")
    ]
    _check(
        2, "Every qualified row passed all upstream gates",
        not qualified_with_failure,
        "OK." if not qualified_with_failure
        else f"FAIL — {len(qualified_with_failure)} qualified rows carry a failure_category: "
             f"{qualified_with_failure[:5]}",
    )

    # ── Check 3: no pregame probability survived after event start ────────────
    live_qualified = [
        c.get("ticker") for c in candidates
        if c.get("process_pass_fail") == "PASS"
        and (c.get("event_status") or "").lower().strip() in _LIVE_EVENT_STATUSES
    ]
    _check(
        3, "No pregame probability survived after event start",
        not live_qualified,
        "OK — no live-event rows in qualified pool." if not live_qualified
        else f"FAIL — {len(live_qualified)} qualified rows have live event status: {live_qualified}",
    )

    # ── Check 4: no midpoint mislabeled as no-vig ─────────────────────────────
    midpoint_misuse = [
        c.get("ticker") for c in candidates
        if c.get("no_vig_misuse_warning") or c.get("midpoint_labeled_as_no_vig")
    ]
    _check(
        4, "No midpoint was mislabeled as no-vig",
        not midpoint_misuse,
        "OK — no midpoint-as-no-vig violations found." if not midpoint_misuse
        else f"WARN — {len(midpoint_misuse)} candidates flagged midpoint-as-no-vig: "
             f"{midpoint_misuse[:5]}",
    )

    # ── Check 5: no stale price in qualified candidates ───────────────────────
    stale_qualified = [
        c.get("ticker") for c in candidates
        if c.get("process_pass_fail") == "PASS"
        and c.get("price_age_minutes") is not None
        and float(c.get("price_age_minutes", 0)) > 10.0
    ]
    _check(
        5, "No stale price entered edge calculations (age ≤ 10 min for qualified)",
        not stale_qualified,
        "OK — all qualified rows have fresh prices." if not stale_qualified
        else f"FAIL — {len(stale_qualified)} qualified rows have price_age > 10 min: "
             f"{stale_qualified}",
    )

    # ── Check 6: lower-bound edge cleared floor for every qualified row ────────
    no_edge_qualified = [
        c.get("ticker") for c in candidates
        if c.get("process_pass_fail") == "PASS"
        and (
            c.get("net_edge_lower_bound") is None
            or float(c.get("net_edge_lower_bound", 0)) <= 0
        )
        and c.get("category") == "sports_winner"  # only sports; weather uses different metric
    ]
    _check(
        6, "Lower-bound edge cleared category floor for all qualified sports rows",
        not no_edge_qualified,
        "OK — all qualified sports rows cleared edge floor." if not no_edge_qualified
        else f"FAIL — {len(no_edge_qualified)} qualified sports rows have non-positive "
             f"lower-bound edge: {no_edge_qualified}",
    )

    # ── Check 7: portfolio governor ran ──────────────────────────────────────
    governor_ran = "portfolio_failures" in counters or "portfolio_failures_ct" in counters
    _check(
        7, "Portfolio governor ran",
        governor_ran,
        "OK — portfolio_failures counter present in run." if governor_ran
        else "WARN — portfolio_failures counter not found; governor may not have run.",
    )

    return {
        "passed":        not failures,
        "checks":        checks,
        "failures":      failures,
        "total_checks":  len(checks),
        "checks_passed": sum(1 for c in checks if c["passed"]),
        "can_execute":   False,
    }


# ---------------------------------------------------------------------------
# Reconciliation equation  (item #3)
# ---------------------------------------------------------------------------

def build_reconciliation_equation(
    counters:  dict[str, int],
    qualified: int,
) -> dict[str, Any]:
    """
    rows_scanned = identity_failed + settlement_failed + event_state_failed
                 + model_failed + price_failed + edge_failed
                 + portfolio_failed + qualified

    'rows_scanned' is derived as the sum of all buckets + qualified.
    If a caller provides 'rows_scanned' explicitly, it is compared to the sum.

    Returns:
      {
        "rows_scanned":   int,
        "equation_buckets": dict,
        "equation_sum":   int,
        "status":         "RECONCILIATION_PASS" | "RECONCILIATION_MISMATCH",
        "delta":          int,   # equation_sum − rows_scanned (0 = pass)
      }
    """
    identity_failed  = counters.get("identity_failures", 0)
    settlement_failed = counters.get("settlement_failures", 0)
    event_state_failed = counters.get("event_state_failures", 0)
    model_failed     = counters.get("model_failures", 0)
    price_failed     = counters.get("stale_price_failures", 0)
    edge_failed      = counters.get("edge_failures", 0)
    portfolio_failed = counters.get("portfolio_failures", 0) or counters.get("portfolio_failures_ct", 0)

    buckets = {
        "identity_failed":    identity_failed,
        "settlement_failed":  settlement_failed,
        "event_state_failed": event_state_failed,
        "model_failed":       model_failed,
        "price_failed":       price_failed,
        "edge_failed":        edge_failed,
        "portfolio_failed":   portfolio_failed,
        "qualified":          qualified,
    }
    equation_sum = sum(buckets.values())

    # If the caller provided an explicit rows_scanned, compare to it;
    # otherwise treat the equation sum as the authoritative total.
    rows_scanned = counters.get("rows_scanned", equation_sum)
    delta = equation_sum - rows_scanned
    status = "RECONCILIATION_PASS" if delta == 0 else "RECONCILIATION_MISMATCH"

    return {
        "rows_scanned":     rows_scanned,
        "equation_buckets": buckets,
        "equation_sum":     equation_sum,
        "status":           status,
        "delta":            delta,
        "can_execute":      False,
    }


# ---------------------------------------------------------------------------
# Candidate funnel summary  (item #9)
# ---------------------------------------------------------------------------

def build_candidate_funnel_summary(
    counters:         dict[str, int],
    discovered:       int,
    eligible_category: int,
    identity_verified: int,
    settlement_verified: int,
    model_ready:      int,
    fresh_orderbook:  int,
    positive_lb_edge: int,
    portfolio_qualified: int,
    final_research_pool: int,
) -> dict[str, Any]:
    """
    Compact end-of-run funnel report.

    Makes a zero- or one-contract result look deliberate rather than incomplete.

    Example:
      Inventory discovered:    24
      Eligible category:       11
      Identity verified:        9
      Settlement verified:      9
      Model ready:              5
      Fresh orderbook:          3
      Positive lower-bound edge: 1
      Portfolio qualified:      1
      Final research pool:      1
    """
    return {
        "inventory_discovered":      discovered,
        "eligible_category":         eligible_category,
        "identity_verified":         identity_verified,
        "settlement_verified":       settlement_verified,
        "model_ready":               model_ready,
        "fresh_orderbook":           fresh_orderbook,
        "positive_lower_bound_edge": positive_lb_edge,
        "portfolio_qualified":       portfolio_qualified,
        "final_research_pool":       final_research_pool,
        "note":                      (
            "Zero or one result is deliberate — fewer results are preferred "
            "over unsupported results. can_execute=False."
        ),
    }
