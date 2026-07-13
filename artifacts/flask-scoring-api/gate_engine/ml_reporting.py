"""
gate_engine/ml_reporting.py
WOW-PATCH-2026-07-13 — P2-8 + P2-9

P2-8: Report independent-event results, not ticket results.
P2-9: Calibrate by implied-probability bucket.

The dashboard must never headline "5–2" when the official independent-
event record is 3–3.  Financial ticket count and model observation count
are always shown separately.

Probability buckets:
    52–55%, 55–60%, 60–65%, 65–70%, 70%+
"""
from __future__ import annotations

from typing import Any

from .ml_deduplication import deduplicate_entries


# ---------------------------------------------------------------------------
# Probability bucket definitions (P2-9)
# ---------------------------------------------------------------------------

PROB_BUCKETS: list[tuple[float, float, str]] = [
    (0.52, 0.55, "52–55%"),
    (0.55, 0.60, "55–60%"),
    (0.60, 0.65, "60–65%"),
    (0.65, 0.70, "65–70%"),
    (0.70, 1.01, "70%+"),
]


def _get_bucket(prob: float | None) -> str | None:
    if prob is None:
        return None
    for lo, hi, label in PROB_BUCKETS:
        if lo <= prob < hi:
            return label
    return None


# ---------------------------------------------------------------------------
# P2-8: Performance summary (independent-event basis)
# ---------------------------------------------------------------------------

