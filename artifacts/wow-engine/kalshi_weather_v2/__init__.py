"""Governed Kalshi Weather V2 specialist agents and terminal reducer."""

from .agents import ContractSettlementAgent, MarketCalibrationAuditor, WeatherProbabilityAgent
from .calibration_fit import CalibrationFitResult, ForecastResidual, fit_candidate_calibration_profile
from .contract_resolver import ContractResolutionError, resolve_weather_contract
from .contract_rule_acquisition import (
    ContractRuleAcquisitionError,
    FrozenContractRulePackage,
    KalshiContractRuleAcquirer,
    SettlementSourceEvidence,
    resolve_settlement_source,
)
from .fee_policy import (
    DIRECT_BALANCE_QUANTUM,
    NON_DIRECT_BALANCE_QUANTUM,
    FeePolicyError,
    FeePolicySnapshot,
    TradeFeeQuote,
    quote_new_order_single_fill_cash_fee,
    quote_trade_fee,
    resolve_fee_policy,
)
from .hourly_forecast_fusion import (
    HourlyForecastEstimate,
    HourlyForecastFusion,
    HourlyForecastFusionError,
    build_hourly_weather_evidence,
    source_snapshot_id,
)
from .hourly_index import (
    KalshiWeatherIndexAdapter,
    WeatherIndexCalibrationSnapshot,
    WeatherIndexError,
    WeatherIndexPoint,
    WeatherIndexSnapshot,
    WeatherIndexStationReading,
    parse_weather_index_payload,
)
from .hourly_rule_semantics import ParsedHourlyTemperatureRule, parse_hourly_temperature_rule
from .http_client import HttpAcquisitionError, HttpPolicy, ReadOnlyJsonClient
from .kalshi_market_data import KalshiMarketDataError, KalshiOrderbookEvidence, KalshiPublicMarketAdapter
from .models import (
    AgentResult,
    ContractSnapshot,
    MarketSnapshot,
    ProbabilityPackage,
    TerminalDecision,
    WeatherEvidenceSnapshot,
)
from .observation_reconstruction import ObservationPoint, ReconstructedExtreme, reconstruct_extreme, reconstruct_temperature_series
from .orchestrator import evaluate_weather_contract
from .probability_core import CalibrationProfile, WeatherProbabilityCore
from .rule_semantics import ParsedTemperatureRule, parse_temperature_rule
from .rule_snapshot import FrozenRuleSnapshot, RuleSnapshotError, freeze_market_rules
from .source_adapters import NoaaNceiAdapter, NwsAdapter, OpenMeteoAdapter, ProviderSnapshot, XweatherAdapter
from .terminal_governor import KalshiWeatherTerminalGovernor

__all__ = [
    "AgentResult",
    "ContractSnapshot",
    "MarketSnapshot",
    "ProbabilityPackage",
    "TerminalDecision",
    "WeatherEvidenceSnapshot",
    "ContractSettlementAgent",
    "WeatherProbabilityAgent",
    "MarketCalibrationAuditor",
    "KalshiWeatherTerminalGovernor",
    "ContractResolutionError",
    "resolve_weather_contract",
    "ContractRuleAcquisitionError",
    "FrozenContractRulePackage",
    "KalshiContractRuleAcquirer",
    "SettlementSourceEvidence",
    "resolve_settlement_source",
    "ParsedTemperatureRule",
    "parse_temperature_rule",
    "ParsedHourlyTemperatureRule",
    "parse_hourly_temperature_rule",
    "HourlyForecastFusionError",
    "HourlyForecastEstimate",
    "HourlyForecastFusion",
    "build_hourly_weather_evidence",
    "source_snapshot_id",
    "FrozenRuleSnapshot",
    "RuleSnapshotError",
    "freeze_market_rules",
    "CalibrationProfile",
    "WeatherProbabilityCore",
    "ForecastResidual",
    "CalibrationFitResult",
    "fit_candidate_calibration_profile",
    "ObservationPoint",
    "ReconstructedExtreme",
    "reconstruct_temperature_series",
    "reconstruct_extreme",
    "ProviderSnapshot",
    "NwsAdapter",
    "OpenMeteoAdapter",
    "NoaaNceiAdapter",
    "XweatherAdapter",
    "WeatherIndexError",
    "WeatherIndexPoint",
    "WeatherIndexStationReading",
    "WeatherIndexSnapshot",
    "WeatherIndexCalibrationSnapshot",
    "KalshiWeatherIndexAdapter",
    "parse_weather_index_payload",
    "HttpAcquisitionError",
    "HttpPolicy",
    "ReadOnlyJsonClient",
    "KalshiMarketDataError",
    "KalshiOrderbookEvidence",
    "KalshiPublicMarketAdapter",
    "FeePolicyError",
    "FeePolicySnapshot",
    "TradeFeeQuote",
    "DIRECT_BALANCE_QUANTUM",
    "NON_DIRECT_BALANCE_QUANTUM",
    "resolve_fee_policy",
    "quote_trade_fee",
    "quote_new_order_single_fill_cash_fee",
    "evaluate_weather_contract",
]
