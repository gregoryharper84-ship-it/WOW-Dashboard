"""
gate_engine/ml_settlement_truth.py
WOW-PATCH-2026-07-13 — P0-1: Official Result Must Override Platform Badge

A platform "Win" badge or promo payment must NEVER convert an official
model loss into a model win for calibration purposes.

Key principle:
  - official_event_result  = what actually happened (team won/lost)
  - selected_side_result   = did the selected team win? (WIN | LOSS | PUSH)
  - model_result           = calibration outcome — derived from selected_side_result
  - platform_settlement_status = reflects what the platform paid/showed
  - calibration_eligible   = True only when model_result is unambiguous

Hard rule:
  if platform_display_result == "WIN" and selected_side_result == "LOSS":
      platform_settlement_status = "PROMO_OR_SPECIAL_SETTLEMENT"
      model_result = "LOSS"

The platform payment is preserved in gross_return/net_return for financial
ROI tracking, but it never touches model_result or calibration_outcome.
"""
from __future__ import annotations

from typing import Any

from .ml_labels import MLReasonCode, PlatformSettlementStatus


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reconcile_settlement(entry: dict[str, Any]) -> dict[str, Any]:
    """
    Reconcile a single ML pick entry and return the truth-corrected record.

    Input fields:
        official_event_result   : str | None  — "HOME_WIN" | "AWAY_WIN" | "TIE" | "UNKNOWN"
        selected_side           : str | None  — team slug or "HOME" | "AWAY"
        selected_side_is_home   : bool | None — True if selected_side is the home team
        platform_display_result : str | None  — "WIN" | "LOSS" | "PUSH" | "REFUND"
        platform_payment        : float | None
        stake                   : float | None
        listed_return           : float | None
        promo_protection_active : bool        — True if a protection/insurance was applied

    Output fields (merged into returned dict):
        selected_side_result        : "WIN" | "LOSS" | "PUSH" | "UNKNOWN"
        official_event_result       : passed through / normalized
        platform_settlement_status  : PlatformSettlementStatus value
        model_result                : "WIN" | "LOSS" | "PUSH" | "UNKNOWN"
        calibration_outcome         : "WIN" | "LOSS" | "PUSH" (excludes UNKNOWN)
        calibration_eligible        : bool
        gross_return                : float | None  (platform payment)
        net_return                  : float | None  (gross_return - stake)
        promo_protection_status     : "APPLIED" | "NOT_APPLIED" | "UNKNOWN"
        reason_codes                : list[str]
        reconciliation_notes        : list[str]
    """
    out = dict(entry)
    reason_codes: list[str] = []
    notes: list[str] = []

    official = (entry.get("official_event_result") or "").upper().strip()
    selected_is_home = entry.get("selected_side_is_home")
    platform_display = (entry.get("platform_display_result") or "").upper().strip()
    platform_payment = entry.get("platform_payment")
    stake            = entry.get("stake")
    listed_return    = entry.get("listed_return")
    promo_active     = bool(entry.get("promo_protection_active", False))

    # ── 1. Derive selected_side_result from official result ─────────────────
    selected_side_result = _derive_selected_side_result(official, selected_is_home)
    out["selected_side_result"] = selected_side_result

    # ── 2. Determine platform settlement status ─────────────────────────────
    platform_status = _classify_platform_status(
        selected_side_result, platform_display, platform_payment, stake, promo_active
    )
    out["platform_settlement_status"] = platform_status
    out["promo_protection_status"] = (
        "APPLIED" if promo_active
        else ("UNKNOWN" if selected_side_result == "UNKNOWN" else "NOT_APPLIED")
    )

    # ── 3. Hard rule: platform promo/special payment never = model win ──────
    if (platform_display == "WIN" and selected_side_result == "LOSS"):
        model_result = "LOSS"
        reason_codes.append(MLReasonCode.PROMO_SETTLEMENT.value)
        reason_codes.append(MLReasonCode.OFFICIAL_LOSS.value)
        notes.append(
            f"Platform displayed 'WIN' but official result shows selected side LOST. "
            f"platform_settlement_status={platform_status}. "
            f"model_result forced to LOSS per settlement truth rule."
        )
    elif selected_side_result == "UNKNOWN":
        model_result = "UNKNOWN"
        notes.append("Official result unknown — model_result deferred.")
    else:
        model_result = selected_side_result   # WIN | LOSS | PUSH

    out["model_result"] = model_result

    # ── 4. Calibration eligibility ──────────────────────────────────────────
    calibration_outcome = model_result if model_result in ("WIN", "LOSS", "PUSH") else None
    out["calibration_outcome"]  = calibration_outcome
    out["calibration_eligible"] = calibration_outcome is not None

    # ── 5. Financial return fields (never tied to model_result) ────────────
    gross_return = _to_float(platform_payment)
    net_return   = None
    if gross_return is not None and stake is not None:
        try:
            net_return = round(gross_return - float(stake), 4)
        except (TypeError, ValueError):
            pass
    out["gross_return"] = gross_return
    out["net_return"]   = net_return

    # listed_return from the ticket (pre-settlement expectation)
    if listed_return is not None:
        out["listed_return"] = _to_float(listed_return)

    out["reason_codes"]         = reason_codes
    out["reconciliation_notes"] = notes
    return out


