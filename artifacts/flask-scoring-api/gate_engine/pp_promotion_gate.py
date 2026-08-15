"""
pp_promotion_gate.py — PrizePicks Paid-Card Promotion Gate
WOW-PATCH-2026-08-15-PP-PROMOTION-AND-SAME-GAME-FRAGILITY

HIGH_PROBABILITY ≠ QUALIFIED_PAID_CARD.

A row that reaches MONEY_QUALIFIED or FINAL_APPROVED on the probability
leaderboard may still be blocked from appearing in a PrizePicks Power or
Flex card by this gate.  The gate is binding — a failing row cannot appear
in a constructed paid card regardless of its probability rank.

The probability leaderboard is price-independent.  This gate is
price-aware.  Gate results are written to row["gates"]["pp_promotion"].
The terminal_label is capped at MARKET_VERIFIED_HOLD for rows that fail
while carrying a paid-card-eligible label.  Research output (probability
rank, analysis text, model outputs) is fully preserved in all cases.

Platform break-even (implied by PrizePicks payout structure):
    POWER : 0.556   (2-leg Power payout ≈ +265 combined)
    FLEX  : 0.500
    NONE  : 0.500   (unclassified / default)

Default safety buffer: 0.020 (2 percentage-points above break-even).
Configurable per-run via the safety_buffer parameter.

Gate checks — ALL must pass for PAID_CARD_QUALIFIED:
    1. Calibrated lower bound  >=  break_even + safety_buffer
    2. Two-way no-vig probability  >=  break_even + safety_buffer
    3. Recency-shock stable: LOO removal of the single most extreme recent
       result must not change the hit-rate verdict by >= RECENCY_SHOCK_THRESHOLD.

Module invariants:
    can_execute              = False   (unconditional)
    PRODUCTION_AUTHORITY     = False
    USER_OUTPUT_AUTHORITY    = False
    TERMINAL_LABEL_AUTHORITY = False   (caps label; never approves)
    EXECUTION_RULE           = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Module-level authority constants — unconditional
# ---------------------------------------------------------------------------
can_execute              = False
PRODUCTION_AUTHORITY     = False
USER_OUTPUT_AUTHORITY    = False
TERMINAL_LABEL_AUTHORITY = False
EXECUTION_RULE           = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# ---------------------------------------------------------------------------
# Platform break-even table
# ---------------------------------------------------------------------------
BREAK_EVEN: dict[str, float] = {
    "POWER": 0.556,
    "FLEX":  0.500,
    "NONE":  0.500,
}

# Default configurable safety buffer (matches audit_closure.SAFETY_BUFFER)
DEFAULT_SAFETY_BUFFER: float = 0.020

# LOO recency-shock: if removing the single most extreme game changes the
# model-implied hit rate by >= this value (absolute), block qualification.
RECENCY_SHOCK_THRESHOLD: float = 0.030

# Labels that indicate a row is eligible for paid-card promotion evaluation.
# Only rows with these terminal labels are checked; lower labels are skipped.
PAID_CARD_ELIGIBLE_LABELS: frozenset[str] = frozenset({
    "MONEY_QUALIFIED",
    "FINAL_APPROVED",
})

# Label applied when a row fails the promotion gate while paid-card-eligible.
# Imported lazily to avoid circular imports at module load.
_REJECT_LABEL = "REJECT_PP_PROMOTION_GATE"
_CAP_LABEL    = "MARKET_VERIFIED_HOLD"   # ceiling when promotion gate fails


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_slip_type(row: dict[str, Any]) -> str:
    """Return the normalised slip type: POWER, FLEX, or NONE."""
    raw = (row.get("slip_type") or row.get("card_type") or "NONE").upper()
    if raw in BREAK_EVEN:
        return raw
    return "NONE"


def _safe_float(v: Any) -> float | None:
    """Return float(v) or None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _price_to_implied_prob(american_odds: float) -> float | None:
    """
    Convert American moneyline odds to raw implied probability.

    Positive odds (e.g. +120): prob = 100 / (100 + odds)
    Negative odds (e.g. -115): prob = |odds| / (|odds| + 100)
    """
    try:
        o = float(american_odds)
    except (TypeError, ValueError):
        return None
    if o == 0:
        return None
    if o > 0:
        return 100.0 / (100.0 + o)
    else:
        return abs(o) / (abs(o) + 100.0)


