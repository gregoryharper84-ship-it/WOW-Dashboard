"""
skills/adapters/kalshi_contract.py
Kalshi Contract Intelligence adapter.

Invariants enforced here (acceptance tests 8-11):
  8.  Kalshi price age > 10 min  → DATA_UNOBTAINABLE
  9.  Empty orderbook            → DATA_UNOBTAINABLE
  10. Market closed              → REJECT_BAD_RULES
  11. INVENTORY_EMPTY            → immediate stop, no scan
  Additional: can_execute always False; synthetic/operator prices cap at WATCH.
"""
from __future__ import annotations

from ..contracts import SkillLabel, SkillResult, FRESHNESS_LIVE_PRICE, SOURCE_QUALITY_OPERATOR_SUPPLIED
from .base import BaseSkillAdapter

SKILL_ID      = "wow.kalshi-contract-intelligence"
SKILL_VERSION = "1.0.0"

# Dry-run label (acceptance test 13)
DRY_RUN_LABEL = "LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN"


class KalshiContractAdapter(BaseSkillAdapter):
    SKILL_ID      = SKILL_ID
    SKILL_VERSION = SKILL_VERSION

    def run(self, context: dict, run_id: str | None = None) -> SkillResult:
        inputs = self._inputs_or_empty(context)

        # ── Acceptance test 11: INVENTORY_EMPTY stops scan ────────────────────
        inventory_health = context.get("kalshi_inventory_health", "")
        if inventory_health == "INVENTORY_EMPTY":
            return SkillResult.reject(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                code="KALSHI_INVENTORY_EMPTY",
                message="Kalshi sports inventory EMPTY — scan stopped.",
                label=SkillLabel.DATA_UNOBTAINABLE.value,
                run_id=run_id,
            )

        # Only proceed with scan if INVENTORY_READY or non-sports (weather etc.)
        # For sports lanes, a missing/unknown health is treated as non-sports.

        # ── Acceptance test 10: Market closed ─────────────────────────────────
        market_status = context.get("kalshi_market_status", "")
        if market_status in ("closed", "settled", "finalized"):
            return SkillResult.reject(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                code="KALSHI_MARKET_CLOSED",
                message=f"Kalshi market is closed/settled (status={market_status!r}).",
                label=SkillLabel.REJECT_BAD_RULES.value,
                run_id=run_id,
            )

        # ── Acceptance test 8: Stale price > 10 minutes ───────────────────────
        price_age = context.get("kalshi_price_age_seconds")
        if price_age is not None:
            stale = self._kalshi_freshness_check(price_age, inputs, run_id)
            if stale:
                return stale

        # ── Acceptance test 9: Empty orderbook ────────────────────────────────
        orderbook = context.get("kalshi_orderbook") or {}
        yes_bids = orderbook.get("yes_bids", [])
        no_bids  = orderbook.get("no_bids", [])
        if not yes_bids and not no_bids:
            if context.get("kalshi_orderbook") is not None:
                # Orderbook was provided but empty
                return SkillResult.unobtainable(
                    skill_id=self.SKILL_ID,
                    skill_version=self.SKILL_VERSION,
                    inputs=inputs,
                    reason="Empty Kalshi orderbook — no YES or NO bids available.",
                    run_id=run_id,
                )

        # ── Source quality: synthetic/operator-supplied caps at WATCH ─────────
        source_type = context.get("kalshi_source_type", "direct")
        if source_type in ("synthetic_test", "operator_supplied"):
            return SkillResult.watch(
                skill_id=self.SKILL_ID,
                skill_version=self.SKILL_VERSION,
                inputs=inputs,
                findings=[{"note": f"Source type {source_type!r} caps at WATCH."}],
                reason=(f"SOURCE_CAP: {source_type} evidence cannot be a direct/live source. "
                        "Caps at WATCH per SHARED_CONTRACT.md."),
                confidence=0.25,
                run_id=run_id,
            )

        # ── Build findings from context ───────────────────────────────────────
        ticker  = context.get("kalshi_ticker", "")
        market_type = context.get("kalshi_market_category", "unknown")
        findings: list[dict] = [
            {"ticker": ticker, "market_category": market_type,
             "inventory_health": inventory_health, "source_type": source_type},
        ]
        if yes_bids or no_bids:
            findings.append({"orderbook_summary":
                             {"yes_bids": len(yes_bids), "no_bids": len(no_bids)}})

        # ── Happy path: dry-run fill ledger entry ─────────────────────────────
        return SkillResult(
            skill_id=self.SKILL_ID,
            skill_version=self.SKILL_VERSION,
            run_id=run_id or None,
            inputs_used=inputs,
            sources=[{"source_id": "kalshi_api", "quality": 1}],
            source_timestamps=[context.get("kalshi_price_timestamp", "")],
            data_quality="complete",
            findings=findings,
            blockers=[],
            # Dry-run label — execution is audit only
            label=DRY_RUN_LABEL,
            confidence=0.6,
            can_execute=False,
            downstream=["wow.bankroll-risk-manager", "wow.qa-hallucination-auditor"],
        )
