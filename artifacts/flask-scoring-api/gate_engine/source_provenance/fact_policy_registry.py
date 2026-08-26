"""
gate_engine/source_provenance/fact_policy_registry.py

Fact-policy registry for freshness and source-type acceptance.

Each FactPolicy answers three questions for a specific (fact_type, checkpoint):
  1. Which timestamp is the freshness anchor? (freshness_basis)
  2. How old can this fact be before it fails? (max_age_seconds)
  3. Which source types are accepted for this checkpoint? (accepted_source_types)

When a fact fails a check, the policy specifies which ceiling to impose:
  - stale_ceiling          : imposed when the fact is STALE or EXPIRED
  - insufficient_source_ceiling : imposed when the source_type is not accepted

Both ceilings may be None (no constraint) even when a check fires — the policy
author decides which failures warrant a cap.  A single stale official-source
lineup can still fail a current-lineup-confirmation checkpoint (freshness and
source grade are independent).

Design notes:
  - INVARIANT-1: freshness_basis is fact-specific, never universally retrieved_at.
  - INVARIANT-2: insufficient_source_ceiling is per-policy, never per-SourceType.
  - INVARIANT-3: conflict handling is not in this registry; see conflict_detector.

Lookup priority:
  1. Exact (fact_type, checkpoint)
  2. Wildcard fact_type  ("*", checkpoint)
  3. Wildcard checkpoint (fact_type, "*")
  4. Double wildcard     ("*", "*")
  5. None → FreshnessStatus.POLICY_ABSENT, max_supportable_ceiling unchanged

To add a new fact type or checkpoint, add an entry to POLICY_REGISTRY below.
No other code changes are required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evidence_contract import FreshnessBasis, FreshnessStatus, SourceType


# ---------------------------------------------------------------------------
# FactPolicy dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FactPolicy:
    """
    Governs freshness and source-type acceptance for a specific
    (fact_type, checkpoint) combination.
    """
    policy_id:    str          # Unique identifier for this policy
    description:  str          # Human-readable intent

    # Freshness
    freshness_basis:   FreshnessBasis  # Which timestamp to use as the age anchor
    max_age_seconds:   int             # Fact fails freshness when age > this value

    # Source acceptance
    accepted_source_types: frozenset[SourceType]  # Types that satisfy this checkpoint

    # Ceilings imposed when a check fails (None = no ceiling imposed)
    stale_ceiling:             str | None = None   # Ceiling when fact is STALE/EXPIRED
    insufficient_source_ceiling: str | None = None  # Ceiling when source_type not accepted

    # Optional auxiliary metadata
    notes: str = ""


# ---------------------------------------------------------------------------
# Policy registry
# ---------------------------------------------------------------------------

_ALL_SOURCES = frozenset(SourceType)
_HIGH_QUALITY = frozenset({
    SourceType.OFFICIAL, SourceType.PRIMARY_API, SourceType.SPORTSBOOK_EXCHANGE,
})
_RESEARCH_CAPABLE = frozenset({
    SourceType.OFFICIAL, SourceType.PRIMARY_API, SourceType.SPORTSBOOK_EXCHANGE,
    SourceType.TRUSTED_SECONDARY, SourceType.RECONSTRUCTED,
})
_MACHINE_READABLE = frozenset({
    SourceType.OFFICIAL, SourceType.PRIMARY_API, SourceType.SPORTSBOOK_EXCHANGE,
    SourceType.TRUSTED_SECONDARY,
})
# SCREENSHOT and OPERATOR_SUPPLIED are never accepted for live-price or
# live-verification checkpoints.  They may be accepted at earlier intake
# stages where the claim is only "we saw this on the board" rather than
# "this is the verified live price".
_CANDIDATE_IDENTIFICATION = frozenset({
    SourceType.OFFICIAL, SourceType.PRIMARY_API, SourceType.SPORTSBOOK_EXCHANGE,
    SourceType.TRUSTED_SECONDARY, SourceType.RECONSTRUCTED,
    SourceType.PROXY, SourceType.SCREENSHOT,
})


# Registry keyed on (fact_type, checkpoint).
# Use "*" as a wildcard for either dimension.
POLICY_REGISTRY: dict[tuple[str, str], FactPolicy] = {

    # ------------------------------------------------------------------
    # Player line / prop line
    # ------------------------------------------------------------------
    ("player_line", "candidate_intake"): FactPolicy(
        policy_id="PLAYER_LINE_CANDIDATE_INTAKE",
        description="Initial ingestion of a player prop line from any supported source.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=3600 * 6,   # 6 hours — stale lines may no longer be tradeable
        accepted_source_types=_CANDIDATE_IDENTIFICATION,
        stale_ceiling="WATCH",
        insufficient_source_ceiling=None,  # Any source is accepted at intake stage
        notes="Screenshot-sourced lines are accepted at intake but impose no ceiling here.",
    ),
    ("player_line", "market_gate"): FactPolicy(
        policy_id="PLAYER_LINE_MARKET_GATE",
        description="Market validation requires a machine-readable price from a live feed.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=3600,       # 1 hour
        accepted_source_types=_HIGH_QUALITY,
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
        notes="Screenshot cannot verify a live market price; SCREENSHOT → WATCH ceiling.",
    ),
    ("player_line", "final_approval"): FactPolicy(
        policy_id="PLAYER_LINE_FINAL_APPROVAL",
        description="Final approval requires fresh, machine-readable price confirmation.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=1800,       # 30 minutes
        accepted_source_types=_HIGH_QUALITY,
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
    ),
    ("player_line", "llp_calibration"): FactPolicy(
        policy_id="PLAYER_LINE_LLP_CALIBRATION",
        description="LLP calibration can use reconstructed lines from aggregators.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=3600 * 24,  # 24 hours for historical calibration
        accepted_source_types=_RESEARCH_CAPABLE,
        stale_ceiling=None,         # Calibration tolerates older data
        insufficient_source_ceiling="WATCH",
        notes="Screenshot data is insufficient for calibration probability injection.",
    ),
    ("player_line", "uac_evidence_intake"): FactPolicy(
        policy_id="PLAYER_LINE_UAC_EVIDENCE_INTAKE",
        description="Universal agent evidence intake; broad acceptance with ceiling notes.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=3600 * 4,
        accepted_source_types=_RESEARCH_CAPABLE,
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
    ),

    # ------------------------------------------------------------------
    # Odds line (live sportsbook price)
    # ------------------------------------------------------------------
    ("odds_line", "candidate_intake"): FactPolicy(
        policy_id="ODDS_LINE_CANDIDATE_INTAKE",
        description="Odds ingestion; proxy/screenshot only identifies the candidate, not the price.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=3600 * 3,
        accepted_source_types=_CANDIDATE_IDENTIFICATION,
        stale_ceiling="WATCH",
        insufficient_source_ceiling=None,
        notes="PROXY/SCREENSHOT sourced odds can flag a candidate but impose a ceiling at market_gate.",
    ),
    ("odds_line", "market_gate"): FactPolicy(
        policy_id="ODDS_LINE_MARKET_GATE",
        description="Live odds must come from a sportsbook or authoritative API.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=1800,
        accepted_source_types=frozenset({
            SourceType.OFFICIAL, SourceType.PRIMARY_API, SourceType.SPORTSBOOK_EXCHANGE,
        }),
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
        notes="Operator-supplied price data cannot satisfy a live-orderbook verification gate.",
    ),
    ("odds_line", "final_approval"): FactPolicy(
        policy_id="ODDS_LINE_FINAL_APPROVAL",
        description="Final approval requires fresh sportsbook or exchange odds.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=900,        # 15 minutes
        accepted_source_types=frozenset({SourceType.SPORTSBOOK_EXCHANGE, SourceType.PRIMARY_API}),
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
    ),
    ("odds_line", "llp_calibration"): FactPolicy(
        policy_id="ODDS_LINE_LLP_CALIBRATION",
        description="Calibration odds may be reconstructed from aggregators.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=3600 * 48,
        accepted_source_types=_RESEARCH_CAPABLE,
        stale_ceiling=None,
        insufficient_source_ceiling="WATCH",
    ),
    ("odds_line", "uac_evidence_intake"): FactPolicy(
        policy_id="ODDS_LINE_UAC_EVIDENCE_INTAKE",
        description="UAC intake for odds; reconstructed accepted with WATCH ceiling note.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=3600 * 2,
        accepted_source_types=_RESEARCH_CAPABLE,
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
    ),

    # ------------------------------------------------------------------
    # Starting pitcher
    # ------------------------------------------------------------------
    ("starting_pitcher", "candidate_intake"): FactPolicy(
        policy_id="STARTING_PITCHER_CANDIDATE_INTAKE",
        description="Starting pitcher identification; broad source acceptance.",
        freshness_basis=FreshnessBasis.PUBLISHED_AT,
        max_age_seconds=3600 * 8,
        accepted_source_types=_CANDIDATE_IDENTIFICATION,
        stale_ceiling="WATCH",
        insufficient_source_ceiling=None,
    ),
    ("starting_pitcher", "market_gate"): FactPolicy(
        policy_id="STARTING_PITCHER_MARKET_GATE",
        description="Pitcher confirmation at market gate requires official or primary source.",
        freshness_basis=FreshnessBasis.PUBLISHED_AT,
        max_age_seconds=3600 * 4,
        accepted_source_types=_HIGH_QUALITY,
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
        notes="A stale official-source lineup can still fail if published_at > 4h before gate.",
    ),
    ("starting_pitcher", "final_approval"): FactPolicy(
        policy_id="STARTING_PITCHER_FINAL_APPROVAL",
        description="Final approval: must have official confirmation within 2 hours.",
        freshness_basis=FreshnessBasis.PUBLISHED_AT,
        max_age_seconds=3600 * 2,
        accepted_source_types=frozenset({SourceType.OFFICIAL, SourceType.PRIMARY_API}),
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
    ),

    # ------------------------------------------------------------------
    # Injury status
    # ------------------------------------------------------------------
    ("injury_status", "candidate_intake"): FactPolicy(
        policy_id="INJURY_STATUS_CANDIDATE_INTAKE",
        description="Injury status ingestion; beat reporter acceptable at intake.",
        freshness_basis=FreshnessBasis.PUBLISHED_AT,
        max_age_seconds=3600 * 12,
        accepted_source_types=_CANDIDATE_IDENTIFICATION | {SourceType.OPERATOR_SUPPLIED},
        stale_ceiling="WATCH",
        insufficient_source_ceiling=None,
    ),
    ("injury_status", "market_gate"): FactPolicy(
        policy_id="INJURY_STATUS_MARKET_GATE",
        description="Injury at market gate: official or primary API required.",
        freshness_basis=FreshnessBasis.PUBLISHED_AT,
        max_age_seconds=3600 * 3,
        accepted_source_types=frozenset({
            SourceType.OFFICIAL, SourceType.PRIMARY_API, SourceType.TRUSTED_SECONDARY,
        }),
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
        notes="Social reports / screenshots do not satisfy injury confirmation at market gate.",
    ),

    # ------------------------------------------------------------------
    # Lineup confirmation
    # ------------------------------------------------------------------
    ("lineup_confirmation", "candidate_intake"): FactPolicy(
        policy_id="LINEUP_CONFIRMATION_CANDIDATE_INTAKE",
        description="Projected lineup; any machine-readable source accepted.",
        freshness_basis=FreshnessBasis.EFFECTIVE_AT,
        max_age_seconds=3600 * 6,
        accepted_source_types=_MACHINE_READABLE,
        stale_ceiling="WATCH",
        insufficient_source_ceiling=None,
    ),
    ("lineup_confirmation", "market_gate"): FactPolicy(
        policy_id="LINEUP_CONFIRMATION_MARKET_GATE",
        description="Confirmed lineup at market gate; effective_at is the semantic anchor.",
        freshness_basis=FreshnessBasis.EFFECTIVE_AT,
        max_age_seconds=3600,
        accepted_source_types=frozenset({SourceType.OFFICIAL, SourceType.PRIMARY_API}),
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
        notes=(
            "A stale official lineup still fails the checkpoint — freshness and source grade "
            "are evaluated independently.  Published_at age alone is not sufficient."
        ),
    ),

    # ------------------------------------------------------------------
    # Weather (outdoor sports)
    # ------------------------------------------------------------------
    ("weather", "candidate_intake"): FactPolicy(
        policy_id="WEATHER_CANDIDATE_INTAKE",
        description="Weather observation or forecast; NWS/official preferred but not required.",
        freshness_basis=FreshnessBasis.OBSERVED_AT,
        max_age_seconds=3600 * 3,
        accepted_source_types=_CANDIDATE_IDENTIFICATION,
        stale_ceiling="WATCH",
        insufficient_source_ceiling=None,
        notes="Consumer weather site data is accepted at intake with WATCH stale ceiling.",
    ),
    ("weather", "market_gate"): FactPolicy(
        policy_id="WEATHER_MARKET_GATE",
        description="Weather at market gate must come from an official station or NWS CLI.",
        freshness_basis=FreshnessBasis.OBSERVED_AT,
        max_age_seconds=3600 * 2,
        accepted_source_types=frozenset({SourceType.OFFICIAL, SourceType.PRIMARY_API}),
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
        notes="Consumer weather Kalshi block: PROXY weather cannot satisfy market gate.",
    ),
    ("weather", "final_approval"): FactPolicy(
        policy_id="WEATHER_FINAL_APPROVAL",
        description="Weather final approval requires official NWS CLI data.",
        freshness_basis=FreshnessBasis.OBSERVED_AT,
        max_age_seconds=3600,
        accepted_source_types=frozenset({SourceType.OFFICIAL}),
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
    ),

    # ------------------------------------------------------------------
    # Historical game log
    # ------------------------------------------------------------------
    ("historical_gamelog", "model_scoring"): FactPolicy(
        policy_id="HISTORICAL_GAMELOG_MODEL_SCORING",
        description=(
            "Historical game log used in probability modeling; reconstructed data allowed "
            "with an uncertainty penalty only when the specific sport skill explicitly permits it."
        ),
        freshness_basis=FreshnessBasis.EFFECTIVE_AT,
        max_age_seconds=3600 * 24 * 30,  # 30 days — historical data ages slowly
        accepted_source_types=frozenset({
            SourceType.OFFICIAL, SourceType.PRIMARY_API, SourceType.TRUSTED_SECONDARY,
            SourceType.RECONSTRUCTED,
        }),
        stale_ceiling=None,          # Historical staleness expected; does not impose ceiling
        insufficient_source_ceiling="WATCH",
        notes=(
            "Reconstructed historical data may support a research-only model with an "
            "uncertainty penalty, but only when the specific sport skill's existing rules "
            "explicitly allow it."
        ),
    ),
    ("historical_gamelog", "llp_calibration"): FactPolicy(
        policy_id="HISTORICAL_GAMELOG_LLP_CALIBRATION",
        description="Historical data for LLP calibration; broad acceptance.",
        freshness_basis=FreshnessBasis.EFFECTIVE_AT,
        max_age_seconds=3600 * 24 * 365,  # 1 year for historical records
        accepted_source_types=_RESEARCH_CAPABLE,
        stale_ceiling=None,
        insufficient_source_ceiling=None,
    ),
    ("historical_gamelog", "uac_evidence_intake"): FactPolicy(
        policy_id="HISTORICAL_GAMELOG_UAC_EVIDENCE_INTAKE",
        description="Historical game log evidence for UAC.",
        freshness_basis=FreshnessBasis.EFFECTIVE_AT,
        max_age_seconds=3600 * 24 * 30,
        accepted_source_types=_RESEARCH_CAPABLE,
        stale_ceiling=None,
        insufficient_source_ceiling="WATCH",
    ),

    # ------------------------------------------------------------------
    # Team statistics
    # ------------------------------------------------------------------
    ("team_stats", "model_scoring"): FactPolicy(
        policy_id="TEAM_STATS_MODEL_SCORING",
        description="Team stats for model; official or trusted secondary only.",
        freshness_basis=FreshnessBasis.EFFECTIVE_AT,
        max_age_seconds=3600 * 24 * 7,  # 1 week
        accepted_source_types=frozenset({
            SourceType.OFFICIAL, SourceType.PRIMARY_API, SourceType.TRUSTED_SECONDARY,
        }),
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
    ),

    # ------------------------------------------------------------------
    # Kalshi market price (special: operator-supplied cannot satisfy)
    # ------------------------------------------------------------------
    ("kalshi_market_price", "market_gate"): FactPolicy(
        policy_id="KALSHI_MARKET_PRICE_MARKET_GATE",
        description="Kalshi live contract price; must come from exchange API directly.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=900,   # 15 minutes
        accepted_source_types=frozenset({SourceType.SPORTSBOOK_EXCHANGE, SourceType.PRIMARY_API}),
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
        notes="Operator-supplied price data cannot satisfy a live-orderbook verification gate.",
    ),

    # ------------------------------------------------------------------
    # Universal agent evidence (catch-all for UAC packets)
    # ------------------------------------------------------------------
    ("uac_evidence", "uac_evidence_intake"): FactPolicy(
        policy_id="UAC_EVIDENCE_INTAKE",
        description="Generic UAC evidence packet intake policy.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=3600 * 6,
        accepted_source_types=_RESEARCH_CAPABLE,
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
    ),

    # ------------------------------------------------------------------
    # Wildcard fallback policies
    # ------------------------------------------------------------------
    ("*", "final_approval"): FactPolicy(
        policy_id="WILDCARD_FINAL_APPROVAL",
        description="Fallback for any fact type at final approval: require machine-readable source.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=1800,
        accepted_source_types=_MACHINE_READABLE,
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
    ),
    ("*", "market_gate"): FactPolicy(
        policy_id="WILDCARD_MARKET_GATE",
        description="Fallback for any fact type at market gate: require high-quality source.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=3600,
        accepted_source_types=_HIGH_QUALITY,
        stale_ceiling="WATCH",
        insufficient_source_ceiling="WATCH",
    ),
    ("*", "*"): FactPolicy(
        policy_id="WILDCARD_UNIVERSAL",
        description="Universal fallback: broadly permissive; marks POLICY_ABSENT in freshness.",
        freshness_basis=FreshnessBasis.RETRIEVED_AT,
        max_age_seconds=3600 * 24,
        accepted_source_types=_ALL_SOURCES,
        stale_ceiling=None,
        insufficient_source_ceiling=None,
        notes="This policy fires only when no specific policy is registered.",
    ),
}


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def lookup_policy(
    fact_type: str,
    checkpoint: str,
    *,
    route: str | None = None,
    event_proximity: str | None = None,
    materiality: str | None = None,
) -> FactPolicy | None:
    """
    Return the most specific FactPolicy for the given fact_type + checkpoint.

    Lookup priority:
      1. (fact_type, checkpoint)   — exact match
      2. ("*", checkpoint)         — wildcard fact type
      3. (fact_type, "*")          — wildcard checkpoint
      4. ("*", "*")                — universal fallback

    Returns None only when even the wildcard fallback is absent (should not
    happen with the registry above, but callers must handle it).

    Parameters route, event_proximity, materiality are reserved for future
    specialization (e.g. different policies for pre-game vs live scoring).
    They are accepted here so call sites are forward-compatible.
    """
    candidates = [
        (fact_type, checkpoint),
        ("*", checkpoint),
        (fact_type, "*"),
        ("*", "*"),
    ]
    for key in candidates:
        policy = POLICY_REGISTRY.get(key)
        if policy is not None:
            return policy
    return None
