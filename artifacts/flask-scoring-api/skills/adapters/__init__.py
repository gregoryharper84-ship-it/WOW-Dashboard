"""Adapter registry — maps skill_id → adapter class."""
from .sports_research   import SportsResearchAdapter
from .market_odds       import MarketOddsAdapter
from .kalshi_contract   import KalshiContractAdapter
from .player_prop       import PlayerPropAdapter
from .game_script       import GameScriptAdapter
from .mlb_pitching      import MlbPitchingAdapter
from .mlb_hitting       import MlbHittingAdapter
from .wnba_specialist   import WnbaSpecialistAdapter
from .correlation_slip  import CorrelationSlipAdapter
from .probability_ev    import ProbabilityEvAdapter
from .weather_intel     import WeatherIntelAdapter
from .bankroll_risk     import BankrollRiskAdapter
from .qa_auditor        import QaAuditorAdapter
from .patch_governance  import PatchGovernanceAdapter
from .lottery_analyst   import LotteryAnalystAdapter
from .financial_market  import FinancialMarketAdapter
from .historical_trend  import HistoricalTrendAdapter
from .promo_optimizer   import PromoOptimizerAdapter
from .dfs_analyst       import DfsAnalystAdapter
from .sports_psychology import SportsPsychologyAdapter
from .referee_umpire    import RefereeUmpireAdapter

ADAPTER_MAP: dict = {
    "wow.sports-research-analyst":    SportsResearchAdapter,
    "wow.market-odds-intelligence":   MarketOddsAdapter,
    "wow.kalshi-contract-intelligence": KalshiContractAdapter,
    "wow.player-prop-intelligence":   PlayerPropAdapter,
    "wow.game-script-simulator":      GameScriptAdapter,
    "wow.mlb-pitching-expert":        MlbPitchingAdapter,
    "wow.mlb-hitting-expert":         MlbHittingAdapter,
    "wow.wnba-specialist":            WnbaSpecialistAdapter,
    "wow.correlation-slip-auditor":   CorrelationSlipAdapter,
    "wow.probability-ev-auditor":     ProbabilityEvAdapter,
    "wow.weather-intelligence":       WeatherIntelAdapter,
    "wow.bankroll-risk-manager":      BankrollRiskAdapter,
    "wow.qa-hallucination-auditor":   QaAuditorAdapter,
    "wow.patch-governance-architect": PatchGovernanceAdapter,
    "wow.lottery-analyst":            LotteryAnalystAdapter,
    "wow.financial-market-analyst":   FinancialMarketAdapter,
    "wow.historical-trend-researcher": HistoricalTrendAdapter,
    "wow.sportsbook-promo-optimizer": PromoOptimizerAdapter,
    "wow.dfs-analyst":                DfsAnalystAdapter,
    "wow.sports-psychology-context":  SportsPsychologyAdapter,
    "wow.referee-umpire-tendency":    RefereeUmpireAdapter,
}
