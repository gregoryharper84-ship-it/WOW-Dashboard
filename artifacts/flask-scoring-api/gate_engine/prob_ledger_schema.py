"""
gate_engine/prob_ledger_schema.py
WOW-PATCH-2026-08-17-PROB-LEDGER-HANDOFF — Task: hydration/acquisition →
prob-ledger → live scorer handoff for MLB pitcher and WNBA player props.

Canonical, versioned ProbabilityLedgerInput contract plus typed diagnostics.

This module is a pure data-contract layer:
  - It never assigns terminal labels.
  - It never fabricates probabilities.
  - can_execute=False / PRODUCTION_AUTHORITY=False unconditional.

CONTRACT_VERSION="v1" is stamped into every ledger record built through the
sport adapters (gate_engine/wnba/prob_ledger_adapter.py and
gate_engine/mlb/pitcher_prob_ledger_adapter.py).

Design rule (root-cause of the 0-rank_eligible production replay):
  a vague PROB_LEDGER_INCOMPLETE must never be emitted when evidence was
  actually fetched.  Every validation failure is expressed as a
  SchemaValidationResult / PipelineDiagnostic that carries stage,
  contract_version, row_id, received/normalized/missing/invalid fields,
  acquisition_attempted and provider_status.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Governance constants — unconditional
# ---------------------------------------------------------------------------
can_execute              = False
PRODUCTION_AUTHORITY     = False
USER_OUTPUT_AUTHORITY    = False
TERMINAL_LABEL_AUTHORITY = False

CONTRACT_VERSION = "v1"
SUPPORTED_CONTRACT_VERSIONS = frozenset({"v1"})

# Typed market statuses (module-level constants — never added to labels.py).
MARKET_STATUS_STALE_MARKET       = "STALE_MARKET"
MARKET_STATUS_REHYDRATE_REQUIRED = "REHYDRATE_REQUIRED"
MARKET_STATUS_AVAILABLE          = "MARKET_AVAILABLE"

# Run-level contract-breach blocker string (module-level constant).
PROBABILITY_PIPELINE_CONTRACT_BREACH = "PROBABILITY_PIPELINE_CONTRACT_BREACH"

# The 7 Stage-2 probability fields (mirror of prob_ledger.STAGE2_REQUIRED_FIELDS
# — imported there, single source of truth is prob_ledger; kept as a tuple here
# to avoid a circular import at module load).
STAGE2_FIELDS = (
    "raw_probability",
    "calibrated_probability",
    "lower_bound",
    "upper_bound",
    "model_timestamp",
    "source_snapshot_id",
    "calibration_method",
)

# The 3 component guards.
COMPONENT_GUARDS = ("market_no_vig", "l10_distribution", "role_usage")

# Model-side components: everything the sporting model needs.  market_no_vig
# is deliberately NOT in this set — market readiness is a separate lane.
MODEL_COMPONENTS = ("l10_distribution", "role_usage")


@dataclass
class ProbabilityLedgerInput:
    """Canonical adapter output — one per directional row."""
    row_id: str = ""
    event_id: Optional[str] = None
    sport: str = ""
    market: str = ""
    stat_key: str = ""
    direction: str = ""
    player_id: Optional[str] = None
    team_id: Optional[str] = None
    line: Optional[float] = None
    source_timestamps: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    ttl_seconds: Optional[int] = None
    raw_evidence: dict = field(default_factory=dict)
    normalized_evidence: dict = field(default_factory=dict)
    acquisition_status: str = "NOT_ATTEMPTED"   # NOT_ATTEMPTED|ATTEMPTED|RETRIEVED|FAILED
    provider_status: dict = field(default_factory=dict)   # source -> status string

    # 7 Stage-2 probability fields (never fabricated — copied from a real model
    # output when one exists, otherwise left None and listed in missing_fields).
    raw_probability: Optional[float] = None
    calibrated_probability: Optional[float] = None
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    model_timestamp: Optional[str] = None
    source_snapshot_id: Optional[str] = None
    calibration_method: Optional[str] = None

    # 3 component guards — dicts with {name, weight, value, source} or None.
    market_no_vig: Optional[dict] = None
    l10_distribution: Optional[dict] = None
    role_usage: Optional[dict] = None

    missing_fields: list = field(default_factory=list)
    contract_version: str = CONTRACT_VERSION
    can_execute: bool = False

    def to_ledger_payload(self) -> dict[str, Any]:
        """
        Translate into the enrichment["model_probability_ledger"] dict shape
        that gate_engine/prob_ledger.py consumes (components list + Stage-2
        fields).  Components with None values are omitted, never fabricated.
        """
        components: list[dict] = []
        for comp_name in COMPONENT_GUARDS:
            comp = getattr(self, comp_name)
            if isinstance(comp, dict):
                entry = dict(comp)
                entry.setdefault("name", comp_name)
                components.append(entry)
        payload: dict[str, Any] = {
            "components":       components,
            "contract_version": self.contract_version,
            "missing_fields":   list(self.missing_fields),
            "provider_status":  dict(self.provider_status),
            "acquisition_status": self.acquisition_status,
            "can_execute":      False,
        }
        for f_name in STAGE2_FIELDS:
            val = getattr(self, f_name)
            if val is not None:
                payload[f_name] = val
        if self.line is not None:
            payload["market_snapshot_line"] = (
                (self.market_no_vig or {}).get("snapshot_line")
                if isinstance(self.market_no_vig, dict) else None
            )
        return payload


@dataclass
class SchemaValidationResult:
    stage: str
    contract_version: str
    row_id: str
    received_fields: list
    normalized_fields: list
    missing_fields: list
    invalid_fields: list
    acquisition_attempted: bool
    provider_status: dict
    valid: bool
    can_execute: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PipelineDiagnostic:
    """Typed per-row diagnostic — replaces bare PROB_LEDGER_INCOMPLETE."""
    stage: str
    contract_version: str
    row_id: str
    received_fields: list = field(default_factory=list)
    normalized_fields: list = field(default_factory=list)
    missing_fields: list = field(default_factory=list)
    invalid_fields: list = field(default_factory=list)
    acquisition_attempted: bool = False
    provider_status: dict = field(default_factory=dict)
    specialist_status: Optional[str] = None
    ledger_status: Optional[str] = None
    outlier_status: Optional[str] = None
    failure_path_status: Optional[str] = None
    market_status: Optional[str] = None
    rank_eligible: bool = False
    retryable: bool = False
    can_execute: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def to_blocker_string(self) -> str:
        return (
            f"PROB_LEDGER_SCHEMA:{self.contract_version}:stage={self.stage}:"
            f"row_id={self.row_id}:missing={','.join(self.missing_fields) or 'none'}:"
            f"invalid={','.join(self.invalid_fields) or 'none'}:"
            f"market_status={self.market_status or 'n/a'}:"
            f"retryable={self.retryable}"
        )


def validate_schema(ledger_input: Any, stage: str = "prob_ledger_schema") -> SchemaValidationResult:
    """
    Validate a ProbabilityLedgerInput (or an equivalent dict).

    Never returns a vague result: received / normalized / missing / invalid
    fields are always enumerated, and acquisition_attempted / provider_status
    show whether evidence was actually fetched.
    """
    if isinstance(ledger_input, ProbabilityLedgerInput):
        data = asdict(ledger_input)
    elif isinstance(ledger_input, dict):
        data = dict(ledger_input)
    else:
        return SchemaValidationResult(
            stage=stage,
            contract_version=CONTRACT_VERSION,
            row_id="",
            received_fields=[],
            normalized_fields=[],
            missing_fields=list(STAGE2_FIELDS) + list(COMPONENT_GUARDS),
            invalid_fields=[f"input_type:{type(ledger_input).__name__}"],
            acquisition_attempted=False,
            provider_status={},
            valid=False,
        )

    received = [k for k, v in data.items() if v not in (None, [], {}, "")]
    missing: list[str] = []
    invalid: list[str] = []

    # Contract-version enforcement: a payload carrying an unsupported version
    # is rejected as invalid — an arbitrary/old payload cannot become eligible
    # merely by carrying a version string.
    _cv = data.get("contract_version")
    if _cv is not None and str(_cv) not in SUPPORTED_CONTRACT_VERSIONS:
        invalid.append(f"contract_version:unsupported:{str(_cv)[:20]}")

    for f_name in STAGE2_FIELDS:
        val = data.get(f_name)
        if val is None or (isinstance(val, str) and not val.strip()):
            missing.append(f_name)
        elif f_name in ("raw_probability", "calibrated_probability",
                        "lower_bound", "upper_bound"):
            if isinstance(val, bool):
                invalid.append(f"{f_name}:bool_not_accepted")
            else:
                try:
                    fv = float(val)
                    if not (0.0 < fv < 1.0):
                        invalid.append(f"{f_name}:out_of_range:{fv}")
                except (TypeError, ValueError):
                    invalid.append(f"{f_name}:not_numeric:{type(val).__name__}")

    for comp_name in COMPONENT_GUARDS:
        comp = data.get(comp_name)
        if comp is None:
            missing.append(comp_name)
        elif not isinstance(comp, dict):
            invalid.append(f"{comp_name}:not_a_dict:{type(comp).__name__}")

    # Preserve adapter-recorded missing fields (e.g. STALE_MARKET marker)
    for m in data.get("missing_fields") or []:
        if m not in missing:
            missing.append(m)

    normalized = [k for k in received if k in
                  set(STAGE2_FIELDS) | set(COMPONENT_GUARDS) | {"normalized_evidence"}]

    return SchemaValidationResult(
        stage=stage,
        contract_version=str(data.get("contract_version") or CONTRACT_VERSION),
        row_id=str(data.get("row_id") or ""),
        received_fields=received,
        normalized_fields=normalized,
        missing_fields=missing,
        invalid_fields=invalid,
        acquisition_attempted=(data.get("acquisition_status") or "NOT_ATTEMPTED") != "NOT_ATTEMPTED",
        provider_status=dict(data.get("provider_status") or {}),
        valid=not missing and not invalid,
    )
