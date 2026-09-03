"""Immutable envelopes for V17 Daily evidence handoff.

These envelopes enforce complete data contracts between:
- Daily hydration → team event candidate envelope
- Fitted model output → governed probability package
- Governed probability → LLP ingress and identity lock

Every field must be either present with value, or explicitly marked unavailable
with source and timestamp. NOT_CALLED is forbidden once canonical hydration completes.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Literal, Optional
from functools import wraps


def _freeze(cls):
    """Decorator to make a dataclass frozen and hashable."""
    original_init = cls.__init__

    @wraps(original_init)
    def frozen_init(self, *args, **kwargs):
        object.__setattr__(self, '__initialized__', False)
        original_init(self, *args, **kwargs)
        object.__setattr__(self, '__initialized__', True)

    def __setattr__(self, name, value):
        if hasattr(self, '__initialized__') and self.__initialized__:
            raise RuntimeError(f"Cannot modify frozen envelope: {name}")
        object.__setattr__(self, name, value)

    cls.__init__ = frozen_init
    cls.__setattr__ = __setattr__
    return cls


@dataclass(frozen=True)
class DataUnavailable:
    """Explicit marker for unavailable evidence with provenance."""
    status: Literal["DATA_UNOBTAINABLE", "SOURCE_CONFLICT", "FAILED"]
    source_attempted: list[str] = field(default_factory=list)
    as_of: Optional[str] = None
    error_type: Optional[str] = None


@dataclass(frozen=True)
class V17TeamEventCandidateEnvelope:
    """Immutable canonical envelope for team/event candidates.

    Every field required by the patch must be present here with explicit
    provenance. Missing data is marked with DataUnavailable, never NOT_CALLED.
    """

    research_run_id: str
    requested_slate_date: str
    requested_timezone: str

    event_key: str
    official_event_id: str
    official_event_id_source: str
    event_start_time_utc: str
    event_date_local: str
    sport: str
    league: str
    home_team: str
    away_team: str
    venue: str | DataUnavailable
    official_event_status: str | DataUnavailable
    official_event_status_source: Optional[str]

    settlement_market: str
    settlement_basis: str
    settlement_rule: str | DataUnavailable
    settlement_source: Optional[str]

    home_starter: Optional[str]
    home_starter_status: str | DataUnavailable
    home_starter_source: Optional[str]
    away_starter: Optional[str]
    away_starter_status: str | DataUnavailable
    away_starter_source: Optional[str]

    home_lineup_status: str | DataUnavailable
    home_lineup_source: Optional[str]
    away_lineup_status: str | DataUnavailable
    away_lineup_source: Optional[str]

    injury_status: str | DataUnavailable
    injury_source: Optional[str]
    weather_status: str | DataUnavailable
    weather_source: Optional[str]
    bullpen_status: str | DataUnavailable
    bullpen_source: Optional[str]

    market_snapshot_id: Optional[str]
    market_snapshot_timestamp: Optional[str]
    market_source: Optional[str]
    market_status: Literal["EXACT_LINE", "ADJACENT_LINE", "NO_MARKET", "DATA_UNOBTAINABLE"]
    book_count: Optional[int]
    market_role: Optional[str]
    market_role_status: str | DataUnavailable
    consensus_probability_no_vig: Optional[float]
    market_prior_probability: Optional[float]

    source_snapshot_id: str
    source_snapshot_timestamp: str
    latest_material_update_timestamp: Optional[str]
    evidence_as_of: str

    def validate_identity(self) -> tuple[bool, list[str]]:
        """Validate that required identity fields are present."""
        errors = []
        if not self.official_event_id:
            errors.append("official_event_id_missing")
        if not self.official_event_id_source:
            errors.append("official_event_id_source_missing")
        if not self.event_start_time_utc:
            errors.append("event_start_time_utc_missing")
        if not self.home_team:
            errors.append("home_team_missing")
        if not self.away_team:
            errors.append("away_team_missing")
        if not self.settlement_market:
            errors.append("settlement_market_missing")

        return len(errors) == 0, errors

    def validate_market_context(self) -> tuple[bool, list[str]]:
        """Validate that required market context is present."""
        errors = []
        if self.market_status in ("EXACT_LINE", "ADJACENT_LINE"):
            if not self.market_snapshot_id:
                errors.append("market_snapshot_id_missing_for_exact_or_adjacent")
            if not self.market_snapshot_timestamp:
                errors.append("market_snapshot_timestamp_missing_for_exact_or_adjacent")
            if not self.market_source:
                errors.append("market_source_missing_for_exact_or_adjacent")

        return len(errors) == 0, errors


@dataclass(frozen=True)
class V17FailurePath:
    """Failure path evidence classification per patch section 7."""
    path_type: str
    classification: Literal["modeled_path", "evidence_only_path", "unavailable_path"]
    probability_if_modeled: Optional[float] = None
    evidence_summary: Optional[str] = None

    def validate(self) -> tuple[bool, str]:
        """Validate that synthetic probabilities are not fabricated."""
        if self.classification == "evidence_only_path" and self.probability_if_modeled is not None:
            return False, "evidence_only_path cannot have synthetic probability"
        if self.classification == "unavailable_path" and self.probability_if_modeled is not None:
            return False, "unavailable_path cannot have probability"
        return True, ""


@dataclass(frozen=True)
class V17GovernedProbabilityPackage:
    """Immutable package for sport-model probability with complete governance.

    Wrapped output from fitted model must traverse losslessly. All fields
    the model produced must be forwarded or explicitly translated.
    """

    research_run_id: str
    event_key: str
    official_event_id: str
    participant: str
    opponent: str
    market_role: Optional[str]
    outcome_space: str

    raw_model_probability: float
    independent_model_probability: float
    market_prior_probability: Optional[float]
    market_prior_weight: float

    calibrated_probability: float
    calibrated_probability_lower_bound: float
    calibrated_probability_upper_bound: float

    calibration_method: str
    calibration_version: str
    calibration_sample_scope: str
    calibration_health_status: str

    model_version: str
    model_timestamp: str
    latest_material_update_timestamp: Optional[str]
    model_valid_after_latest_material_update: bool

    source_snapshot_id: str
    source_snapshot_timestamp: str

    simulation_count_if_applicable: Optional[int] = None
    model_component_weights_if_available: Optional[dict[str, Any]] = None
    model_disagreement_if_available: Optional[float] = None
    uncertainty_method: Optional[str] = None

    favorite_primary_win_path: Optional[str] = None
    favorite_primary_failure_path: Optional[str] = None
    favorite_failure_path_probability_if_modeled: Optional[float] = None
    largest_favorite_loss_path: Optional[str] = None
    underdog_upset_path: Optional[str] = None

    def validate_failure_paths(self) -> tuple[bool, list[str]]:
        """Validate failure path evidence per patch section 7.

        Fitted model may produce failure paths. If not produced by model,
        paths are descriptive evidence only and synthetic probabilities
        are forbidden.
        """
        errors = []

        if self.favorite_failure_path_probability_if_modeled is not None:
            if not (0.0 <= self.favorite_failure_path_probability_if_modeled <= 1.0):
                errors.append("failure_path_probability_outside_valid_domain")

        return len(errors) == 0, errors

    def validate_calibration(self) -> tuple[bool, list[str]]:
        """Validate complete calibration provenance."""
        errors = []
        if self.calibration_health_status == "PASS":
            if not self.calibration_method:
                errors.append("calibration_method_missing_after_health_pass")
            if not self.calibration_version:
                errors.append("calibration_version_missing_after_health_pass")
            if not self.calibration_sample_scope:
                errors.append("calibration_sample_scope_missing_after_health_pass")
            if self.calibrated_probability is None:
                errors.append("calibrated_probability_missing_after_health_pass")
            if self.calibrated_probability_lower_bound is None:
                errors.append("calibrated_lower_bound_missing_after_health_pass")
            if self.calibrated_probability_upper_bound is None:
                errors.append("calibrated_upper_bound_missing_after_health_pass")

        return len(errors) == 0, errors

    def validate_probability_domain(self) -> tuple[bool, list[str]]:
        """Validate that probabilities are in valid domain (0, 1)."""
        errors = []
        for name, value in (
            ("raw_model_probability", self.raw_model_probability),
            ("independent_model_probability", self.independent_model_probability),
            ("calibrated_probability", self.calibrated_probability),
        ):
            if value is not None:
                if not (0.0 < value < 1.0):
                    errors.append(f"{name}_outside_valid_domain_{value}")

        for name, value in (
            ("calibrated_lower_bound", self.calibrated_probability_lower_bound),
            ("calibrated_upper_bound", self.calibrated_probability_upper_bound),
        ):
            if value is not None:
                if not (0.0 <= value <= 1.0):
                    errors.append(f"{name}_outside_valid_domain_{value}")

        return len(errors) == 0, errors

    def validate_complete_package(self) -> tuple[bool, list[str]]:
        """Run all validations on the governed probability package."""
        all_errors = []

        ok, errors = self.validate_calibration()
        all_errors.extend(errors)

        ok, errors = self.validate_probability_domain()
        all_errors.extend(errors)

        ok, errors = self.validate_failure_paths()
        all_errors.extend(errors)

        return len(all_errors) == 0, all_errors
