from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping


class UncertaintyError(ValueError):
    pass


@dataclass(frozen=True)
class UncertaintyDecomposition:
    """Downstream decision uncertainty; never a market-driven weather-probability input."""

    aleatoric: float
    epistemic: float
    data: float
    settlement: float
    unit: str = "PROBABILITY_SD"
    method: str = "EXPLICIT_COMPONENTS_V1"
    evidence: Mapping[str, str] | None = None
    market_price_used_as_weather_input: bool = False

    def __post_init__(self) -> None:
        for name in ("aleatoric", "epistemic", "data", "settlement"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise UncertaintyError(f"{name.upper()}_UNCERTAINTY_INVALID")
            number = float(value)
            if not math.isfinite(number) or number < 0.0:
                raise UncertaintyError(f"{name.upper()}_UNCERTAINTY_INVALID")
        if self.market_price_used_as_weather_input:
            raise UncertaintyError("MARKET_PRICE_WEATHER_INPUT_PROHIBITED")
        if not self.unit.strip():
            raise UncertaintyError("UNCERTAINTY_UNIT_MISSING")
        if not self.method.strip():
            raise UncertaintyError("UNCERTAINTY_METHOD_MISSING")

    def as_dict(self) -> dict[str, object]:
        return {
            "aleatoric": float(self.aleatoric),
            "epistemic": float(self.epistemic),
            "data": float(self.data),
            "settlement": float(self.settlement),
            "unit": self.unit,
            "method": self.method,
            "evidence": dict(self.evidence or {}),
            "market_price_used_as_weather_input": False,
        }
