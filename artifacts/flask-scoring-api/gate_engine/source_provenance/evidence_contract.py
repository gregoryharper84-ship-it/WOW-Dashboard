"""
gate_engine/source_provenance/evidence_contract.py

Common structured-evidence object and source-type normalization.

Design invariants:
  - SourceType does NOT by itself impose ceiling caps (INVARIANT-2).
  - Freshness is NOT computed as now - retrieved_at (INVARIANT-1).
  - Conflicting facts are flagged, never silently resolved (INVARIANT-3).

SOURCE_TYPE_NORMALIZER maps legacy source_grade.py identifiers and
llp_acquisition_resilience.py source keys to the canonical 8-value
SourceType enum.  Adding a new upstream source only requires a new
entry in SOURCE_TYPE_NORMALIZER — no logic changes elsewhere.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SourceType(str, Enum):
    """
    Normalized 8-value source type taxonomy.

    DESIGN NOTE (INVARIANT-2):
        These values are classification labels for provenance tracking.
        They do NOT hard-code ceiling caps.  The ceiling applied to a fact
        that arrives from a given source type is determined by
        FactPolicy.insufficient_source_ceiling for the specific
        (fact_type, checkpoint) combination — not by SourceType alone.
    """
    OFFICIAL            = "OFFICIAL"            # League/official body (e.g. MLB Stats API, NWS CLI)
    PRIMARY_API         = "PRIMARY_API"         # Authoritative third-party API (e.g. BallDontLie, ESPN API)
    SPORTSBOOK_EXCHANGE = "SPORTSBOOK_EXCHANGE" # Live sportsbook / exchange feed (e.g. Odds API / DK / FD)
    TRUSTED_SECONDARY   = "TRUSTED_SECONDARY"   # Reputable aggregator with editorial review
    RECONSTRUCTED       = "RECONSTRUCTED"       # Back-filled / inferred from secondary signals
    PROXY               = "PROXY"               # Consumer-facing display (e.g. PrizePicks board, consumer weather)
    SCREENSHOT          = "SCREENSHOT"          # Visual capture; no machine-readable verification
    OPERATOR_SUPPLIED   = "OPERATOR_SUPPLIED"   # Human-submitted article, blurb, social report
    UNKNOWN             = "UNKNOWN"             # Could not classify; treat conservatively


class FreshnessBasis(str, Enum):
    """
    Which timestamp is the semantic anchor for freshness evaluation.

    DESIGN NOTE (INVARIANT-1):
        The correct basis is fact-specific, not a universal 'retrieved_at'.
        - PUBLISHED_AT  : when the upstream source published the datum
        - EFFECTIVE_AT  : when the fact became operationally valid (e.g. lineup lock)
        - OBSERVED_AT   : when a human or agent observed the state (screenshot, blurb)
        - RETRIEVED_AT  : when the system fetched it; last-resort fallback only
    """
    PUBLISHED_AT = "published_at"
    EFFECTIVE_AT = "effective_at"
    OBSERVED_AT  = "observed_at"
    RETRIEVED_AT = "retrieved_at"


class FreshnessStatus(str, Enum):
    FRESH          = "FRESH"
    STALE          = "STALE"
    EXPIRED        = "EXPIRED"
    UNVERIFIABLE   = "UNVERIFIABLE"   # Required timestamp absent
    POLICY_ABSENT  = "POLICY_ABSENT"  # No registered policy for this fact_type+checkpoint


class ConflictStatus(str, Enum):
    NO_CONFLICT               = "NO_CONFLICT"
    MATERIAL_SOURCE_CONFLICT  = "MATERIAL_SOURCE_CONFLICT"   # See INVARIANT-3
    MINOR_DISCREPANCY         = "MINOR_DISCREPANCY"          # Within tolerance; both preserved
    RESOLVED_BY_POLICY        = "RESOLVED_BY_POLICY"         # Policy specified resolution rule
    PENDING_RESOLUTION        = "PENDING_RESOLUTION"         # Flagged, awaiting downstream action


class ReconstructionStatus(str, Enum):
    NOT_APPLICABLE              = "NOT_APPLICABLE"
    RECONSTRUCTED_FULL          = "RECONSTRUCTED_FULL"          # All fields reconstructed
    RECONSTRUCTED_PARTIAL       = "RECONSTRUCTED_PARTIAL"       # Some fields reconstructed
    RECONSTRUCTED_EXTRAPOLATED  = "RECONSTRUCTED_EXTRAPOLATED"  # Future-state extrapolation


class Materiality(str, Enum):
    HIGH   = "HIGH"    # Fact directly determines label or stake; conflict blocks scoring
    MEDIUM = "MEDIUM"  # Fact influences confidence tier but not terminal label alone
    LOW    = "LOW"     # Informational; conflict produces a note, not a blocker


# ---------------------------------------------------------------------------
# StructuredEvidence dataclass
# ---------------------------------------------------------------------------

@dataclass
class StructuredEvidence:
    """
    Common evidence object that extends (does not replace) the two existing
    storage tables: llp_source_snapshots and uac_evidence_packets.

    Fields marked [NEW] are added by this patch.
    Fields marked [REUSE] map to existing columns.
    Fields set by the auditor are marked [AUDITOR-SET].

    max_supportable_ceiling is None when this fact imposes no constraint.
    When set, it reflects the policy-defined cap for the failure condition
    that triggered it (stale or insufficient source type).
    """
    # --- Identity ---
    evidence_id:      str          # [REUSE] maps to snapshot_id
    fact_type:        str          # [NEW]  semantic category of the fact
    fact_value_hash:  str          # [NEW]  SHA-256 of canonical value JSON

    # --- Source provenance ---
    source_id:    str         # [REUSE] maps to snapshot_id (same as evidence_id in most cases)
    source:       str         # [REUSE] maps to source_name
    source_type:  SourceType  # [REUSE] normalized to 8-value enum
    source_grade: str         # [NEW]  letter grade from source_grade.py (A, A-, B, C, D, N/T)

    # --- Timestamps (each independently meaningful) ---
    published_at: datetime | None = None  # [NEW]  when source published this datum
    observed_at:  datetime | None = None  # [NEW]  when agent/human observed it
    effective_at: datetime | None = None  # [NEW]  when the fact became operationally valid
    retrieved_at: datetime | None = None  # [REUSE] maps to fetch_timestamp
    valid_until:  datetime | None = None  # [NEW]  when the fact expires per source policy

    # --- Freshness (set by auditor) ---
    freshness_policy_id: str | None           = None                      # [NEW] [AUDITOR-SET]
    freshness_basis:     FreshnessBasis | None = None                     # [NEW] [AUDITOR-SET]
    freshness_status:    FreshnessStatus       = FreshnessStatus.UNVERIFIABLE  # [NEW] [AUDITOR-SET]

    # --- Context ---
    materiality:         Materiality   = Materiality.MEDIUM      # [NEW]
    supports_checkpoint: list[str]     = field(default_factory=list)  # [NEW]

    # --- Conflicts (set by conflict_detector + auditor) ---
    conflicts_with:  list[str]       = field(default_factory=list)       # [NEW] evidence_ids that conflict
    conflict_status: ConflictStatus  = ConflictStatus.NO_CONFLICT        # [NEW] [AUDITOR-SET]

    # --- Reconstruction ---
    reconstruction_status: ReconstructionStatus = ReconstructionStatus.NOT_APPLICABLE  # [NEW]

    # --- Ceiling (set by auditor; None = unconstrained by this fact) ---
    max_supportable_ceiling: str | None = None  # [NEW] [AUDITOR-SET]

    # --- Audit metadata ---
    sport:       str | None = None
    market:      str | None = None
    raw_payload: dict | None = None   # [REUSE] maps to raw_payload JSONB

    can_execute:    bool = False
    execution_rule: str  = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id":             self.evidence_id,
            "fact_type":               self.fact_type,
            "fact_value_hash":         self.fact_value_hash,
            "source_id":               self.source_id,
            "source":                  self.source,
            "source_type":             self.source_type.value,
            "source_grade":            self.source_grade,
            "published_at":            self.published_at.isoformat() if self.published_at else None,
            "observed_at":             self.observed_at.isoformat() if self.observed_at else None,
            "effective_at":            self.effective_at.isoformat() if self.effective_at else None,
            "retrieved_at":            self.retrieved_at.isoformat() if self.retrieved_at else None,
            "valid_until":             self.valid_until.isoformat() if self.valid_until else None,
            "freshness_policy_id":     self.freshness_policy_id,
            "freshness_basis":         self.freshness_basis.value if self.freshness_basis else None,
            "freshness_status":        self.freshness_status.value,
            "materiality":             self.materiality.value,
            "supports_checkpoint":     list(self.supports_checkpoint),
            "conflicts_with":          list(self.conflicts_with),
            "conflict_status":         self.conflict_status.value,
            "reconstruction_status":   self.reconstruction_status.value,
            "max_supportable_ceiling": self.max_supportable_ceiling,
            "sport":                   self.sport,
            "market":                  self.market,
            "can_execute":             self.can_execute,
            "execution_rule":          self.execution_rule,
        }


# ---------------------------------------------------------------------------
# Source type normalizer
# ---------------------------------------------------------------------------

# Maps legacy source identifiers (from source_grade.py SOURCE_TYPE_GRADES and
# llp_acquisition_resilience.py) to the canonical SourceType enum.
# Extend this table when new upstream sources are introduced; no logic changes
# required elsewhere.
SOURCE_TYPE_NORMALIZER: dict[str, SourceType] = {
    # --- OFFICIAL ---
    "official_feed":                    SourceType.OFFICIAL,
    "nws_cli":                          SourceType.OFFICIAL,
    "official_weather_station":         SourceType.OFFICIAL,
    "nba_official":                     SourceType.OFFICIAL,
    "wnba_official":                    SourceType.OFFICIAL,
    "mlb_official":                     SourceType.OFFICIAL,
    "nhl_official":                     SourceType.OFFICIAL,
    "nfl_official":                     SourceType.OFFICIAL,
    "cbssports_official":               SourceType.OFFICIAL,
    "league_official":                  SourceType.OFFICIAL,
    "direct_league_official_source":    SourceType.OFFICIAL,
    "official_box_score_gamelog":       SourceType.OFFICIAL,
    # mlb stats api appears in code as these keys:
    "mlb_stats_api":                    SourceType.OFFICIAL,
    "statcast":                         SourceType.OFFICIAL,

    # --- PRIMARY_API ---
    "api_feed":                         SourceType.PRIMARY_API,
    "stat_feed":                        SourceType.PRIMARY_API,
    "odds_api":                         SourceType.PRIMARY_API,
    "sportsbook_api":                   SourceType.PRIMARY_API,
    "espn_api":                         SourceType.PRIMARY_API,
    "box_score":                        SourceType.PRIMARY_API,
    "official_gamelog":                 SourceType.PRIMARY_API,
    "balldontlie_api":                  SourceType.PRIMARY_API,
    "balldontlie":                      SourceType.PRIMARY_API,

    # --- SPORTSBOOK_EXCHANGE ---
    "the_odds_api":                     SourceType.SPORTSBOOK_EXCHANGE,
    "fanduel":                          SourceType.SPORTSBOOK_EXCHANGE,
    "draftkings":                       SourceType.SPORTSBOOK_EXCHANGE,
    "betmgm":                           SourceType.SPORTSBOOK_EXCHANGE,
    "caesars":                          SourceType.SPORTSBOOK_EXCHANGE,
    "pinnacle":                         SourceType.SPORTSBOOK_EXCHANGE,
    "betrivers":                        SourceType.SPORTSBOOK_EXCHANGE,

    # --- TRUSTED_SECONDARY ---
    "statmuse":                         SourceType.TRUSTED_SECONDARY,
    "basketball_reference":             SourceType.TRUSTED_SECONDARY,
    "bbref":                            SourceType.TRUSTED_SECONDARY,
    "her_hoop_stats":                   SourceType.TRUSTED_SECONDARY,
    "across_the_timeline":              SourceType.TRUSTED_SECONDARY,
    "rotowire":                         SourceType.TRUSTED_SECONDARY,
    "action_network":                   SourceType.TRUSTED_SECONDARY,
    "establish_the_run":                SourceType.TRUSTED_SECONDARY,
    "daily_fantasy_fuel":               SourceType.TRUSTED_SECONDARY,
    "bettingpros":                      SourceType.TRUSTED_SECONDARY,

    # --- RECONSTRUCTED ---
    "odds_aggregator":                  SourceType.RECONSTRUCTED,
    "aggregator_reconstructed":         SourceType.RECONSTRUCTED,
    "donbest":                          SourceType.RECONSTRUCTED,
    "covers":                           SourceType.RECONSTRUCTED,
    "vegasinsider":                     SourceType.RECONSTRUCTED,
    "thelines":                         SourceType.RECONSTRUCTED,
    "pp_reconstruction":                SourceType.RECONSTRUCTED,

    # --- PROXY ---
    "consumer_weather_site":            SourceType.PROXY,
    "weather_underground":              SourceType.PROXY,
    "weather_dot_com":                  SourceType.PROXY,
    "wunderground":                     SourceType.PROXY,
    "prizepicks":                       SourceType.PROXY,   # PrizePicks board (live display)

    # --- SCREENSHOT ---
    "screenshot":                       SourceType.SCREENSHOT,
    "screenshot_manual_proxy":          SourceType.SCREENSHOT,
    "pikkit":                           SourceType.SCREENSHOT,
    "prizepicks_screenshot":            SourceType.SCREENSHOT,
    "board_capture":                    SourceType.SCREENSHOT,

    # --- OPERATOR_SUPPLIED ---
    "user_supplied":                    SourceType.OPERATOR_SUPPLIED,
    "article":                          SourceType.OPERATOR_SUPPLIED,
    "preview":                          SourceType.OPERATOR_SUPPLIED,
    "blurb":                            SourceType.OPERATOR_SUPPLIED,
    "espn_blurb":                       SourceType.OPERATOR_SUPPLIED,
    "espn_article":                     SourceType.OPERATOR_SUPPLIED,
    "web_search":                       SourceType.OPERATOR_SUPPLIED,
    "news_article":                     SourceType.OPERATOR_SUPPLIED,
    "beat_reporter":                    SourceType.OPERATOR_SUPPLIED,
    "tweet":                            SourceType.OPERATOR_SUPPLIED,
    "social_report":                    SourceType.OPERATOR_SUPPLIED,
}


def normalize_source_type(raw: str | None) -> SourceType:
    """Return the canonical SourceType for *raw*, falling back to UNKNOWN."""
    if not raw:
        return SourceType.UNKNOWN
    key = raw.strip().lower()
    return SOURCE_TYPE_NORMALIZER.get(key, SourceType.UNKNOWN)


# ---------------------------------------------------------------------------
# Value hashing
# ---------------------------------------------------------------------------

def hash_fact_value(value: Any) -> str:
    """
    Produce a stable SHA-256 hex digest of *value* for conflict detection.
    Uses canonical JSON serialization (sorted keys, no whitespace variation).
    """
    if value is None:
        canonical = b"null"
    elif isinstance(value, (dict, list)):
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                               default=str).encode()
    else:
        canonical = str(value).encode()
    return hashlib.sha256(canonical).hexdigest()


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_evidence_from_dict(d: dict[str, Any]) -> StructuredEvidence:
    """
    Construct a StructuredEvidence from a raw dict such as a calibration entry,
    packet_dict, or source snapshot row.  Missing fields get safe defaults.
    """
    def _dt(v: Any) -> datetime | None:
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        try:
            return datetime.fromisoformat(str(v))
        except (ValueError, TypeError):
            return None

    raw_source_type = d.get("source_type") or d.get("source_class") or ""
    source_type = normalize_source_type(raw_source_type)

    fact_value = d.get("fact_value") or d.get("line") or d.get("odds") or d.get("value")
    evidence_id = (
        d.get("evidence_id")
        or d.get("snapshot_id")
        or d.get("source_snapshot_id")
        or "unknown"
    )

    return StructuredEvidence(
        evidence_id=evidence_id,
        fact_type=d.get("fact_type") or d.get("market") or "unknown",
        fact_value_hash=d.get("fact_value_hash") or hash_fact_value(fact_value),
        source_id=d.get("source_id") or d.get("snapshot_id") or evidence_id,
        source=d.get("source") or d.get("source_name") or d.get("book") or "unknown",
        source_type=source_type,
        source_grade=d.get("source_grade") or _infer_source_grade(source_type),
        published_at=_dt(d.get("published_at")),
        observed_at=_dt(d.get("observed_at")),
        effective_at=_dt(d.get("effective_at")),
        retrieved_at=_dt(d.get("retrieved_at") or d.get("fetch_timestamp")),
        valid_until=_dt(d.get("valid_until")),
        freshness_policy_id=d.get("freshness_policy_id"),
        freshness_basis=(
            FreshnessBasis(d["freshness_basis"])
            if d.get("freshness_basis") and d["freshness_basis"] in FreshnessBasis._value2member_map_
            else None
        ),
        freshness_status=(
            FreshnessStatus(d["freshness_status"])
            if d.get("freshness_status") and d["freshness_status"] in FreshnessStatus._value2member_map_
            else FreshnessStatus.UNVERIFIABLE
        ),
        materiality=(
            Materiality(d["materiality"])
            if d.get("materiality") and d["materiality"] in Materiality._value2member_map_
            else Materiality.MEDIUM
        ),
        supports_checkpoint=list(d.get("supports_checkpoint") or []),
        conflicts_with=list(d.get("conflicts_with") or []),
        conflict_status=(
            ConflictStatus(d["conflict_status"])
            if d.get("conflict_status") and d["conflict_status"] in ConflictStatus._value2member_map_
            else ConflictStatus.NO_CONFLICT
        ),
        reconstruction_status=(
            ReconstructionStatus(d["reconstruction_status"])
            if d.get("reconstruction_status") and d["reconstruction_status"] in ReconstructionStatus._value2member_map_
            else ReconstructionStatus.NOT_APPLICABLE
        ),
        max_supportable_ceiling=d.get("max_supportable_ceiling"),
        sport=d.get("sport"),
        market=d.get("market"),
        raw_payload=d.get("raw_payload"),
    )


def _infer_source_grade(source_type: SourceType) -> str:
    """
    Conservative source-grade inference when no explicit grade is recorded.
    This is a fallback only — the caller should prefer explicit grades from
    gate_engine/source_grade.py when they are available.
    """
    return {
        SourceType.OFFICIAL:            "A",
        SourceType.PRIMARY_API:         "A-",
        SourceType.SPORTSBOOK_EXCHANGE: "A",
        SourceType.TRUSTED_SECONDARY:   "B",
        SourceType.RECONSTRUCTED:       "B",
        SourceType.PROXY:               "C",
        SourceType.SCREENSHOT:          "D",
        SourceType.OPERATOR_SUPPLIED:   "D",
        SourceType.UNKNOWN:             "N/T",
    }.get(source_type, "N/T")
