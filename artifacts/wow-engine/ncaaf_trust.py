"""
NCAAF trust layer for WOW v16 Clean Core.

This module is deliberately NOT a replacement for an NCAAF fitted game-win
model. It owns the NCAAF-specific evidence contract, hard quarterback gate,
failure-regime reconciliation, CLV grading, and forward trust-state
progression. A real controlling NCAAF game-win model and eligible calibrator
remain mandatory before governed probabilities can publish.

Governance invariant:
    can_execute = False
    DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from math import isfinite, log
from typing import Mapping, Optional, Sequence

CAN_EXECUTE = False
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True


class NCAAFTrustState(str, Enum):
    TEST_ONLY = "NCAAF_TEST_ONLY"
    WATCH = "NCAAF_WATCH"
    PRIMARY_CANDIDATE = "NCAAF_PRIMARY_CANDIDATE"
    TRUSTED = "NCAAF_TRUSTED"
    SCALE_ELIGIBLE = "NCAAF_SCALE_ELIGIBLE"


class CLVGrade(str, Enum):
    BEAT_CLOSE = "BEAT_CLOSE"
    CLOSED_SAME = "CLOSED_SAME"
    LOST_TO_CLOSE = "LOST_TO_CLOSE"
    NO_CLOSE_AVAILABLE = "NO_CLOSE_AVAILABLE"


class MarketRole(str, Enum):
    FAVORITE = "FAVORITE"
    UNDERDOG = "UNDERDOG"
    CONFLICT = "CONFLICT"
    EVEN = "EVEN"


REQUIRED_FAILURE_REGIMES = (
    "BASE_SCRIPT",
    "QB_UNDERPERFORMANCE_OR_BACKUP",
    "TURNOVER_NEGATIVE_GAME",
    "EXPLOSIVE_PLAY_ALLOWED",
    "WEATHER_OR_LOW_POSSESSION_VARIANCE",
    "SPECIAL_TEAMS_OR_FIELD_POSITION_SWING",
)

# These are status *evidence* values, not terminal labels. A merely expected
# starter is intentionally not enough to clear the hard NCAAF QB gate.
_QB_CONFIRMED = {
    "CONFIRMED",
    "CONFIRMED_STARTER",
    "BACKUP_CONFIRMED",
}


@dataclass(frozen=True)
class NCAAFEventEvidence:
    official_event_id: str
    event_date: str
    scheduled_start_utc: str
    venue: str
    neutral_site: bool
    home_away: str
    team: str
    opponent: str

    starting_qb_status: str
    backup_qb_downgrade_value: Optional[float]
    offensive_line_injury_status: str
    defensive_front_pass_rush_health: str
    top_wr_rb_availability: str
    travel_rest_spot: str
    weather_summary: str
    wind_mph: Optional[float]

    market_role: str
    selection_price_american: int
    opposing_price_american: int
    market_timestamp: str
    no_vig_probability: float
    model_timestamp: str
    source_snapshot_id: str

    conference_tier: str
    fbs_vs_fcs: str
    qb_certainty: float
    depth_chart_certainty: float
    injury_reporting_quality: float
    market_liquidity: float
    weather_variance: float
    team_tempo: float
    turnover_volatility: float
    special_teams_volatility: float
    model_disagreement: float

    blockers: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FailureRegime:
    name: str
    probability: float
    win_probability_given_regime: float


@dataclass(frozen=True)
class FailurePathResult:
    unconditional_probability: float
    failure_path_score: float
    largest_failure_path: str
    largest_failure_contribution: float
    regime_probability_sum: float


@dataclass(frozen=True)
class TrustAssessment:
    state: NCAAFTrustState
    publication_ceiling: str
    blockers: tuple[str, ...]
    settled_candidates: int
    ncaaf_moneyline_bucket_candidates: int
    clv_positive_rate: Optional[float]
    roi: Optional[float]
    review_25_passed: bool
    confirmation_50_passed: bool


def _parse_aware_timestamp(value: str) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _probability(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)) and 0 < float(value) < 1


def american_to_implied_probability(price: int) -> float:
    if isinstance(price, bool) or not isinstance(price, int) or price == 0:
        raise ValueError("American price must be a non-zero integer")
    if price < 0:
        return abs(price) / (abs(price) + 100.0)
    return 100.0 / (price + 100.0)


def two_way_no_vig(selection_price_american: int, opposing_price_american: int) -> tuple[float, float, float]:
    """Return (selection no-vig, opposing no-vig, raw overround)."""
    q_sel = american_to_implied_probability(selection_price_american)
    q_opp = american_to_implied_probability(opposing_price_american)
    denom = q_sel + q_opp
    if denom <= 0:
        raise ValueError("Invalid two-way market")
    return q_sel / denom, q_opp / denom, denom - 1.0


def validate_ncaaf_evidence(
    evidence: NCAAFEventEvidence,
    *,
    as_of: Optional[datetime] = None,
    market_max_age_minutes: float = 10.0,
    no_vig_tolerance: float = 0.01,
) -> list[str]:
    """Validate the explicit NCAAF acquisition contract.

    This is an evidence/readiness gate only. It never produces a model
    probability. Any returned blocker survives downstream processing.
    """
    blockers = list(evidence.blockers)
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)

    required_text = {
        "official_event_id": evidence.official_event_id,
        "event_date": evidence.event_date,
        "scheduled_start_utc": evidence.scheduled_start_utc,
        "venue": evidence.venue,
        "home_away": evidence.home_away,
        "team": evidence.team,
        "opponent": evidence.opponent,
        "starting_qb_status": evidence.starting_qb_status,
        "offensive_line_injury_status": evidence.offensive_line_injury_status,
        "defensive_front_pass_rush_health": evidence.defensive_front_pass_rush_health,
        "top_wr_rb_availability": evidence.top_wr_rb_availability,
        "travel_rest_spot": evidence.travel_rest_spot,
        "weather_summary": evidence.weather_summary,
        "market_role": evidence.market_role,
        "market_timestamp": evidence.market_timestamp,
        "model_timestamp": evidence.model_timestamp,
        "source_snapshot_id": evidence.source_snapshot_id,
        "conference_tier": evidence.conference_tier,
        "fbs_vs_fcs": evidence.fbs_vs_fcs,
    }
    for name, value in required_text.items():
        if not isinstance(value, str) or not value.strip():
            blockers.append(f"NCAAF_REQUIRED_FIELD_MISSING:{name}")

    if evidence.home_away not in {"HOME", "AWAY", "NEUTRAL"}:
        blockers.append("NCAAF_HOME_AWAY_INVALID")
    if evidence.neutral_site and evidence.home_away != "NEUTRAL":
        blockers.append("NCAAF_NEUTRAL_SITE_CONFLICT")
    if evidence.market_role not in {r.value for r in MarketRole}:
        blockers.append("NCAAF_MARKET_ROLE_INVALID")
    if evidence.market_role == MarketRole.CONFLICT.value:
        blockers.append("FAVORITE_STATUS_CONFLICT")

    start = _parse_aware_timestamp(evidence.scheduled_start_utc)
    if start is None:
        blockers.append("NCAAF_START_TIME_INVALID")
    elif start <= now:
        blockers.append("EVENT_ALREADY_STARTED")

    market_ts = _parse_aware_timestamp(evidence.market_timestamp)
    if market_ts is None:
        blockers.append("NCAAF_MARKET_TIMESTAMP_INVALID")
    else:
        age_min = (now - market_ts).total_seconds() / 60.0
        if age_min < -1.0:
            blockers.append("NCAAF_MARKET_TIMESTAMP_IN_FUTURE")
        elif age_min > market_max_age_minutes:
            blockers.append("NCAAF_MARKET_PRICE_STALE")

    model_ts = _parse_aware_timestamp(evidence.model_timestamp)
    if model_ts is None:
        blockers.append("NCAAF_MODEL_TIMESTAMP_INVALID")
    elif model_ts > now:
        blockers.append("NCAAF_MODEL_TIMESTAMP_IN_FUTURE")

    if evidence.starting_qb_status not in _QB_CONFIRMED:
        blockers.append("NCAAF_QB_STATUS_UNCONFIRMED")
    if evidence.starting_qb_status == "BACKUP_CONFIRMED" and evidence.backup_qb_downgrade_value is None:
        blockers.append("NCAAF_BACKUP_QB_DOWNGRADE_UNRESOLVED")
    if evidence.backup_qb_downgrade_value is not None and not isfinite(float(evidence.backup_qb_downgrade_value)):
        blockers.append("NCAAF_BACKUP_QB_DOWNGRADE_INVALID")

    uncertainty_fields = {
        "qb_certainty": evidence.qb_certainty,
        "depth_chart_certainty": evidence.depth_chart_certainty,
        "injury_reporting_quality": evidence.injury_reporting_quality,
        "market_liquidity": evidence.market_liquidity,
        "weather_variance": evidence.weather_variance,
        "team_tempo": evidence.team_tempo,
        "turnover_volatility": evidence.turnover_volatility,
        "special_teams_volatility": evidence.special_teams_volatility,
        "model_disagreement": evidence.model_disagreement,
    }
    for name, value in uncertainty_fields.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(float(value)) or not (0.0 <= float(value) <= 1.0):
            blockers.append(f"NCAAF_CALIBRATION_INPUT_INVALID:{name}")

    if not _probability(evidence.no_vig_probability):
        blockers.append("NCAAF_NO_VIG_PROBABILITY_INVALID")
    else:
        try:
            computed, _, _ = two_way_no_vig(evidence.selection_price_american, evidence.opposing_price_american)
        except ValueError:
            blockers.append("NCAAF_TWO_WAY_ML_PRICE_INVALID")
        else:
            if abs(computed - float(evidence.no_vig_probability)) > no_vig_tolerance:
                blockers.append("NCAAF_NO_VIG_PRICE_MISMATCH")

    # Stable de-duplication preserves first-seen blocker ordering.
    return list(dict.fromkeys(blockers))


def ncaaf_dynamic_calibration_components(evidence: NCAAFEventEvidence) -> dict[str, object]:
    """Return NCAAF-specific uncertainty inputs for the existing dynamic calibrator.

    No universal fixed haircut is applied here. The active calibrator owns
    mapping these components to candidate-specific bounds.
    """
    return {
        "conference_tier": evidence.conference_tier,
        "fbs_vs_fcs": evidence.fbs_vs_fcs,
        "qb_certainty": evidence.qb_certainty,
        "depth_chart_certainty": evidence.depth_chart_certainty,
        "injury_reporting_quality": evidence.injury_reporting_quality,
        "market_liquidity": evidence.market_liquidity,
        "weather_variance": evidence.weather_variance,
        "team_tempo": evidence.team_tempo,
        "turnover_volatility": evidence.turnover_volatility,
        "special_teams_volatility": evidence.special_teams_volatility,
        "model_disagreement": evidence.model_disagreement,
    }


def reconcile_failure_regimes(regimes: Sequence[FailureRegime], *, tolerance: float = 1e-6) -> FailurePathResult:
    """Compute unconditional NCAAF win probability from quantified regimes.

    The six required top-level regimes are treated as a mutually exclusive,
    collectively exhaustive scenario partition. Correlated causes must be
    resolved when constructing the regimes; callers may not double-count
    overlapping probability mass here.
    """
    names = [r.name for r in regimes]
    missing = [name for name in REQUIRED_FAILURE_REGIMES if name not in names]
    extra = [name for name in names if name not in REQUIRED_FAILURE_REGIMES]
    if missing:
        raise ValueError(f"Missing required NCAAF failure regimes: {missing}")
    if extra:
        raise ValueError(f"Unsupported top-level NCAAF failure regimes: {extra}")
    if len(names) != len(set(names)):
        raise ValueError("Duplicate NCAAF failure regime")

    mass = 0.0
    unconditional = 0.0
    loss_contributions: dict[str, float] = {}
    for regime in regimes:
        if not isinstance(regime.probability, (int, float)) or isinstance(regime.probability, bool) or not isfinite(float(regime.probability)) or not (0.0 <= float(regime.probability) <= 1.0):
            raise ValueError(f"Invalid regime probability for {regime.name}")
        if not _probability(regime.win_probability_given_regime):
            raise ValueError(f"Invalid conditional win probability for {regime.name}")
        p_regime = float(regime.probability)
        p_win = float(regime.win_probability_given_regime)
        mass += p_regime
        unconditional += p_regime * p_win
        if regime.name != "BASE_SCRIPT":
            loss_contributions[regime.name] = p_regime * (1.0 - p_win)

    if abs(mass - 1.0) > tolerance:
        raise ValueError(f"NCAAF failure regime probability mass must sum to 1; got {mass:.9f}")
    if not _probability(unconditional):
        raise ValueError("Unconditional NCAAF probability is outside (0,1)")

    largest_name, largest_contribution = max(loss_contributions.items(), key=lambda item: item[1])
    return FailurePathResult(
        unconditional_probability=unconditional,
        failure_path_score=sum(loss_contributions.values()),
        largest_failure_path=largest_name,
        largest_failure_contribution=largest_contribution,
        regime_probability_sum=mass,
    )


def grade_clv(entry_no_vig: Optional[float], closing_no_vig: Optional[float], *, same_tolerance: float = 1e-9) -> CLVGrade:
    """Grade selection-side probability CLV.

    Positive CLV means the selection's vig-free market probability increased
    from the governed pregame snapshot to the governed closing snapshot.
    """
    if entry_no_vig is None or closing_no_vig is None:
        return CLVGrade.NO_CLOSE_AVAILABLE
    if not _probability(entry_no_vig) or not _probability(closing_no_vig):
        raise ValueError("CLV probabilities must satisfy 0<p<1")
    delta = float(closing_no_vig) - float(entry_no_vig)
    if abs(delta) <= same_tolerance:
        return CLVGrade.CLOSED_SAME
    return CLVGrade.BEAT_CLOSE if delta > 0 else CLVGrade.LOST_TO_CLOSE


def calibration_metrics(probability: float, won: bool) -> tuple[float, float]:
    """Return (Brier score, log loss) for one settled binary ML row."""
    if not _probability(probability):
        raise ValueError("Probability must satisfy 0<p<1")
    y = 1.0 if won else 0.0
    p = float(probability)
    brier = (p - y) ** 2
    log_loss = -(y * log(p) + (1.0 - y) * log(1.0 - p))
    return brier, log_loss


def assess_ncaaf_trust(
    *,
    settled_candidates: int,
    ncaaf_moneyline_bucket_candidates: int,
    review_25_passed: bool,
    confirmation_50_passed: bool,
    clv_positive_rate: Optional[float],
    roi: Optional[float],
    repeating_failure_tag: bool,
    active_banned_failure_pattern: bool,
    market_role: str,
) -> TrustAssessment:
    """Resolve the monotonic NCAAF trust state.

    The user's 20-row moneyline-bucket TRUSTED test is nested *inside* the
    50-row overall confirmation requirement. This prevents the 20-row bucket
    rule from bypassing the explicit instruction that NCAAF stay TEST_ONLY /
    WATCH until both the 25-row review and 50-row confirmation have passed.
    """
    blockers: list[str] = []
    if settled_candidates < 0 or ncaaf_moneyline_bucket_candidates < 0:
        raise ValueError("Candidate counts cannot be negative")
    if clv_positive_rate is not None and not (0.0 <= clv_positive_rate <= 1.0):
        raise ValueError("clv_positive_rate must be in [0,1]")
    if roi is not None and not isfinite(float(roi)):
        raise ValueError("roi must be finite when supplied")

    if settled_candidates < 25:
        state = NCAAFTrustState.TEST_ONLY
        blockers.append("NCAAF_25_SETTLED_REVIEW_NOT_REACHED")
    elif not review_25_passed:
        state = NCAAFTrustState.TEST_ONLY
        blockers.append("NCAAF_25_ROW_CALIBRATION_REVIEW_NOT_PASSED")
    elif settled_candidates < 50 or not confirmation_50_passed:
        state = NCAAFTrustState.WATCH
        blockers.append("NCAAF_50_ROW_CONFIRMATION_NOT_PASSED")
    else:
        if clv_positive_rate is None:
            state = NCAAFTrustState.WATCH
            blockers.append("NCAAF_CLV_RATE_UNAVAILABLE")
        elif roi is None:
            state = NCAAFTrustState.WATCH
            blockers.append("NCAAF_ROI_UNAVAILABLE")
        elif clv_positive_rate < 0.55 or roi <= 0 or repeating_failure_tag:
            state = NCAAFTrustState.WATCH
            if clv_positive_rate < 0.55:
                blockers.append("NCAAF_CLV_PRIMARY_THRESHOLD_NOT_MET")
            if roi <= 0:
                blockers.append("NCAAF_POSITIVE_ROI_NOT_MET")
            if repeating_failure_tag:
                blockers.append("NCAAF_REPEATING_FAILURE_TAG_ACTIVE")
        else:
            state = NCAAFTrustState.PRIMARY_CANDIDATE

            trusted = (
                ncaaf_moneyline_bucket_candidates >= 20
                and clv_positive_rate >= 0.60
                and roi > 0
                and not repeating_failure_tag
            )
            if trusted:
                state = NCAAFTrustState.TRUSTED

            scale = (
                trusted
                and settled_candidates >= 100
                and clv_positive_rate >= 0.60
                and roi > 0
                and not active_banned_failure_pattern
            )
            if scale:
                state = NCAAFTrustState.SCALE_ELIGIBLE
            elif active_banned_failure_pattern:
                blockers.append("NCAAF_ACTIVE_BANNED_FAILURE_PATTERN")

    # Native WOW terminal labels only. The trust state is metadata; this
    # ceiling can only reduce, never upgrade, the controlling Full Model.
    if state == NCAAFTrustState.TEST_ONLY:
        ceiling = "RESEARCH_INTEREST"
    elif state == NCAAFTrustState.WATCH:
        ceiling = "UPSET_WATCH" if market_role == MarketRole.UNDERDOG.value else "WINNER_WATCH"
    elif state == NCAAFTrustState.PRIMARY_CANDIDATE:
        ceiling = "MODEL_QUALIFIED_HOLD"
    else:
        ceiling = "NO_ADDITIONAL_NCAAF_TRUST_CEILING"

    return TrustAssessment(
        state=state,
        publication_ceiling=ceiling,
        blockers=tuple(dict.fromkeys(blockers)),
        settled_candidates=settled_candidates,
        ncaaf_moneyline_bucket_candidates=ncaaf_moneyline_bucket_candidates,
        clv_positive_rate=clv_positive_rate,
        roi=roi,
        review_25_passed=review_25_passed,
        confirmation_50_passed=confirmation_50_passed,
    )


def apply_qb_ceiling(existing_ceiling: str, blockers: Sequence[str], market_role: str) -> str:
    """Apply the hard NCAAF QB/depth-chart ceiling without inventing labels."""
    if "NCAAF_QB_STATUS_UNCONFIRMED" not in blockers:
        return existing_ceiling
    return "UPSET_WATCH" if market_role == MarketRole.UNDERDOG.value else "WINNER_WATCH"