def _two_way_novig_prob(
    side_odds: float | None,
    other_side_odds: float | None,
) -> float | None:
    """
    Compute no-vig probability for the target side given both sides' American odds.
    Returns None if either side is unavailable.

    Method: normalize each implied probability by the sum (remove vig).
      p_novig = p_raw / (p_raw + p_other_raw)
    """
    if side_odds is None or other_side_odds is None:
        return None
    p_side  = _price_to_implied_prob(side_odds)
    p_other = _price_to_implied_prob(other_side_odds)
    if p_side is None or p_other is None:
        return None
    total = p_side + p_other
    if total <= 0:
        return None
    return p_side / total


# ---------------------------------------------------------------------------
# Gate 1 — Calibrated lower bound
# ---------------------------------------------------------------------------

def _check_lower_bound(
    row: dict[str, Any],
    slip_type: str,
    safety_buffer: float,
) -> dict[str, Any]:
    """
    Return {"passed": bool, "code": str, "detail": str, "lower_bound": float|None,
            "threshold": float}
    """
    threshold = BREAK_EVEN[slip_type] + safety_buffer

    lower_bound = _safe_float(
        row.get("calibrated_probability_lower_bound")
        or (row.get("gates") or {}).get("wnba_generative", {}).get("cal_lower_bound")
        or (row.get("gates") or {}).get("tennis_total_games", {}).get("cal_lower_bound")
        or row.get("lower_bound")
    )

    if lower_bound is None:
        return {
            "passed":      False,
            "code":        "LOWER_BOUND_UNAVAILABLE",
            "detail":      "calibrated_probability_lower_bound not present",
            "lower_bound": None,
            "threshold":   threshold,
        }

    if lower_bound < threshold:
        return {
            "passed":      False,
            "code":        "LOWER_BOUND_BELOW_THRESHOLD",
            "detail":      (
                f"lower_bound={lower_bound:.4f} < "
                f"break_even({BREAK_EVEN[slip_type]:.3f}) + "
                f"safety_buffer({safety_buffer:.3f}) = {threshold:.4f}"
            ),
            "lower_bound": lower_bound,
            "threshold":   threshold,
        }

    return {
        "passed":      True,
        "code":        "LOWER_BOUND_OK",
        "detail":      f"lower_bound={lower_bound:.4f} >= threshold={threshold:.4f}",
        "lower_bound": lower_bound,
        "threshold":   threshold,
    }


# ---------------------------------------------------------------------------
# Gate 2 — Two-way no-vig market check
# ---------------------------------------------------------------------------

def _check_novig(
    row: dict[str, Any],
    slip_type: str,
    safety_buffer: float,
) -> dict[str, Any]:
    """
    Two-way no-vig check.  Prefers explicit no-vig field; falls back to
    computing from American odds on both sides.

    If market odds are unavailable, the check uses calibrated_probability
    directly as a proxy (model is treated as the no-vig source).
    """
    threshold = BREAK_EVEN[slip_type] + safety_buffer

    # 1. Explicit no-vig field from ev_gate / market_gate
    novig_explicit = _safe_float(row.get("no_vig_probability"))
    if novig_explicit is not None:
        passed = novig_explicit >= threshold
        return {
            "passed":          passed,
            "code":            "NOVIG_OK" if passed else "NOVIG_BELOW_THRESHOLD",
            "detail":          f"no_vig={novig_explicit:.4f} vs threshold={threshold:.4f}",
            "novig_prob":      novig_explicit,
            "threshold":       threshold,
            "source":          "explicit_field",
        }

    # 2. Compute from both sides' American odds
    more_odds  = _safe_float(row.get("odds_more")  or row.get("price_more"))
    less_odds  = _safe_float(row.get("odds_less")  or row.get("price_less"))
    side       = (row.get("side") or row.get("direction") or "MORE").upper()

    if more_odds is not None and less_odds is not None:
        if side == "MORE":
            novig = _two_way_novig_prob(more_odds, less_odds)
        else:
            novig = _two_way_novig_prob(less_odds, more_odds)

        if novig is not None:
            passed = novig >= threshold
            return {
                "passed":     passed,
                "code":       "NOVIG_OK" if passed else "NOVIG_BELOW_THRESHOLD",
                "detail":     (
                    f"computed_novig={novig:.4f} from "
                    f"more_odds={more_odds} less_odds={less_odds} "
                    f"vs threshold={threshold:.4f}"
                ),
                "novig_prob": novig,
                "threshold":  threshold,
                "source":     "computed_from_odds",
            }

    # 3. Fall back to calibrated_probability as proxy for no-vig
    cal_prob = _safe_float(
        row.get("calibrated_probability")
        or (row.get("gates") or {}).get("wnba_generative", {}).get("cal_selected")
    )
    if cal_prob is not None:
        passed = cal_prob >= threshold
        return {
            "passed":     passed,
            "code":       "NOVIG_OK_PROXY" if passed else "NOVIG_BELOW_THRESHOLD_PROXY",
            "detail":     (
                f"calibrated_prob_proxy={cal_prob:.4f} vs threshold={threshold:.4f} "
                f"(no market odds available)"
            ),
            "novig_prob": cal_prob,
            "threshold":  threshold,
            "source":     "calibrated_probability_proxy",
        }

    # 4. Completely unavailable — fail-closed
    return {
        "passed":     False,
        "code":       "NOVIG_UNAVAILABLE",
        "detail":     "no no-vig probability or odds available — fail-closed",
        "novig_prob": None,
        "threshold":  threshold,
        "source":     "unavailable",
    }


