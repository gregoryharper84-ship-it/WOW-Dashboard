"""
types.py — Core dataclasses and enums for the opportunity acquisition layer.

All structures carry can_execute=False unconditionally.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Acquisition status enum
# ---------------------------------------------------------------------------

class AcquisitionStatus(str, Enum):
    """Per-field acquisition status.  NOT_CALLED must never survive reconciliation."""
    RETRIEVED         = "retrieved"
    RECONSTRUCTED     = "reconstructed"
    PROXY_ONLY        = "proxy-only"
    SOURCE_CONFLICT   = "source-conflict"
    DATA_UNOBTAINABLE = "data-unobtainable"
    INPUT_FAILURE     = "input-failure"
    NOT_APPLICABLE    = "not-applicable"
    NOT_CALLED        = "not-called"   # internal; must be replaced before final output


class LineupStatus(str, Enum):
    CONFIRMED   = "confirmed"
    EXPECTED    = "expected"
    UNCONFIRMED = "unconfirmed"
    UNKNOWN     = "unknown"


class PropFamily(str, Enum):
    PRA = "pra"
    PR  = "p+r"
    RA  = "r+a"
    PA  = "p+a"
    PTS = "points"
    REB = "rebounds"
    AST = "assists"


# ---------------------------------------------------------------------------
# Sub-structures
# ---------------------------------------------------------------------------

@dataclass
class MinutesDistribution:
    """Projected minutes distribution for one vendor / consensus."""
    low:        float
    mode:       float
    high:       float
    confidence: float  # 0.0–1.0
    source:     str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "low":        self.low,
            "mode":       self.mode,
            "high":       self.high,
            "confidence": self.confidence,
            "source":     self.source,
        }


@dataclass
class ComponentOpportunityRates:
    """Per-minute opportunity rates for the three PRA components."""
    scoring_per_min:    float | None = None
    rebounding_per_min: float | None = None
    assisting_per_min:  float | None = None
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scoring_per_min":    self.scoring_per_min,
            "rebounding_per_min": self.rebounding_per_min,
            "assisting_per_min":  self.assisting_per_min,
            "source":             self.source,
        }


# ---------------------------------------------------------------------------
# VendorPacket — typed output from one source adapter
# ---------------------------------------------------------------------------

@dataclass
class VendorPacket:
    """Typed result from a single source adapter call."""
    source:         str   # e.g. "balldontlie", "odds_api", "internal_stats_api"
    retrieved_at:   str   # ISO-8601 UTC
    source_grade:   str   # A / B / C
    request_status: str   # success / failed / auth-required / rate-limited / unavailable / empty

    # Per-field outputs (None when adapter did not obtain the field)
    minutes_distribution:     MinutesDistribution | None       = None
    lineup_status:            LineupStatus                     = LineupStatus.UNKNOWN
    starter_probability:      float | None                     = None
    rotation_confidence:      float | None                     = None
    minutes_restriction_prob: float | None                     = None
    blowout_risk:             float | None                     = None
    component_opportunity:    ComponentOpportunityRates | None = None
    event_status:             str | None                       = None
    player_status:            str | None                       = None
    teammate_status:          dict[str, str]                   = field(default_factory=dict)

    raw:            dict[str, Any] = field(default_factory=dict)
    failure_reason: str | None     = None

    # Provenance label — distinguishes live HTTP vendor calls from enrichment reads.
    # Values: "vendor_retrieved" | "enrichment_provided" | "not-attempted" | "auth-required"
    data_provenance: str = "not-attempted"

    # Unconditional
    can_execute: bool = False


# ---------------------------------------------------------------------------
# OpportunityState — finalized record attached to each row
# ---------------------------------------------------------------------------

@dataclass
class OpportunityState:
    """
    Normalized player-event opportunity state produced by the AcquisitionOrchestrator.

    can_execute=False is unconditional and must never be overridden by callers.
    """
    can_execute: bool = False   # UNCONDITIONAL — do not change

    # Minutes distribution (consensus or best-available)
    minutes_distribution:     MinutesDistribution | None = None
    minutes_source_agreement: bool  = False
    minutes_conflict_penalty: float = 0.0   # 0.0–1.0; raised when sources disagree >15%

    # Role state
    starter_probability:       float | None  = None
    lineup_status:             LineupStatus  = LineupStatus.UNKNOWN
    rotation_confidence:       float | None  = None
    minutes_restriction_prob:  float | None  = None
    blowout_risk:              float | None  = None

    # Component rates
    component_opportunity: ComponentOpportunityRates | None = None

    # Teammate availability
    teammate_status: dict[str, str] = field(default_factory=dict)

    # Source metadata
    source_timestamps:     dict[str, str]        = field(default_factory=dict)
    source_agreement:      bool                  = False
    source_conflict_pairs: list[tuple[str, str]] = field(default_factory=list)
    composite_confidence:  float                 = 0.0

    # Per-field acquisition status
    # NOT_CALLED must be replaced during reconciliation
    per_field_statuses: dict[str, AcquisitionStatus] = field(default_factory=dict)

    # Internal
    vendor_packets: list[VendorPacket] = field(default_factory=list)
    notes:          list[str]          = field(default_factory=list)

    # ---------------------------------------------------------------------------
    # Reconciliation helpers
    # ---------------------------------------------------------------------------

    def reconcile(self) -> None:
        """
        Replace any NOT_CALLED status with DATA_UNOBTAINABLE.
        Must be called before the state is attached to a row.
        """
        for field_name, status in list(self.per_field_statuses.items()):
            if status == AcquisitionStatus.NOT_CALLED:
                self.per_field_statuses[field_name] = AcquisitionStatus.DATA_UNOBTAINABLE
                self.notes.append(
                    f"NOT_CALLED→DATA_UNOBTAINABLE reconciled for field: {field_name}"
                )

    def has_not_called(self) -> bool:
        """True if any required field was never attempted — indicates incomplete run."""
        return any(
            s == AcquisitionStatus.NOT_CALLED
            for s in self.per_field_statuses.values()
        )

    def has_live_opportunity_data(self) -> bool:
        """
        True if we have at least a minutes distribution with RETRIEVED/RECONSTRUCTED status.
        Historical-only rows have no live opportunity data and must fail closed.
        """
        minutes_status = self.per_field_statuses.get("minutes_distribution")
        if minutes_status in (AcquisitionStatus.RETRIEVED, AcquisitionStatus.RECONSTRUCTED):
            return self.minutes_distribution is not None
        return False

    def lineup_is_confirmed(self) -> bool:
        return self.lineup_status == LineupStatus.CONFIRMED

    # ---------------------------------------------------------------------------
    # Serialization
    # ---------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_execute":              False,
            "minutes_distribution":     (
                self.minutes_distribution.to_dict()
                if self.minutes_distribution else None
            ),
            "minutes_source_agreement": self.minutes_source_agreement,
            "minutes_conflict_penalty": self.minutes_conflict_penalty,
            "starter_probability":      self.starter_probability,
            "lineup_status":            self.lineup_status.value,
            "rotation_confidence":      self.rotation_confidence,
            "minutes_restriction_prob": self.minutes_restriction_prob,
            "blowout_risk":             self.blowout_risk,
            "component_opportunity":    (
                self.component_opportunity.to_dict()
                if self.component_opportunity else None
            ),
            "teammate_status":          self.teammate_status,
            "source_timestamps":        self.source_timestamps,
            "source_agreement":         self.source_agreement,
            "source_conflict_pairs":    [list(p) for p in self.source_conflict_pairs],
            "composite_confidence":     self.composite_confidence,
            "per_field_statuses":       {
                k: v.value for k, v in self.per_field_statuses.items()
            },
            "notes":                    self.notes,
        }
