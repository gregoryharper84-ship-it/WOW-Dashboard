from __future__ import annotations

from .models import AgentResult, TerminalDecision


class KalshiWeatherTerminalGovernor:
    """Deterministic weather-domain audit reducer beneath V17 global authority.

    This component does not forecast, reinterpret evidence, vote, or publish.
    It reduces structured weather specialist results into a local/native status
    only. V17_TERMINAL_REDUCER is the sole authority that may expose a model
    probability, edge, or rank-eligible recommendation.
    """

    name = "KALSHI_WEATHER_TERMINAL_GOVERNOR"
    global_terminal_authority = "V17_TERMINAL_REDUCER"

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
            status, code = "NO_PLAY_SETTLEMENT_AMBIGUITY", "SETTLEMENT_GATE_FAILED"
            payload = self._payload(settlement, probability, market)
        elif not probability.ok:
            status, code = "NO_PLAY_DATA_INSUFFICIENT", "WEATHER_PROBABILITY_GATE_FAILED"
            payload = self._payload(settlement, probability, market)
        elif not market.ok:
            status, code = "WATCH", "WEATHER_PROBABILITY_READY_MARKET_EDGE_HELD"
            payload = self._payload(settlement, probability, market)
        else:
            side, adjusted_edge, raw_edge = self._best_side(market.payload)
            payload = {
                **self._payload(settlement, probability, market),
                "best_side": side,
                "best_raw_edge": raw_edge,
                "best_uncertainty_adjusted_edge": adjusted_edge,
            }
            if adjusted_edge is None:
                status, code = "WATCH", "UNCERTAINTY_ADJUSTED_EDGE_UNRESOLVED"
            elif adjusted_edge <= 0:
                status, code = "NO_EDGE", "NO_POSITIVE_UNCERTAINTY_ADJUSTED_EDGE"
            else:
                # Do not invent a STRONG_EDGE threshold. Positive conservative
                # edge remains a local QUALIFIED_EDGE candidate until V17's
                # global publication/reconciliation gates complete.
                status, code = "QUALIFIED_EDGE", "POSITIVE_UNCERTAINTY_ADJUSTED_EDGE"

        # Critical V17 boundary: local weather labels are audit-only. A local
        # specialist may never create a globally publishable/rankable row.
        return TerminalDecision(
            status=status,
            code=code,
            rank_eligible=False,
            probability_publishable=False,
            edge_publishable=False,
            blockers=blockers,
            warnings=warnings,
            payload=payload,
        )

    @staticmethod
    def _payload(settlement: AgentResult, probability: AgentResult, market: AgentResult) -> dict:
        return {
            "governor": KalshiWeatherTerminalGovernor.name,
            "local_terminal_label_audit_only": True,
            "local_global_terminal_authority": False,
            "global_terminal_authority_required": KalshiWeatherTerminalGovernor.global_terminal_authority,
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