def classify_settlement_batch(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Reconcile a list of ML entries and return summary statistics.

    Returns:
        {
          entries              : [reconciled entries]
          total_tickets        : int
          platform_wins        : int   (what platform showed)
          model_wins           : int   (official model wins)
          model_losses         : int
          promo_settlements    : int
          calibration_eligible : int
          financial_roi        : float | None
          model_record         : str   e.g. "3-3"
          platform_record      : str   e.g. "5-2"
        }
    """
    reconciled = [reconcile_settlement(e) for e in entries]

    platform_wins    = sum(1 for r in reconciled if (r.get("platform_display_result") or "").upper() == "WIN")
    model_wins       = sum(1 for r in reconciled if r.get("model_result") == "WIN")
    model_losses     = sum(1 for r in reconciled if r.get("model_result") == "LOSS")
    promos           = sum(1 for r in reconciled if r.get("platform_settlement_status") == PlatformSettlementStatus.PROMO_OR_SPECIAL.value)
    cal_eligible     = sum(1 for r in reconciled if r.get("calibration_eligible"))

    total_tickets = len(reconciled)
    platform_losses = total_tickets - platform_wins

    total_stake  = _safe_sum(r.get("stake")        for r in reconciled)
    total_return = _safe_sum(r.get("gross_return")  for r in reconciled)
    financial_roi = None
    if total_stake and total_stake > 0:
        financial_roi = round((total_return - total_stake) / total_stake, 4) if total_return is not None else None

    return {
        "entries":               reconciled,
        "total_tickets":         total_tickets,
        "platform_wins":         platform_wins,
        "platform_losses":       platform_losses,
        "model_wins":            model_wins,
        "model_losses":          model_losses,
        "model_pushes":          sum(1 for r in reconciled if r.get("model_result") == "PUSH"),
        "promo_settlements":     promos,
        "calibration_eligible":  cal_eligible,
        "financial_roi":         financial_roi,
        "model_record":          f"{model_wins}-{model_losses}",
        "platform_record":       f"{platform_wins}-{platform_losses}",
        "execution_rule":        "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_selected_side_result(
    official_event_result: str,
    selected_is_home: bool | None,
) -> str:
    """
    Convert the official event result + which side was selected into
    a WIN/LOSS/PUSH/UNKNOWN for the selected side.
    """
    if not official_event_result or official_event_result == "UNKNOWN":
        return "UNKNOWN"

    if official_event_result == "TIE":
        return "PUSH"

    home_won = official_event_result == "HOME_WIN"
    away_won = official_event_result == "AWAY_WIN"

    if selected_is_home is None:
        return "UNKNOWN"

    if (selected_is_home and home_won) or (not selected_is_home and away_won):
        return "WIN"
    if (selected_is_home and away_won) or (not selected_is_home and home_won):
        return "LOSS"

    return "UNKNOWN"


def _classify_platform_status(
    selected_side_result: str,
    platform_display: str,
    platform_payment: float | None,
    stake: float | None,
    promo_active: bool,
) -> str:
    """Classify what the platform actually did vs. what should have happened."""
    if selected_side_result == "LOSS" and platform_display == "WIN":
        return PlatformSettlementStatus.PROMO_OR_SPECIAL.value

    if promo_active and selected_side_result == "LOSS":
        return PlatformSettlementStatus.PROMO_OR_SPECIAL.value

    if platform_display == "WIN":
        return PlatformSettlementStatus.SETTLED_WIN.value
    if platform_display in ("LOSS", "L"):
        return PlatformSettlementStatus.SETTLED_LOSS.value
    if platform_display in ("PUSH", "TIE", "REFUND"):
        return PlatformSettlementStatus.PUSH.value

    return PlatformSettlementStatus.UNKNOWN.value


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_sum(it) -> float | None:
    total = 0.0
    any_val = False
    for v in it:
        f = _to_float(v)
        if f is not None:
            total += f
            any_val = True
    return total if any_val else None
