"""
gate_engine/moneyline/teamrankings_adapter.py
WOW v16 — WOW-PATCH-2026-08-08-TEAMRANKINGS-SECONDARY-ENRICHMENT

Source role:  SECONDARY_ENRICHMENT / PUBLIC_PREDICTIVE_MODEL
Sports:       NBA, WNBA, MLB, NFL, NCAAF, NCAAB

Access model:
  TeamRankings.com has no authorized public API available to this application.
  All TR data MUST be supplied by the GPT operator in enrichment["teamrankings"]
  (the operator reads TeamRankings and provides the data in the scoring request).
  If absent → DATA_UNOBTAINABLE.  The base model is completely unaffected.

Weight governance (hard rules):
  - Default TR contribution to sport model ensemble: 7.5%
  - Hard ceiling on TR weight: 10%
  - Weight = 0.0 when: STALE | DATA_UNOBTAINABLE | PROXY_ONLY | SOURCE_CONFLICT
  - TR matchup_win_prob_home required for any non-zero weight.
    Raw predictive ratings CANNOT be converted to a win probability here —
    no calibrated logistic mapping exists per WOW governance.
  - TR display_odds (American moneyline) = market context ONLY.
    They are stored in the record but NEVER passed to extract_no_vig_probability()
    and NEVER included in the market prior computation.
  - Market prior weight cap is independent and unchanged.

Contradiction logic:
  - Fires when TR matchup_win_prob_home differs from core model by > 8 pp
  - OPPOSITE_SIDE: TR favors the opposite winner → contradiction_flag=True
  - DISCREPANCY: same-direction diff > 8 pp → contradiction_flag=True
  - Both route through the disagreement audit before final qualification
  - TR contradiction LOWERS confidence; it NEVER flips the pick

can_execute=False unconditional.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

can_execute: bool = False  # UNCONDITIONAL

# ---------------------------------------------------------------------------
# Supported sports
# ---------------------------------------------------------------------------

TEAMRANKINGS_SUPPORTED_SPORTS: frozenset[str] = frozenset({
    "NBA", "WNBA", "MLB", "NFL", "NCAAF", "NCAAB",
})

# ---------------------------------------------------------------------------
# Weight governance constants
# ---------------------------------------------------------------------------

TR_WEIGHT_DEFAULT: float = 0.075   # 7.5% of sport model ensemble
TR_WEIGHT_MAX:     float = 0.10    # 10% hard ceiling
TR_WEIGHT_ZERO:    float = 0.0

# Contradiction thresholds (absolute probability difference, home-team perspective)
TR_CONTRADICTION_THRESHOLD_PP: float = 0.08   # 8 pp → material discrepancy
TR_OPPOSITE_SIDE_BOUNDARY:     float = 0.50   # TR favors opposite winner boundary

# Staleness: TR data older than this → zero weight
TR_STALE_THRESHOLD_HOURS: float = 4.0


# ---------------------------------------------------------------------------
# Acquisition status vocabulary
# ---------------------------------------------------------------------------

class TeamRankingsStatus:
    RETRIEVED         = "RETRIEVED"
    PROXY_ONLY        = "PROXY_ONLY"
    DATA_UNOBTAINABLE = "DATA_UNOBTAINABLE"
    SOURCE_CONFLICT   = "SOURCE_CONFLICT"
    STALE             = "STALE"
    NOT_ATTEMPTED     = "NOT_ATTEMPTED"
    UNSUPPORTED_SPORT = "UNSUPPORTED_SPORT"

    _ALL: frozenset[str] = frozenset({
        "RETRIEVED", "PROXY_ONLY", "DATA_UNOBTAINABLE",
        "SOURCE_CONFLICT", "STALE", "NOT_ATTEMPTED", "UNSUPPORTED_SPORT",
    })


# ---------------------------------------------------------------------------
# TeamRankings per-team record
# ---------------------------------------------------------------------------

@dataclass
class TeamRankingsTeamRecord:
    """
    All TeamRankings fields for one team in a matchup.

    Every field is Optional[...] and defaults to None — never fabricated.
    display_odds is market context ONLY and is excluded from the sport model.
    """
    # Identity
    team_name:                    str   | None = None
    league:                       str   | None = None
    sport:                        str   | None = None

    # Core predictive ratings
    predictive_rating:            float | None = None
    predictive_rank:              int   | None = None
    home_rating:                  float | None = None
    home_rank:                    int   | None = None
    away_rating:                  float | None = None
    away_rank:                    int   | None = None
    neutral_rating:               float | None = None   # neutral-site games

    # Schedule quality
    strength_of_schedule:         float | None = None   # season SOS
    future_strength_of_schedule:  float | None = None   # remaining SOS

    # Recent form
    last_5_rating:                float | None = None
    last_10_rating:               float | None = None
    consistency_rating:           float | None = None

    # Opponent-tier performance
    vs_top_25_pct:                float | None = None   # win% vs top-25
    vs_bottom_25_pct:             float | None = None

    # Season projections
    projected_win_pct:            float | None = None
    projected_playoff_prob:       float | None = None

    # Market context — display only; NEVER fed to sport model
    display_odds:                 int   | None = None

    # Source metadata
    source_url:                   str   | None = None
    retrieved_at:                 str   | None = None   # ISO 8601
    freshness_age_hours:          float | None = None
    source_status:                str           = TeamRankingsStatus.NOT_ATTEMPTED
    acquisition_notes:            list[str]     = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_name":                   self.team_name,
            "league":                      self.league,
            "sport":                       self.sport,
            "predictive_rating":           self.predictive_rating,
            "predictive_rank":             self.predictive_rank,
            "home_rating":                 self.home_rating,
            "home_rank":                   self.home_rank,
            "away_rating":                 self.away_rating,
            "away_rank":                   self.away_rank,
            "neutral_rating":              self.neutral_rating,
            "strength_of_schedule":        self.strength_of_schedule,
            "future_strength_of_schedule": self.future_strength_of_schedule,
            "last_5_rating":               self.last_5_rating,
            "last_10_rating":              self.last_10_rating,
            "consistency_rating":          self.consistency_rating,
            "vs_top_25_pct":               self.vs_top_25_pct,
            "vs_bottom_25_pct":            self.vs_bottom_25_pct,
            "projected_win_pct":           self.projected_win_pct,
            "projected_playoff_prob":      self.projected_playoff_prob,
            # display_odds kept for full record transparency, marked market-only
            "display_odds":                self.display_odds,
            "display_odds_note":           "MARKET_CONTEXT_ONLY:never_fed_to_sport_model",
            "source_url":                  self.source_url,
            "retrieved_at":                self.retrieved_at,
            "freshness_age_hours":         self.freshness_age_hours,
            "source_status":               self.source_status,
            "acquisition_notes":           self.acquisition_notes,
        }


# ---------------------------------------------------------------------------
# Full matchup enrichment (both teams + matchup projection + contradiction)
# ---------------------------------------------------------------------------

@dataclass
class TeamRankingsMatchupEnrichment:
    """
    TeamRankings enrichment for a complete game matchup.

    home_record / away_record: per-team records.
    matchup_win_prob_home: TR's direct matchup win projection (home-team perspective).
        Used only when source_status=RETRIEVED and not stale.
    Contradiction fields:
        teamrankings_model_agreement, teamrankings_model_delta,
        teamrankings_contradiction_flag, teamrankings_contradiction_reason.
    effective_weight: governance-controlled; 0.0 when unavailable/stale/conflicting.
    display_odds_excluded_from_model: always True.
    """
    sport:                             str   | None = None
    home_record:                       TeamRankingsTeamRecord = field(
        default_factory=TeamRankingsTeamRecord)
    away_record:                       TeamRankingsTeamRecord = field(
        default_factory=TeamRankingsTeamRecord)

    # TR's direct matchup projection (NOT derived from raw ratings)
    matchup_win_prob_home:             float | None = None
    matchup_projection_source:         str           = "absent"

    # Contradiction analysis (all four required output fields)
    teamrankings_model_agreement:      str   | None  = None
    teamrankings_model_delta:          float | None  = None
    teamrankings_contradiction_flag:   bool           = False
    teamrankings_contradiction_reason: str   | None  = None

    # Weight governance
    effective_weight:                  float          = 0.0
    weight_reason:                     str   | None  = None

    # Acquisition audit
    source_status:                     str            = TeamRankingsStatus.NOT_ATTEMPTED
    display_odds_excluded_from_model:  bool           = True   # ALWAYS True
    acquisition_notes:                 list[str]      = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sport":                              self.sport,
            "home_record":                        self.home_record.to_dict(),
            "away_record":                        self.away_record.to_dict(),
            "matchup_win_prob_home":              self.matchup_win_prob_home,
            "matchup_projection_source":          self.matchup_projection_source,
            "teamrankings_model_agreement":       self.teamrankings_model_agreement,
            "teamrankings_model_delta":           (
                round(self.teamrankings_model_delta, 4)
                if self.teamrankings_model_delta is not None else None),
            "teamrankings_contradiction_flag":    self.teamrankings_contradiction_flag,
            "teamrankings_contradiction_reason":  self.teamrankings_contradiction_reason,
            "effective_weight":                   round(self.effective_weight, 4),
            "weight_reason":                      self.weight_reason,
            "source_status":                      self.source_status,
            "display_odds_excluded_from_model":   self.display_odds_excluded_from_model,
            "acquisition_notes":                  self.acquisition_notes,
        }

    def fill_contradiction(self, core_independent_prob_home: float | None) -> None:
        """
        Compute contradiction fields in-place using the available core model probability.
        Called after stage 6 (candidate-side extraction) in the pipeline.
        """
        ag, delta, flag, reason = _compute_contradiction(
            self.matchup_win_prob_home, core_independent_prob_home
        )
        self.teamrankings_model_agreement     = ag
        self.teamrankings_model_delta         = delta
        self.teamrankings_contradiction_flag  = flag
        self.teamrankings_contradiction_reason = reason


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_stale(record: TeamRankingsTeamRecord) -> bool:
    """Return True when the record is older than TR_STALE_THRESHOLD_HOURS."""
    hours = record.freshness_age_hours
    if hours is None:
        if record.retrieved_at:
            try:
                ts = datetime.fromisoformat(
                    record.retrieved_at.replace("Z", "+00:00"))
                hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
            except (ValueError, TypeError):
                return False
        else:
            return False
    try:
        return float(hours) > TR_STALE_THRESHOLD_HOURS
    except (TypeError, ValueError):
        return False


def _is_stale_dict(data: dict[str, Any]) -> bool:
    """
    Staleness check for the top-level enrichment["teamrankings"] dict.

    Evaluates top-level freshness_age_hours and retrieved_at using the same
    logic as _is_stale().  Called in addition to per-team checks so that
    minimal-submission payloads (team records without per-team timestamps) are
    still correctly rejected when the top-level timestamp is stale.

    Returns False (not stale) when no timestamp information is present at the
    top level — the per-team checks are the primary gate in that case.
    """
    hours_raw = data.get("freshness_age_hours")
    if hours_raw is not None:
        try:
            return float(hours_raw) > TR_STALE_THRESHOLD_HOURS
        except (TypeError, ValueError):
            pass  # fall through to retrieved_at

    retrieved_at_raw = data.get("retrieved_at")
    if retrieved_at_raw:
        try:
            ts = datetime.fromisoformat(
                str(retrieved_at_raw).replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
            return age_hours > TR_STALE_THRESHOLD_HOURS
        except (ValueError, TypeError):
            pass

    return False  # no top-level timestamp — do not stale on absence alone


def _extract_team_record(data: dict[str, Any], role: str) -> TeamRankingsTeamRecord:
    """
    Parse one team's TR data from a dict.
    role = "home" | "away" (used for fallback aliasing).
    """
    if not isinstance(data, dict):
        return TeamRankingsTeamRecord(
            source_status=TeamRankingsStatus.DATA_UNOBTAINABLE,
            acquisition_notes=[f"expected dict for {role} team record; "
                                f"got {type(data).__name__}"],
        )

    def _f(key: str) -> float | None:
        v = data.get(key) or data.get(f"{role}_{key}")
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _i(key: str) -> int | None:
        v = data.get(key) or data.get(f"{role}_{key}")
        if v is None:
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    def _s(key: str) -> str | None:
        v = data.get(key) or data.get(f"{role}_{key}")
        return str(v).strip() if v is not None else None

    status_raw = str(data.get("source_status") or TeamRankingsStatus.RETRIEVED).upper()
    status = status_raw if status_raw in TeamRankingsStatus._ALL else TeamRankingsStatus.RETRIEVED

    return TeamRankingsTeamRecord(
        team_name                   = _s("team_name") or _s("team"),
        league                      = _s("league"),
        sport                       = _s("sport"),
        predictive_rating           = _f("predictive_rating"),
        predictive_rank             = _i("predictive_rank"),
        home_rating                 = _f("home_rating"),
        home_rank                   = _i("home_rank"),
        away_rating                 = _f("away_rating"),
        away_rank                   = _i("away_rank"),
        neutral_rating              = _f("neutral_rating"),
        strength_of_schedule        = _f("strength_of_schedule") or _f("sos"),
        future_strength_of_schedule = _f("future_strength_of_schedule") or _f("future_sos"),
        last_5_rating               = _f("last_5_rating") or _f("l5_rating"),
        last_10_rating              = _f("last_10_rating") or _f("l10_rating"),
        consistency_rating          = _f("consistency_rating"),
        vs_top_25_pct               = _f("vs_top_25_pct"),
        vs_bottom_25_pct            = _f("vs_bottom_25_pct"),
        projected_win_pct           = _f("projected_win_pct"),
        projected_playoff_prob      = _f("projected_playoff_prob"),
        display_odds                = _i("display_odds"),   # market context ONLY
        source_url                  = _s("source_url"),
        retrieved_at                = _s("retrieved_at"),
        freshness_age_hours         = _f("freshness_age_hours"),
        source_status               = status,
        acquisition_notes           = list(data.get("acquisition_notes") or []),
    )


def _compute_weight(
    home_record:    TeamRankingsTeamRecord,
    away_record:    TeamRankingsTeamRecord,
    matchup_prob:   float | None,
    source_status:  str,
) -> tuple[float, str]:
    """
    Governance-controlled weight for the TR sport model submodel.
    Returns (weight, reason).

    Hard rules:
    - STALE → 0.0
    - DATA_UNOBTAINABLE | NOT_ATTEMPTED | UNSUPPORTED_SPORT → 0.0
    - PROXY_ONLY → 0.0 (insufficient confidence for model contribution)
    - SOURCE_CONFLICT → 0.0
    - No direct matchup_win_prob_home → 0.0 (raw ratings cannot be converted)
    - Otherwise → min(TR_WEIGHT_DEFAULT, TR_WEIGHT_MAX)
    """
    # source_status=STALE may be set by the top-level timestamp check in
    # extract_teamrankings_enrichment (for minimal payloads with no per-team
    # timestamps).  Check it before the per-record _is_stale() guards so that
    # top-level staleness is always honoured.
    if source_status == TeamRankingsStatus.STALE:
        return TR_WEIGHT_ZERO, "STALE:source_status_stale"

    if _is_stale(home_record) or _is_stale(away_record):
        return TR_WEIGHT_ZERO, "STALE:freshness_exceeds_threshold"

    if source_status in (
        TeamRankingsStatus.DATA_UNOBTAINABLE,
        TeamRankingsStatus.NOT_ATTEMPTED,
        TeamRankingsStatus.UNSUPPORTED_SPORT,
    ):
        return TR_WEIGHT_ZERO, f"UNAVAILABLE:{source_status}"

    if source_status == TeamRankingsStatus.PROXY_ONLY:
        return TR_WEIGHT_ZERO, "PROXY_ONLY:zeroed_per_governance"

    if source_status == TeamRankingsStatus.SOURCE_CONFLICT:
        return TR_WEIGHT_ZERO, "SOURCE_CONFLICT:zeroed_per_governance"

    if matchup_prob is None:
        return TR_WEIGHT_ZERO, (
            "NO_DIRECT_MATCHUP_PROB:"
            "raw_ratings_not_converted_without_calibrated_mapping"
        )

    try:
        p = float(matchup_prob)
        if not (0.01 < p < 0.99):
            return TR_WEIGHT_ZERO, f"MATCHUP_PROB_OUT_OF_RANGE:{p:.4f}"
    except (TypeError, ValueError):
        return TR_WEIGHT_ZERO, "MATCHUP_PROB_INVALID_VALUE"

    w = min(TR_WEIGHT_DEFAULT, TR_WEIGHT_MAX)
    return w, f"RETRIEVED:effective_weight={w:.3f}"


def _compute_contradiction(
    matchup_win_prob_home:     float | None,
    core_independent_prob_home: float | None,
) -> tuple[str, float | None, bool, str | None]:
    """
    Compare TR matchup projection vs core model (both in home-team perspective).

    Returns: (agreement, delta, contradiction_flag, contradiction_reason)
      agreement : AGREE | DISCREPANCY | OPPOSITE_SIDE | ABSENT
      delta     : |TR − core| or None
      flag      : True when agreement is DISCREPANCY or OPPOSITE_SIDE
      reason    : human-readable string or None
    """
    if matchup_win_prob_home is None or core_independent_prob_home is None:
        return "ABSENT", None, False, (
            "TR_matchup_prob_absent:no_contradiction_possible"
            if matchup_win_prob_home is None
            else "core_prob_not_yet_available"
        )

    try:
        tr_p  = float(matchup_win_prob_home)
        cor_p = float(core_independent_prob_home)
    except (TypeError, ValueError):
        return "ABSENT", None, False, "INVALID_PROBABILITY_VALUE"

    delta = abs(tr_p - cor_p)
    tr_favors_home  = tr_p  > TR_OPPOSITE_SIDE_BOUNDARY
    cor_favors_home = cor_p > TR_OPPOSITE_SIDE_BOUNDARY

    if tr_favors_home != cor_favors_home:
        # TR favors the opposite winner — strongest contradiction
        return (
            "OPPOSITE_SIDE",
            delta,
            True,
            (
                f"TR_favors_{'home' if tr_favors_home else 'away'}"
                f"_core_favors_{'home' if cor_favors_home else 'away'}"
                f":delta={delta:.4f}:contradiction_review_required"
            ),
        )

    if delta >= TR_CONTRADICTION_THRESHOLD_PP:
        return (
            "DISCREPANCY",
            delta,
            True,
            (
                f"MATERIAL_DISCREPANCY:delta={delta:.4f}pp"
                f"_exceeds_threshold_{TR_CONTRADICTION_THRESHOLD_PP:.2f}"
            ),
        )

    return "AGREE", delta, False, None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_teamrankings_enrichment(
    enrichment:                  dict[str, Any],
    sport:                       str,
    *,
    core_independent_prob_home:  float | None = None,
) -> TeamRankingsMatchupEnrichment:
    """
    Build a TeamRankingsMatchupEnrichment from the GPT-supplied enrichment dict.

    Expected location: enrichment["teamrankings"] with structure:
        {
            "home":                 { per-team fields },
            "away":                 { per-team fields },
            "matchup_win_prob_home": float,   # TR direct matchup projection (preferred)
            "source_status":         str,
            "source_url":            str,
            "retrieved_at":          str,
        }

    If enrichment["teamrankings"] is absent → DATA_UNOBTAINABLE.
    If sport is unsupported → UNSUPPORTED_SPORT.
    Base model is always unaffected by TR absence.

    TR display_odds are stored in team records but NEVER injected into the sport
    model enrichment and NEVER passed to extract_no_vig_probability().
    """
    sport_upper = sport.upper().strip()
    notes: list[str] = []

    # --- Sport support ---
    if sport_upper not in TEAMRANKINGS_SUPPORTED_SPORTS:
        return TeamRankingsMatchupEnrichment(
            sport                        = sport_upper,
            source_status                = TeamRankingsStatus.UNSUPPORTED_SPORT,
            effective_weight             = TR_WEIGHT_ZERO,
            weight_reason                = f"UNSUPPORTED_SPORT:{sport_upper}",
            teamrankings_model_agreement = "ABSENT",
            acquisition_notes            = [
                f"TeamRankings coverage unavailable for sport={sport_upper}; "
                "supported: NBA, WNBA, MLB, NFL, NCAAF, NCAAB"
            ],
        )

    # --- Locate TR data ---
    tr_data = enrichment.get("teamrankings")
    if tr_data is None:
        return TeamRankingsMatchupEnrichment(
            sport                        = sport_upper,
            source_status                = TeamRankingsStatus.DATA_UNOBTAINABLE,
            effective_weight             = TR_WEIGHT_ZERO,
            weight_reason                = "DATA_UNOBTAINABLE:enrichment.teamrankings_absent",
            teamrankings_model_agreement = "ABSENT",
            acquisition_notes            = [
                "enrichment['teamrankings'] not present. "
                "TeamRankings has no authorized public API; data must be supplied "
                "by the GPT operator. Base model is unaffected."
            ],
        )

    if not isinstance(tr_data, dict):
        return TeamRankingsMatchupEnrichment(
            sport                        = sport_upper,
            source_status                = TeamRankingsStatus.SOURCE_CONFLICT,
            effective_weight             = TR_WEIGHT_ZERO,
            weight_reason                = "SOURCE_CONFLICT:teamrankings_not_a_dict",
            teamrankings_model_agreement = "ABSENT",
            acquisition_notes            = [
                f"enrichment['teamrankings'] must be a dict; "
                f"got {type(tr_data).__name__}"
            ],
        )

    # --- Per-team records ---
    home_record = _extract_team_record(tr_data.get("home") or {}, role="home")
    away_record  = _extract_team_record(tr_data.get("away") or {},  role="away")

    # --- Matchup win probability (direct projection only) ---
    matchup_prob_home: float | None = None
    matchup_src = "absent"
    raw_mp = tr_data.get("matchup_win_prob_home")
    if raw_mp is not None:
        try:
            p = float(raw_mp)
            if 0.01 < p < 0.99:
                matchup_prob_home = round(p, 4)
                matchup_src = "direct"
            else:
                notes.append(f"matchup_win_prob_home_out_of_range:{p:.4f}_ignored")
        except (TypeError, ValueError):
            notes.append("matchup_win_prob_home_invalid_value:ignored")

    # --- Overall source status ---
    top_status_raw = str(tr_data.get("source_status") or "").upper()
    if top_status_raw in TeamRankingsStatus._ALL:
        source_status = top_status_raw
    else:
        statuses = {home_record.source_status, away_record.source_status}
        if TeamRankingsStatus.SOURCE_CONFLICT in statuses:
            source_status = TeamRankingsStatus.SOURCE_CONFLICT
        elif TeamRankingsStatus.DATA_UNOBTAINABLE in statuses:
            source_status = TeamRankingsStatus.DATA_UNOBTAINABLE
        elif TeamRankingsStatus.STALE in statuses:
            source_status = TeamRankingsStatus.STALE
        elif TeamRankingsStatus.PROXY_ONLY in statuses:
            source_status = TeamRankingsStatus.PROXY_ONLY
        else:
            source_status = TeamRankingsStatus.RETRIEVED

    # Staleness override — checks per-team records AND top-level timestamp.
    # The top-level check catches minimal-submission payloads where home/away
    # records carry no per-team timestamps (only team_name was supplied).
    if source_status == TeamRankingsStatus.RETRIEVED:
        if (
            _is_stale(home_record)
            or _is_stale(away_record)
            or _is_stale_dict(tr_data)
        ):
            source_status = TeamRankingsStatus.STALE
            notes.append("STALE_OVERRIDE:freshness_age_exceeds_4h_threshold")

    # --- Weight governance ---
    effective_weight, weight_reason = _compute_weight(
        home_record, away_record, matchup_prob_home, source_status,
    )

    # --- Contradiction (pass None if core prob not available yet) ---
    agreement, delta, flag, reason = _compute_contradiction(
        matchup_prob_home, core_independent_prob_home,
    )

    notes.append(
        f"source_status={source_status} effective_weight={effective_weight:.3f} "
        f"matchup_prob_home={matchup_prob_home} agreement={agreement}"
    )
    notes.append(
        "display_odds_excluded_from_model:stored_in_team_records_for_context_only"
    )

    return TeamRankingsMatchupEnrichment(
        sport                            = sport_upper,
        home_record                      = home_record,
        away_record                      = away_record,
        matchup_win_prob_home            = matchup_prob_home,
        matchup_projection_source        = matchup_src,
        teamrankings_model_agreement     = agreement,
        teamrankings_model_delta         = delta,
        teamrankings_contradiction_flag  = flag,
        teamrankings_contradiction_reason = reason,
        effective_weight                 = effective_weight,
        weight_reason                    = weight_reason,
        source_status                    = source_status,
        display_odds_excluded_from_model = True,   # ALWAYS
        acquisition_notes                = notes,
    )


def inject_tr_features_into_clean_enrichment(
    clean_enr: dict[str, Any],
    tr_enr:    TeamRankingsMatchupEnrichment,
) -> dict[str, Any]:
    """
    Inject non-market TR features into the clean enrichment dict for the sport model.

    ONLY injects when effective_weight > 0.
    ONLY injects non-market features:
      - teamrankings_matchup_win_prob_home (model probability, NOT an odds price)
      - teamrankings_home_rating / away_rating
      - teamrankings_home_sos / away_sos
      - teamrankings_home_l5 / away_l5 / home_l10 / away_l10
      - teamrankings_effective_weight

    NEVER injects display_odds or any field in _ODDS_CONTAMINATION_FIELDS.
    Returns a new dict; does NOT mutate the original.
    """
    if tr_enr.effective_weight <= 0.0:
        return dict(clean_enr)   # no TR contribution

    out = dict(clean_enr)
    h = tr_enr.home_record
    a = tr_enr.away_record

    # Direct matchup probability (the only TR-derived model probability used)
    if tr_enr.matchup_win_prob_home is not None:
        out["teamrankings_matchup_win_prob_home"] = tr_enr.matchup_win_prob_home

    # Strength features (for observability; sport model only reads matchup_win_prob)
    if h.predictive_rating is not None:
        out["teamrankings_home_rating"] = h.predictive_rating
    if a.predictive_rating is not None:
        out["teamrankings_away_rating"] = a.predictive_rating
    if h.strength_of_schedule is not None:
        out["teamrankings_home_sos"] = h.strength_of_schedule
    if a.strength_of_schedule is not None:
        out["teamrankings_away_sos"] = a.strength_of_schedule
    if h.last_5_rating is not None:
        out["teamrankings_home_l5"] = h.last_5_rating
    if a.last_5_rating is not None:
        out["teamrankings_away_l5"] = a.last_5_rating
    if h.last_10_rating is not None:
        out["teamrankings_home_l10"] = h.last_10_rating
    if a.last_10_rating is not None:
        out["teamrankings_away_l10"] = a.last_10_rating

    # Effective weight — read by sport_model._teamrankings_predictive for cap enforcement
    out["teamrankings_effective_weight"] = tr_enr.effective_weight

    return out
