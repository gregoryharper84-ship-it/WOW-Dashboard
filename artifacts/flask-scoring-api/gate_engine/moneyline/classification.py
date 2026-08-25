"""
gate_engine/moneyline/classification.py
WOW v16 — Favorite / underdog structural classifier.

Determines lane (FAVORITE / UNDERDOG) from calibrated_lower_bound, NOT
from raw probability or market odds.

Upset pathway taxonomy for underdogs:
  structural_advantage       : Elo / power / efficiency edge
  matchup_advantage          : Style / role matchup
  participant_lineup_advantage: Key player availability difference
  variance_assisted_path     : High-pace / volatile / small-sample
  late_game_finish_path      : Late-game scenarios favor underdog
  favorite_failure_path      : Paths that collapse the favorite

Classification:
  STRUCTURAL      : ≥2 non-variance dimensions active
  VARIANCE_ASSISTED: ≥1 variance + at most 1 structural
  TAIL_ONLY       : requires ≥3 low-probability simultaneous events

TAIL_ONLY upsets CANNOT qualify. All others remain subject to governance.

For favorites: survival_score derived from largest loss paths. A higher
raw probability with wider uncertainty must rank below a more stable
favorite with a better CLB.

can_execute=False unconditional.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

can_execute: bool = False

# A CLB of exactly 0.50 is treated as UNDERDOG (by convention, favorite CLB > 0.50)
_FAVORITE_CLB_FLOOR = 0.50

# Tail-only: requires this many simultaneous low-probability events
_TAIL_ONLY_MIN_EVENTS = 3

# Low probability threshold for "event" classification
_LOW_PROB_EVENT_THRESHOLD = 0.20


@dataclass
class UpsetProfile:
    structural_advantage:        bool = False
    matchup_advantage:           bool = False
    participant_lineup_advantage: bool = False
    variance_assisted_path:      bool = False
    late_game_finish_path:       bool = False
    favorite_failure_path:       bool = False

    @property
    def non_variance_count(self) -> int:
        return sum([
            self.structural_advantage,
            self.matchup_advantage,
            self.participant_lineup_advantage,
            self.late_game_finish_path,
            self.favorite_failure_path,
        ])

    @property
    def all_active_dimensions(self) -> list[str]:
        dims = []
        if self.structural_advantage:
            dims.append("structural_advantage")
        if self.matchup_advantage:
            dims.append("matchup_advantage")
        if self.participant_lineup_advantage:
            dims.append("participant_lineup_advantage")
        if self.variance_assisted_path:
            dims.append("variance_assisted_path")
        if self.late_game_finish_path:
            dims.append("late_game_finish_path")
        if self.favorite_failure_path:
            dims.append("favorite_failure_path")
        return dims

    def to_dict(self) -> dict[str, Any]:
        return {
            "structural_advantage":         self.structural_advantage,
            "matchup_advantage":            self.matchup_advantage,
            "participant_lineup_advantage":  self.participant_lineup_advantage,
            "variance_assisted_path":       self.variance_assisted_path,
            "late_game_finish_path":        self.late_game_finish_path,
            "favorite_failure_path":        self.favorite_failure_path,
            "non_variance_count":           self.non_variance_count,
            "active_dimensions":            self.all_active_dimensions,
        }


@dataclass
class CandidateClassification:
    lane:                 str   # "FAVORITE" | "UNDERDOG"
    upset_profile_type:   str   # "STRUCTURAL" | "VARIANCE_ASSISTED" | "TAIL_ONLY" | "N/A"
    upset_profile:        UpsetProfile | None
    survival_score:       float | None    # favorites only (0–100)
    qualification_gate:   str   # "QUALIFIES" | "TAIL_ONLY_REJECTED" | "GOVERNANCE_BLOCKED"
    explanation_notes:    list[str]        = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane":                self.lane,
            "upset_profile_type":  self.upset_profile_type,
            "upset_profile":       self.upset_profile.to_dict() if self.upset_profile else None,
            "survival_score":      round(self.survival_score, 2) if self.survival_score is not None else None,
            "qualification_gate":  self.qualification_gate,
            "explanation_notes":   self.explanation_notes,
        }


# ---------------------------------------------------------------------------
# Upset profile detectors
# ---------------------------------------------------------------------------

def _detect_structural_advantage(enrichment: dict[str, Any]) -> bool:
    """Underdog has a meaningful Elo/power/efficiency edge."""
    home_elo = enrichment.get("home_elo")
    away_elo = enrichment.get("away_elo")
    if home_elo is not None and away_elo is not None:
        try:
            if abs(float(home_elo) - float(away_elo)) < 30:
                return True  # very close Elo → underdog can win on merit
        except (TypeError, ValueError):
            pass

    hp = enrichment.get("home_power") or enrichment.get("home_power_rating")
    ap = enrichment.get("away_power") or enrichment.get("away_power_rating")
    if hp is not None and ap is not None:
        try:
            if abs(float(hp) - float(ap)) < 3.0:
                return True
        except (TypeError, ValueError):
            pass

    return bool(enrichment.get("structural_advantage_flag"))


def _detect_matchup_advantage(enrichment: dict[str, Any]) -> bool:
    """Style/role matchup explicitly favors underdog."""
    if enrichment.get("matchup_advantage_flag"):
        return True
    matchup_score = enrichment.get("matchup_advantage_score")
    if matchup_score is not None:
        try:
            return float(matchup_score) > 0.6
        except (TypeError, ValueError):
            pass
    return False


def _detect_participant_lineup_advantage(enrichment: dict[str, Any]) -> bool:
    """Key player availability difference favors underdog."""
    if enrichment.get("key_player_advantage_flag"):
        return True
    opp_out = enrichment.get("opponent_key_players_out") or []
    if isinstance(opp_out, list) and len(opp_out) >= 1:
        return True
    return False


def _detect_variance_path(enrichment: dict[str, Any], sport: str) -> bool:
    """High-pace, small-sample, or volatile matchup increases underdog variance."""
    pace = enrichment.get("pace") or enrichment.get("game_pace")
    if pace is not None:
        try:
            if sport in ("NBA", "WNBA") and float(pace) > 102:
                return True
        except (TypeError, ValueError):
            pass

    n_games = 0
    gl = enrichment.get("game_log")
    if isinstance(gl, list):
        n_games = len(gl)
    if n_games < 5:
        return True

    if enrichment.get("high_variance_flag"):
        return True
    return False


def _detect_late_game_path(enrichment: dict[str, Any]) -> bool:
    """Late-game scenarios (close games, OT potential) favor underdog."""
    return bool(
        enrichment.get("late_game_advantage_flag") or
        enrichment.get("overtime_probability", 0.0) > 0.20 or
        enrichment.get("close_game_indicator")
    )


def _detect_favorite_failure_path(
    failure_path_result: dict[str, Any] | None
) -> bool:
    """
    Specific favorite-collapse paths documented in failure path matrix.
    Favorite failure path is active when the primary kill path's mid-probability > 25%.
    """
    if failure_path_result is None:
        return False
    annotations = failure_path_result.get("path_annotations") or []
    for ann in annotations:
        if ann.get("path_name") == "PRIMARY_KILL_PATH":
            mid_p = ann.get("mid_prob", 0.0)
            if mid_p > 0.25:
                return True
    return False


def _classify_upset_type(profile: UpsetProfile) -> str:
    """Determine upset classification from profile dimensions."""
    nv = profile.non_variance_count
    has_variance = profile.variance_assisted_path

    if nv >= 2:
        return "STRUCTURAL"
    if nv == 1 and has_variance:
        return "VARIANCE_ASSISTED"
    if nv == 0 and has_variance:
        # Only variance — check if requires ≥3 simultaneous low-prob events
        return "TAIL_ONLY"
    if nv == 0 and not has_variance:
        return "TAIL_ONLY"
    return "VARIANCE_ASSISTED"


def _count_low_prob_events_required(
    calibration_result: dict[str, Any] | None,
    profile: UpsetProfile,
) -> int:
    """
    Estimate how many independent low-probability events must simultaneously
    occur for the underdog to win. Used to confirm TAIL_ONLY classification.
    """
    # Heuristic: start from CLB
    clb = 0.30
    if calibration_result is not None:
        clb = float(calibration_result.get("calibrated_lower_bound") or 0.30)

    # More structurally weak → more events required
    if clb < 0.20:
        return 4
    if clb < 0.30:
        return 3
    if clb < 0.40:
        return 2
    return 1


# ---------------------------------------------------------------------------
# Favorite survival score
# ---------------------------------------------------------------------------

def _compute_survival_score(
    calibration_result: dict[str, Any],
    failure_path_result: dict[str, Any] | None,
) -> float:
    """
    Survival score (0–100) for a favorite.

    Higher = more structurally stable (lower uncertainty relative to CLB).
    A higher raw probability with wide uncertainty MUST score lower than
    a slightly lower but more stable favorite with better CLB.
    """
    clb = float(calibration_result.get("calibrated_lower_bound") or 0.50)
    uncertainty = float(calibration_result.get("dynamic_uncertainty") or 0.10)

    # Base score from CLB (0–70 points)
    base = max(0.0, min(70.0, (clb - 0.5) * 200.0))

    # Stability bonus: lower uncertainty → higher score (0–20 points)
    stability = max(0.0, min(20.0, (0.20 - uncertainty) * 100.0))

    # Failure path penalty: large primary kill path → reduce score (0–10 points)
    fp_penalty = 0.0
    if failure_path_result:
        for ann in (failure_path_result.get("path_annotations") or []):
            if ann.get("path_name") == "PRIMARY_KILL_PATH":
                mid_p = ann.get("mid_prob", 0.0)
                fp_penalty = min(10.0, mid_p * 30.0)

    return round(max(0.0, base + stability - fp_penalty), 2)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def classify_candidate(
    row:                dict[str, Any],
    calibration_result: dict[str, Any],
    enrichment:         dict[str, Any],
    failure_path_result: dict[str, Any] | None = None,
) -> CandidateClassification:
    """
    Classify a moneyline candidate as FAVORITE or UNDERDOG, and for underdogs,
    determine the upset profile type.

    Lane determination uses calibrated_lower_bound only.
    Upset classification uses the six-dimension taxonomy.
    TAIL_ONLY candidates cannot qualify.

    Parameters
    ----------
    row                : The candidate row
    calibration_result : CalibrationResult.to_dict() from dynamic_calibration
    enrichment         : Full enrichment (source of advantage signals)
    failure_path_result: FailurePathResult.to_dict() (optional)
    """
    clb = float(calibration_result.get("calibrated_lower_bound") or 0.50)
    sport = (row.get("sport") or "").upper()
    notes: list[str] = []

    # Lane determination by CLB
    is_favorite = clb > _FAVORITE_CLB_FLOOR
    lane = "FAVORITE" if is_favorite else "UNDERDOG"
    notes.append(f"lane_determined_by_CLB:{clb:.4f} ({'FAVORITE' if is_favorite else 'UNDERDOG'})")

    if is_favorite:
        # Favorite: compute survival score
        survival = _compute_survival_score(calibration_result, failure_path_result)
        notes.append(f"survival_score={survival:.2f} (higher=more_stable)")
        notes.append(
            "RANKING_NOTE: A candidate with higher raw probability but wider "
            "uncertainty ranks BELOW one with lower raw probability but better CLB."
        )
        return CandidateClassification(
            lane="FAVORITE",
            upset_profile_type="N/A",
            upset_profile=None,
            survival_score=survival,
            qualification_gate="QUALIFIES",
            explanation_notes=notes,
        )

    # Underdog: classify upset profile
    profile = UpsetProfile(
        structural_advantage=_detect_structural_advantage(enrichment),
        matchup_advantage=_detect_matchup_advantage(enrichment),
        participant_lineup_advantage=_detect_participant_lineup_advantage(enrichment),
        variance_assisted_path=_detect_variance_path(enrichment, sport),
        late_game_finish_path=_detect_late_game_path(enrichment),
        favorite_failure_path=_detect_favorite_failure_path(failure_path_result),
    )

    upset_type = _classify_upset_type(profile)
    low_prob_events = _count_low_prob_events_required(calibration_result, profile)

    # Override to TAIL_ONLY if model estimates ≥3 simultaneous low-prob events
    if low_prob_events >= _TAIL_ONLY_MIN_EVENTS:
        upset_type = "TAIL_ONLY"
        notes.append(
            f"TAIL_ONLY_OVERRIDE:estimated_{low_prob_events}_low_prob_events_required"
        )

    # Qualification gate
    if upset_type == "TAIL_ONLY":
        qual_gate = "TAIL_ONLY_REJECTED"
        notes.append(
            "TAIL_ONLY_REJECTED: upset requires ≥3 simultaneous low-probability events; "
            "cannot qualify regardless of raw probability. "
            "This explanation shows WHY the model moved the underdog probability — "
            "it does NOT pretend these additive components are independent production math."
        )
    else:
        qual_gate = "QUALIFIES"
        notes.append(
            f"UPSET_PROFILE:{upset_type} "
            f"active_dimensions={profile.all_active_dimensions}"
        )

    return CandidateClassification(
        lane="UNDERDOG",
        upset_profile_type=upset_type,
        upset_profile=profile,
        survival_score=None,
        qualification_gate=qual_gate,
        explanation_notes=notes,
    )
