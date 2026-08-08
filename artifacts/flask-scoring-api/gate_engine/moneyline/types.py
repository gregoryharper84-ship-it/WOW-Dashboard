"""
gate_engine/moneyline/types.py
WOW v16 — Moneyline layer boundary contracts.

Defines the four clean output fields and the input boundary that enforces
zero sportsbook-price contamination of the independent model.

can_execute=False unconditional.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

can_execute: bool = False

# ---------------------------------------------------------------------------
# Contamination guard — odds-derived field names that must never reach the
# independent model's input scope
# ---------------------------------------------------------------------------

_ODDS_CONTAMINATION_FIELDS: frozenset[str] = frozenset({
    "no_vig_prob", "no_vig_probability", "no_vig",
    "implied_prob", "implied_probability",
    "market_consensus", "market_consensus_prob",
    "american_odds", "decimal_odds", "fractional_odds",
    "sportsbook_odds", "moneyline_odds",
    "consensus_prior", "market_prior",
    "home_odds", "away_odds", "draw_odds",
    "overround", "hold_pct", "book_edge",
})


class IndependentModelContaminationError(ValueError):
    """Raised when odds-derived fields are found in the independent model's input."""
    pass


def check_independence_boundary(enrichment: dict[str, Any]) -> None:
    """
    Enforce that no odds-derived field is present in the enrichment dict
    passed to the independent sport model.

    Raises IndependentModelContaminationError if any contamination is detected.
    """
    found = [k for k in enrichment if k in _ODDS_CONTAMINATION_FIELDS]
    if found:
        raise IndependentModelContaminationError(
            f"INDEPENDENT_MODEL_CONTAMINATION: odds-derived fields present in "
            f"model input scope: {sorted(found)}. "
            f"Market data must only enter at the calibration stage."
        )


def strip_odds_fields(enrichment: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of enrichment with all odds-derived fields removed."""
    return {k: v for k, v in enrichment.items() if k not in _ODDS_CONTAMINATION_FIELDS}


# ---------------------------------------------------------------------------
# Four clean output fields — never label one as another
# ---------------------------------------------------------------------------

@dataclass
class MoneylineOutputs:
    """
    The four distinct probability/value outputs.

    independent_probability
        Computed purely from sport model, historical data, Elo, power ratings.
        Contains zero sportsbook price input.

    calibrated_probability
        Bounded shrinkage blend: (1-w)*independent + w*market_no_vig.
        market_no_vig only enters here, never upstream.

    calibrated_probability_lower_bound
        Conservative floor of calibrated_probability accounting for all
        uncertainty sources (sport volatility, sample size, lineup certainty,
        model disagreement, source conflict, freshness).
        Used for candidate ranking — not raw probability.

    net_edge
        market_no_vig − calibrated_probability (positive = model favors this
        side more than the market does). Downstream-only output.
    """
    independent_probability:            float | None = None
    calibrated_probability:             float | None = None
    calibrated_probability_lower_bound: float | None = None
    calibrated_probability_upper_bound: float | None = None
    net_edge:                           float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "independent_probability":            self.independent_probability,
            "calibrated_probability":             self.calibrated_probability,
            "calibrated_probability_lower_bound": self.calibrated_probability_lower_bound,
            "calibrated_probability_upper_bound": self.calibrated_probability_upper_bound,
            "net_edge":                           self.net_edge,
        }


# ---------------------------------------------------------------------------
# Full moneyline result
# ---------------------------------------------------------------------------

@dataclass
class MoneylineResult:
    """
    Complete output from run_moneyline_pipeline().

    All layer-by-layer intermediate outputs are preserved for observability.
    can_execute=False unconditional.
    """
    # Governance
    can_execute:          bool           = False  # UNCONDITIONAL
    can_approve_bets:     bool           = False
    objective:            str            = "OUTRIGHT_WIN_PROBABILITY_ONLY"
    controlling_skill:    str            = "wow.llp-moneyline-probability-expert"

    # Terminal label and blockers (WOW v16 governance labels)
    terminal_label:       str            = "DATA_CONTRACT_FAIL"
    blockers:             list[str]      = field(default_factory=list)

    # Four clean outputs
    outputs:              MoneylineOutputs = field(default_factory=MoneylineOutputs)

    # Layer observability
    sport_model:          dict[str, Any] = field(default_factory=dict)
    simulation:           dict[str, Any] = field(default_factory=dict)
    failure_path:         dict[str, Any] = field(default_factory=dict)
    disagreement_audit:   dict[str, Any] = field(default_factory=dict)
    calibration:          dict[str, Any] = field(default_factory=dict)
    classification:       dict[str, Any] = field(default_factory=dict)
    market_comparison:    dict[str, Any] = field(default_factory=dict)
    final_refresh:        dict[str, Any] = field(default_factory=dict)
    slate_integrity:      dict[str, Any] = field(default_factory=dict)
    # TeamRankings secondary enrichment layer (WOW-PATCH-2026-08-08-TEAMRANKINGS)
    teamrankings:         dict[str, Any] = field(default_factory=dict)
    # External Analyst Intelligence layer (WOW-PATCH-2026-08-08-EXTERNAL-ANALYST)
    external_analyst_intelligence: dict[str, Any] = field(default_factory=dict)

    # Metadata
    model_id:             str | None     = None
    model_status:         str | None     = None
    snapshot_hash:        str | None     = None
    created_at:           str            = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Soccer 1X2
    three_state_1x2:      dict | None    = None

    def build_snapshot_hash(self) -> str:
        """Produce immutable SHA-256 hash of the probability outputs."""
        body = {
            "independent_probability":            self.outputs.independent_probability,
            "calibrated_probability":             self.outputs.calibrated_probability,
            "calibrated_probability_lower_bound": self.outputs.calibrated_probability_lower_bound,
            "terminal_label":                     self.terminal_label,
            "created_at":                         self.created_at,
        }
        raw = json.dumps(body, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        d = {
            "can_execute":           self.can_execute,
            "can_approve_bets":      self.can_approve_bets,
            "objective":             self.objective,
            "controlling_skill":     self.controlling_skill,
            "terminal_label":        self.terminal_label,
            "blockers":              self.blockers,
            "model_id":              self.model_id,
            "model_status":          self.model_status,
            "snapshot_hash":         self.snapshot_hash,
            "created_at":            self.created_at,
            "three_state_1x2":       self.three_state_1x2,
        }
        d.update(self.outputs.to_dict())
        d["layers"] = {
            "sport_model":        self.sport_model,
            "simulation":         self.simulation,
            "failure_path":       self.failure_path,
            "disagreement_audit": self.disagreement_audit,
            "calibration":        self.calibration,
            "classification":     self.classification,
            "market_comparison":  self.market_comparison,
            "final_refresh":      self.final_refresh,
            "slate_integrity":    self.slate_integrity,
            "teamrankings":                 self.teamrankings,
            "external_analyst_intelligence": self.external_analyst_intelligence,
        }
        return d