# ---------------------------------------------------------------------------
# Gate 3 — Recency shock (Leave-One-Out)
# ---------------------------------------------------------------------------

def _check_recency_shock(
    row: dict[str, Any],
    slip_type: str,
    safety_buffer: float,
) -> dict[str, Any]:
    """
    LOO recency-shock check.

    Uses game_log (list of numeric results) and the cash_threshold from
    pp_thresholds.  Computes full hit rate and LOO hit rate (removing the
    single most extreme result).  If removing the extreme result changes the
    hit-rate verdict by >= RECENCY_SHOCK_THRESHOLD, block.

    Verdict change = |full_hit_rate - loo_hit_rate| >= RECENCY_SHOCK_THRESHOLD

    Requires at least 3 game-log entries to compute; passes vacuously otherwise.
    """
    threshold = BREAK_EVEN[slip_type] + safety_buffer

    game_log = row.get("game_log") or []
    if not isinstance(game_log, (list, tuple)):
        game_log = []

    # Only numeric entries
    numeric_log = []
    for entry in game_log:
        v = _safe_float(entry)
        if v is not None:
            numeric_log.append(v)

    if len(numeric_log) < 3:
        return {
            "passed":           True,
            "code":             "RECENCY_SHOCK_VACUOUS",
            "detail":           f"only {len(numeric_log)} numeric game-log entries (min 3 required)",
            "full_hit_rate":    None,
            "loo_hit_rate":     None,
            "extreme_removed":  None,
            "shock_magnitude":  None,
        }

    pp_t = (row.get("pp_thresholds") or {})
    cash_threshold = _safe_float(pp_t.get("cash_threshold"))
    side = (row.get("side") or row.get("direction") or "MORE").upper()

    def _is_hit(result: float) -> bool:
        if cash_threshold is None:
            return True  # cannot evaluate — vacuous
        if side == "MORE":
            return result >= cash_threshold
        return result <= cash_threshold

    def _hit_rate(log: list[float]) -> float:
        if not log:
            return 0.0
        return sum(1 for v in log if _is_hit(v)) / len(log)

    full_rate = _hit_rate(numeric_log)

    # Identify the most extreme single entry (furthest from cash_threshold)
    if cash_threshold is not None:
        extreme_idx = max(range(len(numeric_log)),
                          key=lambda i: abs(numeric_log[i] - cash_threshold))
    else:
        extreme_idx = 0  # fallback: remove most recent

    loo_log  = [v for i, v in enumerate(numeric_log) if i != extreme_idx]
    loo_rate = _hit_rate(loo_log) if loo_log else full_rate

    shock = abs(full_rate - loo_rate)

    if shock >= RECENCY_SHOCK_THRESHOLD:
        return {
            "passed":           False,
            "code":             "RECENCY_SHOCK_DETECTED",
            "detail":           (
                f"removing extreme result={numeric_log[extreme_idx]:.2f} "
                f"changes hit_rate from {full_rate:.3f} to {loo_rate:.3f} "
                f"(shock={shock:.3f} >= threshold={RECENCY_SHOCK_THRESHOLD:.3f})"
            ),
            "full_hit_rate":    round(full_rate, 4),
            "loo_hit_rate":     round(loo_rate, 4),
            "extreme_removed":  numeric_log[extreme_idx],
            "shock_magnitude":  round(shock, 4),
        }

    return {
        "passed":           True,
        "code":             "RECENCY_SHOCK_STABLE",
        "detail":           (
            f"hit_rate stable: full={full_rate:.3f} loo={loo_rate:.3f} "
            f"shock={shock:.3f} < {RECENCY_SHOCK_THRESHOLD:.3f}"
        ),
        "full_hit_rate":    round(full_rate, 4),
        "loo_hit_rate":     round(loo_rate, 4),
        "extreme_removed":  numeric_log[extreme_idx],
        "shock_magnitude":  round(shock, 4),
    }


