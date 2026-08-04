from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
import json

class FormulaError(ValueError):
    pass

@dataclass(frozen=True)
class FormulaDefinition:
    sport: str
    version: str
    coefficients: dict[str, float]
    verified: bool
    source: str | None
    retrieved_at: str | None

    def validate(self):
        if not self.verified:
            raise FormulaError(f"{self.sport}: FORMULA_UNVERIFIED")
        if not self.source:
            raise FormulaError(f"{self.sport}: FORMULA_SOURCE_MISSING")
        if not self.retrieved_at:
            raise FormulaError(f"{self.sport}: FORMULA_TIMESTAMP_MISSING")
        datetime.fromisoformat(self.retrieved_at.replace("Z", "+00:00"))
        if not self.coefficients:
            raise FormulaError(f"{self.sport}: COEFFICIENTS_MISSING")

    def score(self, row):
        self.validate()
        missing = [k for k in self.coefficients if k not in row]
        if missing:
            raise FormulaError(f"{self.sport}: MISSING_COMPONENT_STATS:{','.join(sorted(missing))}")
        return sum(float(row[k]) * w for k, w in self.coefficients.items())

class FormulaRegistry:
    def __init__(self, formulas):
        self.formulas = {k.upper(): v for k, v in formulas.items()}

    @classmethod
    def from_json(cls, path):
        raw = json.loads(Path(path).read_text())
        formulas = {}
        for sport, item in raw["sports"].items():
            formulas[sport.upper()] = FormulaDefinition(
                sport=sport.upper(),
                version=str(item["version"]),
                coefficients={k: float(v) for k, v in item["coefficients"].items()},
                verified=bool(item.get("verified", False)),
                source=item.get("source"),
                retrieved_at=item.get("retrieved_at"),
            )
        return cls(formulas)

    def get(self, sport):
        key = sport.upper()
        if key not in self.formulas:
            raise FormulaError(f"{key}: UNSUPPORTED_FANTASY_SCORE_SPORT")
        return self.formulas[key]
