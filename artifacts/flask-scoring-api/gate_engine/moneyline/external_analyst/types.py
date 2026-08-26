"""
gate_engine/moneyline/external_analyst/types.py
WOW-PATCH-2026-08-08-EXTERNAL-ANALYST-INTELLIGENCE

Data contracts for the External Analyst Intelligence layer.

Source role: DISCOVERY / CONTRADICTION / FAILURE-PATH research only.
  direct_probability_weight = 0.0  ALWAYS and unconditionally.

Analyst picks NEVER:
  - Directly increase or decrease P(team wins)
  - Override official event/status/starter/lineup/injury data
  - Qualify a side by themselves
  - Enter failure_path_matrix without independent verification

Analyst picks MAY:
  - Raise research priority
  - Trigger contradiction review
  - Surface factual claims for independent verification before model entry

can_execute=False unconditional.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

can_execute: bool = False  # UNCONDITIONAL

# ---------------------------------------------------------------------------
# Source status vocabulary (mirrors TeamRankings for consistency)
# ---------------------------------------------------------------------------

class AnalystSourceStatus:
    RETRIEVED               = "RETRIEVED"
    PROXY_ONLY              = "PROXY_ONLY"
    DATA_UNOBTAINABLE       = "DATA_UNOBTAINABLE"
    STALE                   = "STALE"
    SOURCE_CONFLICT         = "SOURCE_CONFLICT"
    EVENT_IDENTITY_UNRESOLVED = "EVENT_IDENTITY_UNRESOLVED"
    NOT_ATTEMPTED           = "NOT_ATTEMPTED"

    _ALL: frozenset[str] = frozenset({
        "RETRIEVED", "PROXY_ONLY", "DATA_UNOBTAINABLE", "STALE",
        "SOURCE_CONFLICT", "EVENT_IDENTITY_UNRESOLVED", "NOT_ATTEMPTED",
    })


# ---------------------------------------------------------------------------
# Analyst consensus outcomes
# ---------------------------------------------------------------------------

class AnalystConsensus:
    AGREE                    = "AGREE"               # analysts agree with WOW
    OPPOSE                   = "OPPOSE"              # analysts oppose WOW
    ANALYST_CONSENSUS_UNRESOLVED = "ANALYST_CONSENSUS_UNRESOLVED"  # analysts split
    ABSENT                   = "ABSENT"              # no analyst data


# ---------------------------------------------------------------------------
# Thesis tags (structured extraction of stated reasoning)
# ---------------------------------------------------------------------------

@dataclass
class ThesisTags:
    """
    Structured extraction of analyst's stated reasoning.

    All fields are strings containing the raw analyst claim or None.
    Claims are UNVERIFIED NARRATIVE until independently confirmed through
    stronger sources (official records, game logs, starting lineup data).
    Unverified claims never enter failure_path_matrix or the sport model.
    """
    starter_pitcher_thesis:   str | None = None
    bullpen_thesis:           str | None = None
    offense_form_thesis:      str | None = None
    lineup_injury_thesis:     str | None = None
    historical_matchup_thesis: str | None = None
    home_road_thesis:         str | None = None
    weather_venue_thesis:     str | None = None
    rest_travel_thesis:       str | None = None
    market_value_thesis:      str | None = None
    # Additional factual claims that don't fit above buckets
    other_factual_claims:     list[str]  = field(default_factory=list)
    # Narrative/promotional text that cannot be tagged as a factual claim
    unverified_narrative:     list[str]  = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "starter_pitcher_thesis":   self.starter_pitcher_thesis,
            "bullpen_thesis":           self.bullpen_thesis,
            "offense_form_thesis":      self.offense_form_thesis,
            "lineup_injury_thesis":     self.lineup_injury_thesis,
            "historical_matchup_thesis": self.historical_matchup_thesis,
            "home_road_thesis":         self.home_road_thesis,
            "weather_venue_thesis":     self.weather_venue_thesis,
            "rest_travel_thesis":       self.rest_travel_thesis,
            "market_value_thesis":      self.market_value_thesis,
            "other_factual_claims":     self.other_factual_claims,
            "unverified_narrative":     self.unverified_narrative,
        }

    def all_claims(self) -> list[str]:
        """Return all non-None thesis strings as a flat list."""
        base = [
            self.starter_pitcher_thesis,
            self.bullpen_thesis,
            self.offense_form_thesis,
            self.lineup_injury_thesis,
            self.historical_matchup_thesis,
            self.home_road_thesis,
            self.weather_venue_thesis,
            self.rest_travel_thesis,
            self.market_value_thesis,
        ]
        return [s for s in base if s] + list(self.other_factual_claims)


# ---------------------------------------------------------------------------
# Single analyst opinion record
# ---------------------------------------------------------------------------

@dataclass
class AnalystOpinion:
    """
    One analyst pick/opinion captured from an external research source.

    direct_probability_weight: ALWAYS 0.0 — this field is here only to make
    the governance constraint explicit and machine-verifiable in tests.

    source_family / analyst_family: used by family_resolver to deduplicate
    syndicated/reposted copies before contradiction counting.

    All timestamp fields are ISO 8601 strings or None.
    All price/line fields are raw strings (e.g. "-145", "+120", "7.5").
    """
    # Governance invariant — UNCONDITIONAL
    direct_probability_weight: float = 0.0  # ALWAYS 0.0

    # Source identity
    source_name:       str            = ""
    source_url:        str   | None   = None
    source_family:     str            = ""    # for deduplication (e.g. "stumps_the_spread")
    analyst_name:      str   | None   = None  # byline if available
    analyst_family:    str   | None   = None  # canonical analyst ID for dedup

    # Timestamps
    retrieved_at:      str   | None   = None  # ISO 8601 UTC
    published_at:      str   | None   = None  # publication timestamp if available

    # Event identity
    sport:             str   | None   = None
    league:            str   | None   = None
    event_id:          str   | None   = None
    event_date:        str   | None   = None
    team:              str   | None   = None  # the picked team
    opponent:          str   | None   = None
    side:              str   | None   = None  # "home" | "away" | "over" | "under"

    # Market context (display only — never fed to model)
    displayed_line:    str   | None   = None  # e.g. "-145"
    market_type:       str   | None   = None  # e.g. "moneyline" | "spread" | "total"
    favorite_role:     str   | None   = None  # "FAVORITE" | "UNDERDOG" at capture

    # Structured thesis
    thesis_tags:       ThesisTags     = field(default_factory=ThesisTags)

    # Acquisition status
    source_status:     str            = AnalystSourceStatus.NOT_ATTEMPTED
    acquisition_notes: list[str]      = field(default_factory=list)

    # Deduplication
    is_syndicated_copy: bool          = False
    canonical_opinion_key: str | None = None  # hash for dedup

    def to_dict(self) -> dict[str, Any]:
        return {
            "direct_probability_weight": self.direct_probability_weight,
            "source_name":       self.source_name,
            "source_url":        self.source_url,
            "source_family":     self.source_family,
            "analyst_name":      self.analyst_name,
            "analyst_family":    self.analyst_family,
            "retrieved_at":      self.retrieved_at,
            "published_at":      self.published_at,
            "sport":             self.sport,
            "league":            self.league,
            "event_id":          self.event_id,
            "event_date":        self.event_date,
            "team":              self.team,
            "opponent":          self.opponent,
            "side":              self.side,
            "displayed_line":    self.displayed_line,
            "market_type":       self.market_type,
            "favorite_role":     self.favorite_role,
            "thesis_tags":       self.thesis_tags.to_dict(),
            "source_status":     self.source_status,
            "acquisition_notes": self.acquisition_notes,
            "is_syndicated_copy": self.is_syndicated_copy,
            "canonical_opinion_key": self.canonical_opinion_key,
        }


# ---------------------------------------------------------------------------
# Contradiction report
# ---------------------------------------------------------------------------

@dataclass
class ContradictionReport:
    """
    Aggregated contradiction analysis across all independent analyst opinions.

    external_analyst_agreement_count:    N independent analysts agreeing with WOW side
    external_analyst_contradiction_count: N independent analysts opposing WOW side
    external_analyst_consensus_side:     "home" | "away" | ANALYST_CONSENSUS_UNRESOLVED | ABSENT
    external_analyst_conflict_flag:      True when contradiction_count >= 1
    external_analyst_conflict_reasons:   list of reason strings
    unresolved_claims:                   analyst claims not yet independently verified
    force_contradiction_review:          True when contradiction_count >= 2 (hard threshold)
    research_priority:                   "HIGH" | "ELEVATED" | "NORMAL"
    """
    external_analyst_agreement_count:    int        = 0
    external_analyst_contradiction_count: int       = 0
    external_analyst_consensus_side:     str        = AnalystConsensus.ABSENT
    external_analyst_conflict_flag:      bool       = False
    external_analyst_conflict_reasons:   list[str]  = field(default_factory=list)
    unresolved_claims:                   list[str]  = field(default_factory=list)
    force_contradiction_review:          bool       = False
    research_priority:                   str        = "NORMAL"
    independent_analyst_count:           int        = 0
    analyst_consensus_notes:             list[str]  = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_analyst_agreement_count":     self.external_analyst_agreement_count,
            "external_analyst_contradiction_count": self.external_analyst_contradiction_count,
            "external_analyst_consensus_side":      self.external_analyst_consensus_side,
            "external_analyst_conflict_flag":       self.external_analyst_conflict_flag,
            "external_analyst_conflict_reasons":    self.external_analyst_conflict_reasons,
            "unresolved_claims":                    self.unresolved_claims,
            "force_contradiction_review":           self.force_contradiction_review,
            "research_priority":                    self.research_priority,
            "independent_analyst_count":            self.independent_analyst_count,
            "analyst_consensus_notes":              self.analyst_consensus_notes,
        }


# ---------------------------------------------------------------------------
# Full layer result
# ---------------------------------------------------------------------------

@dataclass
class AnalystIntelligenceResult:
    """
    Complete output of the External Analyst Intelligence layer for one row.

    opinions:           All retrieved AnalystOpinion records (including syndicated)
    independent_opinions: Deduplicated list (one per source_family/analyst_family)
    contradiction_report: Aggregated contradiction analysis
    verified_factual_claims: Claims confirmed through stronger sources (may enter model)
    direct_probability_weight: ALWAYS 0.0 (governance invariant)
    sources_consulted:  List of source names that were queried
    sources_failed:     Sources that returned DATA_UNOBTAINABLE or STALE
    """
    opinions:                  list[AnalystOpinion]   = field(default_factory=list)
    independent_opinions:      list[AnalystOpinion]   = field(default_factory=list)
    contradiction_report:      ContradictionReport    = field(
        default_factory=ContradictionReport)
    verified_factual_claims:   list[str]              = field(default_factory=list)
    direct_probability_weight: float                  = 0.0   # ALWAYS 0.0
    sources_consulted:         list[str]              = field(default_factory=list)
    sources_failed:            list[str]              = field(default_factory=list)
    acquisition_notes:         list[str]              = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "direct_probability_weight":  self.direct_probability_weight,
            "sources_consulted":          self.sources_consulted,
            "sources_failed":             self.sources_failed,
            "independent_analyst_count":  len(self.independent_opinions),
            "total_opinion_count":        len(self.opinions),
            "opinions":                   [o.to_dict() for o in self.opinions],
            "independent_opinions":       [o.to_dict() for o in self.independent_opinions],
            "contradiction_report":       self.contradiction_report.to_dict(),
            "verified_factual_claims":    self.verified_factual_claims,
            "acquisition_notes":          self.acquisition_notes,
        }
