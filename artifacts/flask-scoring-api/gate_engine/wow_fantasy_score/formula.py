from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os

class FormulaError(ValueError):
    pass

@dataclass(frozen=True)
class FormulaDefinition:
    sport: str
    version: str
    coefficients: dict[str, float]
    # Granular verification gates.
    # verified_formula  — the additive scoring components are confirmed from an authoritative source.
    # verified_settlement — edge cases (retirement, walkover, tiebreak, best-of-3 vs best-of-5
    #                       weighting) have been validated against settled results.
    # verified          — True only when BOTH flags are True (full confidence).
    verified_formula:     bool
    verified_settlement:  bool
    verified:             bool        # = verified_formula and verified_settlement
    source: str | None
    retrieved_at: str | None

    def validate(self):
        """
        Gate for scoring: blocks unless the formula components are confirmed.

        verified_formula=True is sufficient to run Gaussian scoring — it means
        the coefficient table came from an authoritative source.  verified_settlement
        is not required here; its False state is surfaced in calibration_note so
        downstream consumers can treat the probability with appropriate caution.
        """
        if not self.verified_formula:
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
    def __init__(self, formulas, *, file_hash=None, file_version=None,
                 loaded_at=None, file_mtime=None, file_path=None):
        self.formulas = {k.upper(): v for k, v in formulas.items()}
        # Provenance — stamped on every FS prediction record so calibration back-tests
        # know which formula version produced each historical prediction.
        self.file_hash    = file_hash       # sha256[:16] of JSON content
        self.file_version = file_version    # schema_version from JSON
        self.loaded_at    = loaded_at       # ISO timestamp when loaded
        self.file_mtime   = file_mtime      # os.path.getmtime() at load time
        self.file_path    = file_path       # absolute path to the JSON file

    @classmethod
    def from_json(cls, path):
        path_str = str(path)
        content  = Path(path).read_text()
        raw      = json.loads(content)

        file_hash    = hashlib.sha256(content.encode()).hexdigest()[:16]
        file_mtime   = os.path.getmtime(path_str)
        loaded_at    = datetime.now(timezone.utc).isoformat()
        file_version = str(raw.get("schema_version", "unknown"))

        formulas = {}
        for sport, item in raw["sports"].items():
            # Prefer granular verified_formula/verified_settlement fields;
            # fall back to the legacy boolean `verified` for backward compat.
            legacy_v = bool(item.get("verified", False))
            vf = bool(item.get("verified_formula", legacy_v))
            vs = bool(item.get("verified_settlement", legacy_v))
            formulas[sport.upper()] = FormulaDefinition(
                sport               = sport.upper(),
                version             = str(item["version"]),
                coefficients        = {k: float(v) for k, v in item["coefficients"].items()},
                verified_formula    = vf,
                verified_settlement = vs,
                verified            = vf and vs,
                source              = item.get("source"),
                retrieved_at        = item.get("retrieved_at"),
            )
        return cls(
            formulas,
            file_hash    = file_hash,
            file_version = file_version,
            loaded_at    = loaded_at,
            file_mtime   = file_mtime,
            file_path    = path_str,
        )

    def get(self, sport):
        key = sport.upper()
        if key not in self.formulas:
            raise FormulaError(f"{key}: UNSUPPORTED_FANTASY_SCORE_SPORT")
        return self.formulas[key]
