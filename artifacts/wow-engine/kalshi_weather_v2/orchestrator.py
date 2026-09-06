from __future__ import annotations

from dataclasses import asdict

from .agents import ContractSettlementAgent, MarketCalibrationAuditor, WeatherProbabilityAgent
from .models import ContractSnapshot, MarketSnapshot, ProbabilityPackage, TerminalDecision, WeatherEvidenceSnapshot
from .terminal_governor import KalshiWeatherTerminalGovernor


def evaluate_weather_contract(
    *,
    contract: ContractSnapshot,
    evidence: WeatherEvidenceSnapshot,
    probability: ProbabilityPackage,
    market: MarketSnapshot,
) -> TerminalDecision:
    """Run the fixed Kalshi Weather V2 specialist chain.

    Agents do not vote and cannot modify one another's outputs. The terminal
    governor is the sole reducer. The host remains non-executable.
    """
    settlement_result = ContractSettlementAgent().evaluate(contract, evidence)
    probability_result = WeatherProbabilityAgent().evaluate(evidence, probability)
    market_result = MarketCalibrationAuditor().evaluate(probability, market)

    decision = KalshiWeatherTerminalGovernor().reduce(
        settlement=settlement_result,
        probability=probability_result,
        market=market_result,
    )

    return TerminalDecision(
        status=decision.status,
        code=decision.code,
        rank_eligible=decision.rank_eligible,
        probability_publishable=decision.probability_publishable,
        edge_publishable=decision.edge_publishable,
        blockers=decision.blockers,
        warnings=decision.warnings,
        payload={
            **dict(decision.payload),
            "contract_identity": {
                "ticker": contract.ticker,
                "lane": contract.lane,
                "rule_snapshot_id": contract.rule_snapshot_id,
            },
            "analysis_time": evidence.analysis_time,
            "agent_results": {
                "settlement": asdict(settlement_result),
                "probability": asdict(probability_result),
                "market": asdict(market_result),
            },
        },
        can_execute=False,
    )