# ---------------------------------------------------------------------------
# Public API — run gate on a single row
# ---------------------------------------------------------------------------

def run_row(
    row: dict[str, Any],
    safety_buffer: float = DEFAULT_SAFETY_BUFFER,
    slip_type_override: str | None = None,
) -> dict[str, Any]:
    """
    Evaluate the PrizePicks paid-card promotion gate for a single row.

    Mutates row["gates"]["pp_promotion"] and row["paid_card_qualified"].
    If the row fails while carrying a paid-card-eligible terminal_label,
    the terminal_label is capped at MARKET_VERIFIED_HOLD and the
    REJECT_PP_PROMOTION_GATE blocker is appended.

    The original probability rank and analysis output are preserved.

    Returns the gate result dict.
    """
    slip_type = slip_type_override or _resolve_slip_type(row)
    terminal  = row.get("terminal_label") or ""

    # Only rows with a paid-card-eligible label are subject to promotion
    # gate enforcement.  All rows still receive a gate result dict.
    eligible  = terminal in PAID_CARD_ELIGIBLE_LABELS

    g1 = _check_lower_bound(row, slip_type, safety_buffer)
    g2 = _check_novig(row, slip_type, safety_buffer)
    g3 = _check_recency_shock(row, slip_type, safety_buffer)

    overall_passed = g1["passed"] and g2["passed"] and g3["passed"]

    failures = []
    if not g1["passed"]:
        failures.append(g1["code"])
    if not g2["passed"]:
        failures.append(g2["code"])
    if not g3["passed"]:
        failures.append(g3["code"])

    result: dict[str, Any] = {
        "can_execute":             False,
        "execution_rule":          EXECUTION_RULE,
        "slip_type":               slip_type,
        "safety_buffer":           safety_buffer,
        "qualified":               overall_passed,
        "eligible_for_evaluation": eligible,
        "failure_codes":           failures,
        "lower_bound_check":       g1,
        "novig_check":             g2,
        "recency_shock_check":     g3,
    }

    row.setdefault("gates", {})["pp_promotion"] = result
    row["paid_card_qualified"] = overall_passed

    # Enforcement: cap terminal_label when eligible row fails
    if eligible and not overall_passed:
        blocker = f"PP_PROMOTION_GATE_FAIL:{','.join(failures) if failures else 'UNKNOWN'}"
        if blocker not in (row.get("blockers") or []):
            row.setdefault("blockers", []).append(blocker)
        row["terminal_label"] = _CAP_LABEL
        result["terminal_label_capped"] = True
        result["previous_terminal_label"] = terminal
    else:
        result["terminal_label_capped"] = False

    return result


def run(
    rows: list[dict[str, Any]],
    safety_buffer: float = DEFAULT_SAFETY_BUFFER,
) -> dict[str, Any]:
    """
    Run the promotion gate across a list of rows.

    Returns a batch report: total eligible, passed, failed, and per-row summaries.
    All mutations happen in-place on each row.
    """
    eligible_total = 0
    passed_total   = 0
    failed_total   = 0
    row_summaries  = []

    for row in rows:
        gate_result = run_row(row, safety_buffer=safety_buffer)
        if gate_result["eligible_for_evaluation"]:
            eligible_total += 1
            if gate_result["qualified"]:
                passed_total += 1
            else:
                failed_total += 1
        row_summaries.append({
            "row_id":               row.get("row_id"),
            "player":               row.get("player"),
            "slip_type":            gate_result["slip_type"],
            "paid_card_qualified":  gate_result["qualified"],
            "eligible":             gate_result["eligible_for_evaluation"],
            "failure_codes":        gate_result["failure_codes"],
            "terminal_label_capped": gate_result.get("terminal_label_capped"),
        })

    return {
        "can_execute":    False,
        "execution_rule": EXECUTION_RULE,
        "safety_buffer":  safety_buffer,
        "eligible_total": eligible_total,
        "passed_total":   passed_total,
        "failed_total":   failed_total,
        "row_summaries":  row_summaries,
    }
