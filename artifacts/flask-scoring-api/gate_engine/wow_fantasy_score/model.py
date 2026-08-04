from dataclasses import dataclass
from math import erf, sqrt
from statistics import mean, pstdev

from .formula import FormulaError

@dataclass(frozen=True)
class PropRequest:
    sport: str
    player: str
    line: float
    direction: str
    stat_rows: list[dict]
    minimum_games: int = 5
    calibration_buffer: float = 0.03
    small_sample_buffer: float = 0.02

@dataclass(frozen=True)
class ModelResult:
    terminal_label: str
    model_status: str
    player: str
    sport: str
    line: float
    direction: str
    raw_probability: float | None
    calibrated_probability: float | None
    calibrated_lower_bound: float | None
    opposite_probability: float | None
    mean_fantasy_score: float | None
    std_fantasy_score: float | None
    sample_size: int
    formula_version: str | None
    blockers: tuple[str, ...]

def normal_cdf(x, mu, sigma):
    return 0.5 * (1 + erf((x - mu) / (sigma * sqrt(2))))

class FantasyScoreModel:
    def __init__(self, registry):
        self.registry = registry

    def score(self, request):
        try:
            formula = self.registry.get(request.sport)
            formula.validate()
        except FormulaError as exc:
            return ModelResult("REJECT_DATA_QUALITY", "PROVISIONAL_FORMULA_BLOCKED",
                request.player, request.sport.upper(), request.line, request.direction,
                None, None, None, None, None, None, len(request.stat_rows), None, (str(exc),))

        if len(request.stat_rows) < request.minimum_games:
            return ModelResult("REJECT_DATA_QUALITY", "INSUFFICIENT_ROLE_MATCHED_SAMPLE",
                request.player, request.sport.upper(), request.line, request.direction,
                None, None, None, None, None, None, len(request.stat_rows), formula.version,
                ("FANTASY_SCORE_MINIMUM_SAMPLE_NOT_MET",))

        try:
            scores = [formula.score(row) for row in request.stat_rows]
        except FormulaError as exc:
            return ModelResult("REJECT_DATA_QUALITY", "COMPONENT_DATA_INVALID",
                request.player, request.sport.upper(), request.line, request.direction,
                None, None, None, None, None, None, len(request.stat_rows), formula.version, (str(exc),))

        mu, sigma = mean(scores), pstdev(scores)
        if sigma <= 1e-9:
            return ModelResult("REJECT_DATA_QUALITY", "DEGENERATE_DISTRIBUTION",
                request.player, request.sport.upper(), request.line, request.direction,
                None, None, None, None, mu, sigma, len(scores), formula.version,
                ("FANTASY_SCORE_ZERO_VARIANCE",))

        p_less = normal_cdf(request.line, mu, sigma)
        p_more = 1 - p_less
        raw = p_more if request.direction.upper() == "MORE" else p_less
        buffer = request.calibration_buffer + (request.small_sample_buffer if len(scores) < 10 else 0)
        calibrated = max(0.0, raw - buffer)
        lower = max(0.0, calibrated - 0.05)
        label = "MODEL_QUALIFIED_HOLD" if lower >= 0.65 else "REJECT_NO_EDGE"
        status = "PROVISIONAL_GAUSSIAN" if lower >= 0.65 else "PROVISIONAL_GAUSSIAN_BELOW_FLOOR"

        return ModelResult(label, status, request.player, request.sport.upper(), request.line,
            request.direction.upper(), raw, calibrated, lower, 1 - raw, mu, sigma, len(scores),
            formula.version, ("UNCALIBRATED_FANTASY_SCORE_COHORT", "POWER_INELIGIBLE", "can_execute=false"))
