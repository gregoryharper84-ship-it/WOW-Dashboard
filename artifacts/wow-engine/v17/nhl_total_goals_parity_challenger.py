"""Research-only NHL final-score parity-distribution challenger.

The NHL official final-score outcome structure has non-Poisson odd/even mass.
Fits one odd/even share ONLY from a separate historical calibration cohort;
never uses a sportsbook line/price. This is not certified totals calibration.
"""
from __future__ import annotations
from math import exp, isfinite
from statistics import mean
from typing import Sequence
from scipy.stats import poisson
from v17.nhl_total_goals_challenger import NHLTotalsResearchError, TotalRow

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION = False


def fit_parity_calibration(rows: Sequence[TotalRow], *, season: int = 2023, min_rows: int = 120) -> dict:
    cohort = [r for r in rows if r.season_start_year == season]
    if len(cohort) < min_rows or len({r.event_id for r in cohort}) != len(cohort):
        raise NHLTotalsResearchError("NHL_TOTALS_PARITY_CALIBRATION_INSUFFICIENT", str(len(cohort)))
    odd_rate = mean(r.total_goals % 2 for r in cohort)
    if not 0 < odd_rate < 1:
        raise NHLTotalsResearchError("NHL_TOTALS_PARITY_CALIBRATION_DEGENERATE", str(odd_rate))
    return {"odd_total_share": odd_rate, "calibration_season": season,
            "calibration_n": len(cohort), "probability_publishable": False,
            "calibrated_lower_bound": None, "can_execute": False}


def parity_adjusted_research_outcomes(mean_goals: float, odd_share: float, *, line: float, side: str) -> dict:
    if (type(mean_goals) not in (float,int) or not isfinite(mean_goals) or not 0.1 <= mean_goals <= 30
        or type(odd_share) not in (float,int) or not isfinite(odd_share) or not 0 < odd_share < 1):
        raise NHLTotalsResearchError("NHL_TOTALS_PARITY_PARAMETERS_INVALID", "invalid mean/odd share")
    if type(line) not in (float,int) or not isfinite(line) or not 0 <= line <= 20.5 or 2*line != round(2*line):
        raise NHLTotalsResearchError("NHL_TOTALS_PARITY_LINE_UNSUPPORTED", str(line))
    if side not in ("UNDER", "OVER"):
        raise NHLTotalsResearchError("NHL_TOTALS_PARITY_DIRECTION_UNRESOLVED", str(side))
    exp_even = (1 + exp(-2*mean_goals)) / 2
    exp_odd = 1 - exp_even
    even_factor = (1-odd_share) / exp_even
    odd_factor = odd_share / exp_odd
    def pk(k: int) -> float:
        return float(poisson.pmf(k, mean_goals)) * (odd_factor if k%2 else even_factor)
    under = sum(pk(k) for k in range(int(line) + (0 if float(line).is_integer() else 1)))
    push = pk(int(line)) if float(line).is_integer() else 0.0
    over = 1 - under - push
    if not 0 <= under <= 1 or not 0 <= push <= 1 or over < -1e-10:
        raise NHLTotalsResearchError("NHL_TOTALS_PARITY_MASS_INVALID", str((under,push,over)))
    win, loss = (under, over) if side == "UNDER" else (over, under)
    return {"research_win_probability": win, "research_push_probability": push,
            "research_loss_probability": loss, "research_only": True,
            "calibrated_probability": None, "calibrated_lower_bound": None,
            "probability_publishable": False, "can_execute": False}
