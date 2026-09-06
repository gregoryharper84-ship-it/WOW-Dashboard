"""Governed Kalshi Weather V2 specialist agents and terminal reducer."""

from .agents import ContractSettlementAgent, MarketCalibrationAuditor, WeatherProbabilityAgent
from .calibration_fit import CalibrationFitResult, ForecastResidual, fit_candidate_calibration_profile
from .contract_resolver import ContractResolutionError, resolve_weather_contract
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
    "evaluate_weather_contract",
]