def build_ml_performance_summary(
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Build a performance summary that reports independent-event results as
    the primary metric, not financial ticket results.

    Each entry must have been through ml_settlement_truth.reconcile_settlement()
    so that model_result and calibration_eligible are populated.

    Returns:
        {
          financial_tickets          : int
          independent_model_observations : int
          official_model_record      : str   e.g. "3-3"
          platform_displayed_wins    : int
          promo_special_settlements  : int
          duplicate_entries          : int
          ---
          ticket_hit_rate            : float | None
          independent_event_hit_rate : float | None
          calibration_hit_rate       : float | None
          gross_roi                  : float | None
          net_roi                    : float | None
          promo_adjusted_roi         : float | None
          duplicate_adjusted_roi     : float | None
          ---
          by_probability_bucket      : dict  (P2-9)
          ---
          display_warning            : str | None
          execution_rule             : str
        }
    """
    dedup = deduplicate_entries(entries)
    unique_events = dedup["summary"]["unique_events"]
    dup_tickets   = dedup["summary"]["duplicate_tickets"]

    # Aggregate across canonical events (take first entry per event for model stats)
    model_wins   = 0
    model_losses = 0
    model_pushes = 0
    cal_wins     = 0
    cal_losses   = 0
    platform_wins = 0
    promos        = 0
    cal_eligible  = 0

    total_stake_financial = 0.0
    total_return_financial = 0.0
    any_stake = False

    # For probability-bucket calibration, use one observation per unique event
    bucket_data: dict[str, list[dict[str, Any]]] = {}
    primary_entries: list[dict[str, Any]] = []

    for canonical in dedup["canonical_events"].values():
        # Representative entry for model stats = first entry in group
        rep = canonical["entries"][0]
        primary_entries.append(rep)

        model_result = (rep.get("model_result") or "").upper()
        if model_result == "WIN":
            model_wins += 1
        elif model_result == "LOSS":
            model_losses += 1
        elif model_result == "PUSH":
            model_pushes += 1

        if rep.get("calibration_eligible"):
            cal_eligible += 1
            if model_result == "WIN":
                cal_wins += 1
            elif model_result == "LOSS":
                cal_losses += 1

        # Bucket assignment
        prob = _to_float(rep.get("model_prob") or rep.get("model_probability") or rep.get("approved_model_prob"))
        bucket = _get_bucket(prob)
        if bucket:
            bucket_data.setdefault(bucket, []).append(rep)

    # Financial aggregates across ALL tickets (not just primary)
    for entry in entries:
        s = _to_float(entry.get("stake"))
        r = _to_float(entry.get("gross_return"))
        if s is not None:
            total_stake_financial += s
            any_stake = True
        if r is not None:
            total_return_financial += r
        if (entry.get("platform_display_result") or "").upper() == "WIN":
            platform_wins += 1
        if entry.get("platform_settlement_status") in (
            "PROMO_OR_SPECIAL_SETTLEMENT", "LLP_SETTLEMENT_RECONCILIATION_REQUIRED"
        ):
            promos += 1

    total_tickets = len(entries)

    # ROI calculations
    gross_roi = None
    net_roi   = None
    promo_roi = None
    dup_roi   = None

    if any_stake and total_stake_financial > 0:
        gross_roi = round((total_return_financial - total_stake_financial) / total_stake_financial, 4)
        net_roi   = gross_roi   # same unless platform fees exist

        # Promo-adjusted ROI: exclude promo entries from return (treat as 0 profit)
        promo_return_ex = sum(
            _to_float(e.get("gross_return")) or 0.0
            for e in entries
            if e.get("platform_settlement_status") != "PROMO_OR_SPECIAL_SETTLEMENT"
        )
        promo_roi = round((promo_return_ex - total_stake_financial) / total_stake_financial, 4)

        # Duplicate-adjusted ROI: count only primary observations' stakes
        primary_stake = sum(
            _to_float(e.get("stake")) or 0.0
            for e in primary_entries
        )
        primary_return = sum(
            _to_float(e.get("gross_return")) or 0.0
            for e in primary_entries
        )
        if primary_stake > 0:
            dup_roi = round((primary_return - primary_stake) / primary_stake, 4)

    # Ticket hit rate (platform wins / total tickets)
    ticket_hr = round(platform_wins / total_tickets, 4) if total_tickets else None
    # Independent event hit rate (model wins / unique events)
    ie_hr = round(model_wins / unique_events, 4) if unique_events else None
    # Calibration hit rate
    cal_hr = round(cal_wins / cal_eligible, 4) if cal_eligible else None

    # P2-9: by probability bucket
    by_bucket = _build_bucket_stats(bucket_data)

    # Display warning if platform record differs from model record
    platform_losses = total_tickets - platform_wins
    platform_record_str = f"{platform_wins}-{platform_losses}"
    model_record_str    = f"{model_wins}-{model_losses}"
    display_warning = None
    if platform_record_str != model_record_str:
        display_warning = (
            f"Platform displayed record: {platform_record_str}. "
            f"Official independent-event model record: {model_record_str}. "
            f"Do NOT headline platform record — use official model record."
        )

    return {
        "financial_tickets":                total_tickets,
        "independent_model_observations":   unique_events,
        "duplicate_entries":                dup_tickets,
        "official_model_record":            model_record_str,
        "official_model_wins":              model_wins,
        "official_model_losses":            model_losses,
        "official_model_pushes":            model_pushes,
        "platform_displayed_wins":          platform_wins,
        "promo_special_settlements":        promos,
        "calibration_eligible_events":      cal_eligible,
        "ticket_hit_rate":                  ticket_hr,
        "independent_event_hit_rate":       ie_hr,
        "calibration_hit_rate":             cal_hr,
        "gross_roi":                        gross_roi,
        "net_roi":                          net_roi,
        "promo_adjusted_roi":               promo_roi,
        "duplicate_adjusted_roi":           dup_roi,
        "by_probability_bucket":            by_bucket,
        "display_warning":                  display_warning,
        "execution_rule":                   "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
    }


# ---------------------------------------------------------------------------
# P2-9: Probability bucket calibration stats
# ---------------------------------------------------------------------------

def _build_bucket_stats(
    bucket_data: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Build per-bucket calibration statistics."""
    result: dict[str, dict[str, Any]] = {}

    for label, records in bucket_data.items():
        wins   = sum(1 for r in records if (r.get("model_result") or "").upper() == "WIN")
        losses = sum(1 for r in records if (r.get("model_result") or "").upper() == "LOSS")
        graded = wins + losses

        probs = [
            p for r in records
            if (p := _to_float(
                r.get("model_prob") or r.get("model_probability") or r.get("approved_model_prob")
            )) is not None
        ]
        be_probs = [
            p for r in records
            if (p := _to_float(r.get("breakeven_prob") or r.get("approved_breakeven_prob"))) is not None
        ]
        edges = [
            p for r in records
            if (p := _to_float(r.get("verified_edge") or r.get("approved_edge"))) is not None
        ]
        stakes = [s for r in records if (s := _to_float(r.get("stake"))) is not None]
        returns = [v for r in records if (v := _to_float(r.get("gross_return"))) is not None]

        avg_prob    = round(sum(probs)    / len(probs),    4) if probs    else None
        avg_be      = round(sum(be_probs) / len(be_probs), 4) if be_probs else None
        avg_edge    = round(sum(edges)    / len(edges),    4) if edges    else None
        actual_wr   = round(wins / graded, 4) if graded else None
        exp_wins    = round(avg_prob * graded, 2) if avg_prob and graded else None

        # Brier score: Σ(p-o)² / n
        brier: float | None = None
        brier_records = []
        for r in records:
            p = _to_float(r.get("model_prob") or r.get("model_probability"))
            mr = (r.get("model_result") or "").upper()
            if p is not None and mr in ("WIN", "LOSS"):
                o = 1.0 if mr == "WIN" else 0.0
                brier_records.append((p - o) ** 2)
        if brier_records:
            brier = round(sum(brier_records) / len(brier_records), 6)

        total_stake  = sum(stakes)  if stakes  else None
        total_return = sum(returns) if returns else None
        roi = None
        if total_stake and total_stake > 0 and total_return is not None:
            roi = round((total_return - total_stake) / total_stake, 4)

        result[label] = {
            "independent_events":       len(records),
            "wins":                     wins,
            "losses":                   losses,
            "expected_wins":            exp_wins,
            "actual_win_rate":          actual_wr,
            "average_model_prob":       avg_prob,
            "average_breakeven_prob":   avg_be,
            "average_edge":             avg_edge,
            "brier_score":              brier,
            "roi":                      roi,
        }

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
