"""
validation/ablation/features.py

Feature registry for the ablation runner.

Each feature has:
  - id:           unique string identifier (matches eval_rules.yaml)
  - description:  human-readable description
  - supported:    True if the harness can supply this feature
  - source:       where the value comes from
  - required_enrichment_key: enrichment dict key that carries this feature
    (None if derived or computed)

Unsupported features are reported as "UNAVAILABLE" in ablation results.
They are NEVER fabricated, imputed, or given a default value.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FeatureSpec:
    id:                       str
    description:              str
    supported:                bool
    source:                   str
    required_enrichment_key:  Optional[str] = None
    unavailable_reason:       Optional[str] = None


FEATURE_REGISTRY: list[FeatureSpec] = [
    FeatureSpec(
        id          = "failure_path",
        description = "Pitcher's historical failure rate (early exit, high pitch counts) in first inning",
        supported   = True,
        source      = "savant_1ip_ledger.ledger_rows — derived from l10_hit_rate and l10_pitch_mean",
        required_enrichment_key = "savant_1ip_ledger",
    ),
    FeatureSpec(
        id          = "l10_discernment",
        description = "L10 vs season hit-rate delta — identifies trend direction",
        supported   = True,
        source      = "savant_1ip_ledger.l5_hit_rate vs l10_hit_rate",
        required_enrichment_key = "savant_1ip_ledger",
    ),
    FeatureSpec(
        id          = "top_four_detail",
        description = "BF distribution detail: P(BF=3), P(BF=4), P(BF≥5)",
        supported   = True,
        source      = "savant_1ip_ledger.bf_distribution",
        required_enrichment_key = "first_inning_bf_distribution",
    ),
    FeatureSpec(
        id                = "handedness",
        description       = "Pitcher throws L/R vs opponent lineup handedness split",
        supported         = False,
        source            = "Not available in current Savant ledger extraction",
        unavailable_reason = "Handedness splits require batter-level Statcast queries not in the current ledger schema",
    ),
    FeatureSpec(
        id                = "health_workload",
        description       = "Pitcher recent workload: days rest, pitch count prior 3 games",
        supported         = False,
        source            = "MLB Stats API schedule — not yet wired into harness",
        unavailable_reason = "Workload acquisition module not built; requires /schedule endpoint enrichment",
    ),
    FeatureSpec(
        id                = "catcher",
        description       = "Catcher pitch-framing score and game-call tendencies",
        supported         = False,
        source            = "savant_1ip_ledger.ledger_rows.catcher_mlbam_id — identity present; stats not fetched",
        unavailable_reason = "Catcher stats module not built; catcher_mlbam_id available but framing stats require separate Savant query",
    ),
    FeatureSpec(
        id                = "weather",
        description       = "Game-time temperature, wind speed, and dome flag",
        supported         = False,
        source            = "NWS CLI / weather gate — not wired into offline harness",
        unavailable_reason = "Weather acquisition requires live NWS API call at game time; retrospective data unavailable in Savant schema",
    ),
    FeatureSpec(
        id                = "market_prior",
        description       = "No-vig sportsbook implied probability at prediction time",
        supported         = False,
        source            = "Odds API — available live but not stored historically",
        unavailable_reason = "Historical market prices not stored; live-only Odds API cannot provide past game-time lines",
    ),
    FeatureSpec(
        id                = "recent_form",
        description       = "Pitcher's last 3 starts: trend in pitch counts and BF",
        supported         = True,
        source            = "savant_1ip_ledger.ledger_rows last 3 entries",
        required_enrichment_key = "savant_1ip_ledger",
    ),
    FeatureSpec(
        id                = "matchup_adjustment",
        description       = "Opponent L10 strikeout rate and K% vs pitcher historical",
        supported         = False,
        source            = "MLB Stats API team batting — not wired into offline harness",
        unavailable_reason = "Opponent acquisition not built; requires separate MLB Stats API team-batting query per game",
    ),
]

FEATURE_MAP: dict[str, FeatureSpec] = {f.id: f for f in FEATURE_REGISTRY}


def get_feature(feature_id: str) -> FeatureSpec:
    if feature_id not in FEATURE_MAP:
        raise KeyError(f"Unknown feature: {feature_id!r}")
    return FEATURE_MAP[feature_id]


def supported_features() -> list[FeatureSpec]:
    return [f for f in FEATURE_REGISTRY if f.supported]


def unavailable_features() -> list[FeatureSpec]:
    return [f for f in FEATURE_REGISTRY if not f.supported]
