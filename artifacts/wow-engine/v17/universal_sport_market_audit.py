"""Read-only, conservative cross-sport market coverage inventory.

This module reports *declared* sport and market applicability, not runtime
certification. A sport's available winner route never certifies its spreads,
totals, player props, or deployment. No side effects, scoring or order paths.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from v17.team_event_capability_manifest import (
    EXPECTED_TEAM_EVENT_SPORTS,
    TEAM_EVENT_INPUT_CONTRACTS,
    team_event_capability,
)

Market = Literal["WINNER", "HANDICAP", "TOTAL", "PLAYER_PROP"]
MARKETS: tuple[Market, ...] = ("WINNER", "HANDICAP", "TOTAL", "PLAYER_PROP")
AUDIT_SCHEMA_VERSION = "WOW_V17_UNIVERSAL_SPORT_MARKET_AUDIT_V1"

# NOT_APPLICABLE applies only to structurally inappropriate *generic* markets,
# never to a missing model or to a currently unavailable market price.
# Golf tournament field winners and H2H require distinct settlement contracts.
MARKET_APPLICABILITY: dict[str, frozenset[Market]] = {
    sport: frozenset(MARKETS) for sport in EXPECTED_TEAM_EVENT_SPORTS
}
MARKET_APPLICABILITY["PGA"] = frozenset(("WINNER", "HANDICAP", "TOTAL", "PLAYER_PROP"))
MARKET_APPLICABILITY["MMA"] = frozenset(("WINNER", "HANDICAP", "TOTAL", "PLAYER_PROP"))
MARKET_APPLICABILITY["BOXING"] = frozenset(("WINNER", "HANDICAP", "TOTAL", "PLAYER_PROP"))


@dataclass(frozen=True)
class MarketAuditRow:
    sport: str
    market: Market
    status: str
    declared_specialist: str | None
    required_inputs: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    is_certified_market: bool = False
    production_verified: bool = False
    can_execute: bool = False


# Evidence is deliberately independent for every market. In particular, a
# winner's fitted artifact cannot qualify a margin or total distribution.
_MARKET_EVIDENCE: dict[Market, tuple[str, ...]] = {
    "WINNER": (
        "exact_official_event_identity", "pregame_feature_provenance",
        "sport_specific_fitted_artifact", "chronological_holdout",
        "independent_calibration_and_bounds", "immutable_pregame_receipt",
        "independent_acceptance", "deployed_exact_sha_readback",
    ),
    "HANDICAP": (
        "exact_line_period_and_settlement", "fitted_margin_distribution",
        "push_rules", "chronological_holdout", "independent_calibration",
        "immutable_pregame_receipt", "independent_acceptance",
        "deployed_exact_sha_readback",
    ),
    "TOTAL": (
        "exact_line_period_and_overtime_rules", "fitted_scoring_distribution",
        "push_rules", "chronological_holdout", "independent_calibration",
        "immutable_pregame_receipt", "independent_acceptance",
        "deployed_exact_sha_readback",
    ),
    "PLAYER_PROP": (
        "sport_specific_player_scalar_owner", "canonical_player_identity",
        "exact_stat_line_period_and_settlement", "fitted_player_distribution",
        "availability_and_role_provenance", "chronological_holdout",
        "independent_calibration", "immutable_pregame_receipt",
        "independent_acceptance", "deployed_exact_sha_readback",
    ),
}


def build_universal_sport_market_audit() -> tuple[MarketAuditRow, ...]:
    """Construct a fail-closed inventory; NEVER use as capability promotion."""
    assert set(EXPECTED_TEAM_EVENT_SPORTS) == set(TEAM_EVENT_INPUT_CONTRACTS)
    assert set(EXPECTED_TEAM_EVENT_SPORTS) == set(MARKET_APPLICABILITY)
    rows: list[MarketAuditRow] = []
    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        capability = team_event_capability(sport)
        for market in MARKETS:
            if market not in MARKET_APPLICABILITY[sport]:
                rows.append(MarketAuditRow(
                    sport=sport, market=market, status="NOT_APPLICABLE",
                    declared_specialist=None, required_inputs=(),
                    missing_evidence=(),
                ))
                continue
            # Repository-level static availability is NOT enough to establish
            # current-event certification or production verification.
            # In particular, historical MLB/NFL capability labels must not
            # qualify a specific matchup, market line, or deployment.
            rows.append(MarketAuditRow(
                sport=sport, market=market, status="REQUIRES_EVENT_LEVEL_VERIFICATION",
                declared_specialist=(
                    capability.controlling_specialist if market == "WINNER" else None
                ),
                required_inputs=tuple(capability.required_inputs),
                missing_evidence=_MARKET_EVIDENCE[market],
            ))
    return tuple(rows)


def audit_summary() -> dict[str, object]:
    rows = build_universal_sport_market_audit()
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "kind": "NON_AUTHORITATIVE_READ_ONLY_INVENTORY",
        "sport_count": len(EXPECTED_TEAM_EVENT_SPORTS),
        "market_family_count": len(MARKETS),
        "row_count": len(rows),
        "certified_market_rows": 0,
        "production_verified_rows": 0,
        "can_execute": False,
        "rows": [asdict(row) for row in rows],
    }
