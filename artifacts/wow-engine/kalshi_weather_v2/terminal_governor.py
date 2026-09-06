from __future__ import annotations

from .models import AgentResult, TerminalDecision


class KalshiWeatherTerminalGovernor:
    """Deterministic lowest-ceiling reducer for Kalshi Weather V2.

    This component does not forecast, reinterpret evidence, or vote. It only
    reduces structured specialist results into one terminal state.
    """

    name = "KALSHI_WEATHER_TERMINAL_GOVERNOR"

    def reduce(
        self,
        *,
        settlement: AgentResult,
        probability: AgentResult,
        market: AgentResult,
    ) -> TerminalDecision:
        blockers = tuple(dict.fromkeys(settlement.blockers + probability.blockers + market.blockers))
        warnings = tuple(dict.fromkeys(settlement.warnings + probability.warnings + market.warnings))

        if not settlement.ok:
            return TerminalDecision(
                status="NO_PLAY_SETTLEMENT_AMBIGUITY",
                code="SETTLEMENT_GATE_FAILED",
                rank_eligible=False,
                probability_publishable=False,
                edge_publishable=False,
                blockers=blockers,
                warnings=warnings,
                payload=self._payload(settlement, probability, market),
            )

        if not probability.ok:
            return TerminalDecision(
                status="NO_PLAY_DATA_INSUFFICIENT",
                code="WEATHER_PROBABILITY_GATE_FAILED",
                rank_eligible=False,
                probability_publishable=False,
                edge_publishable=False,
                blockers=blockers,
                warnings=warnings,
                payload=self._payload(settlement, probability, market),
            )

        if not market.ok:
            return TerminalDecision(
                status="WATCH",
                code="WEATHER_PROBABILITY_READY_MARKET_EDGE_HELD",
                rank_eligible=False,
                probability_publishable=True,
                edge_publishable=False,
                blockers=blockers,
                warnings=warnings,
                payload=self._payload(settlement, probability, market),
            )

        side, adjusted_edge, raw_edge = self._best_side(market.payload)
        if adjusted_edge is None:
            return TerminalDecision(
                status="WATCH",
                code="UNCERTAINTY_ADJUSTED_EDGE_UNRESOLVED",
                rank_eligible=False,
                probability_publishable=True,
                edge_publishable=True,
                blockers=blockers,
                warnings=warnings,
                payload={**self._payload(settlement, probability, market), "best_side": side},
            )

        if adjusted_edge <= 0:
            status = "NO_EDGE"
            code = "NO_POSITIVE_UNCERTAINTY_ADJUSTED_EDGE"
            rank_eligible = False
        else:
            # Edge thresholds are deliberately not invented here. A positive
            # conservative edge is QUALIFIED_EDGE until calibration policy
            # establishes an evidence-based STRONG_EDGE threshold.
            status = "QUALIFIED_EDGE"
            code = "POSITIVE_UNCERTAINTY_ADJUSTED_EDGE"
            rank_eligible = True

        return TerminalDecision(
            status=status,
            code=code,
            rank_eligible=rank_eligible,
            probability_publishable=True,
            edge_publishable=True,
            blockers=blockers,
            warnings=warnings,
            payload={
                **self._payload(settlement, probability, market),
                "best_side": side,
                "best_raw_edge": raw_edge,
                "best_uncertainty_adjusted_edge": adjusted_edge,
            },
        )

    @staticmethod
    def _payload(settlement: AgentResult, probability: AgentResult, market: AgentResult) -> dict:
        return {
            "governor": KalshiWeatherTerminalGovernor.name,
            "settlement_agent_code": settlement.code,
            "probability_agent_code": probability.code,
            "market_auditor_code": market.code,
            "settlement": dict(settlement.payload),
            "probability": dict(probability.payload),
            "market": dict(market.payload),
            "can_execute": False,
        }

    @staticmethod
    def _best_side(payload) -> tuple[str | None, float | None, float | None]:
        candidates: list[tuple[str, float, float | None]] = []
        for side in ("yes", "no"):
            adjusted = payload.get(f"uncertainty_adjusted_edge_{side}")
            raw = payload.get(f"raw_edge_{side}")
            if isinstance(adjusted, (int, float)):
                candidates.append((side.upper(), float(adjusted), float(raw) if isinstance(raw, (int, float)) else None))
        if not candidates:
            return None, None, None
        return max(candidates, key=lambda item: item[1])
