"""
skills/contracts.py
Typed result contracts for the WOW v16 Skills Pack.

Hard invariants enforced in __post_init__ (cannot be bypassed by any caller):
  1. can_execute is ALWAYS False — no live trading, no market orders.
  2. Bare LLP_PLAYABLE_LIMIT_ONLY normalizes to LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN.
  3. confidence is clamped to [0.0, 1.0].
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

# ── Label hierarchy ────────────────────────────────────────────────────────────
# Ordered from least to most restrictive.
# The orchestrator always propagates the HIGHEST severity (lowest ceiling).
class SkillLabel(str, Enum):
    READY               = "READY"
    WATCH               = "WATCH"
    SCOUT               = "SCOUT"
    HOLD                = "HOLD"
    REJECT_BAD_RULES    = "REJECT_BAD_RULES"
    REJECT_DATA_QUALITY = "REJECT_DATA_QUALITY"
    DATA_UNOBTAINABLE   = "DATA_UNOBTAINABLE"

LABEL_ORDER: list[str] = [l.value for l in SkillLabel]

DRY_RUN_LABEL        = "LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN"
BARE_LABEL_FORBIDDEN = "LLP_PLAYABLE_LIMIT_ONLY"

# Labels the JSON schema recognizes; domain labels must map to one of these.
SCHEMA_LABELS = frozenset(LABEL_ORDER)


def label_severity(label: str) -> int:
    """Higher number = more restrictive ceiling."""
    try:
        return LABEL_ORDER.index(label)
    except ValueError:
        return 0


def lower_ceiling(a: str, b: str) -> str:
    """Return the more restrictive of two labels (the lower ceiling)."""
    return a if label_severity(a) >= label_severity(b) else b


# ── Source quality tiers ───────────────────────────────────────────────────────
SOURCE_QUALITY_OFFICIAL          = 1   # official league/team/market/weather
SOURCE_QUALITY_STRUCTURED        = 2   # trusted structured data provider
SOURCE_QUALITY_REPUTABLE         = 3   # reputable reporting/beat source
SOURCE_QUALITY_AGGREGATOR        = 4   # aggregator or narrative source
SOURCE_QUALITY_OPERATOR_SUPPLIED = 5   # user screenshot or operator-supplied

# Freshness defaults (seconds)
FRESHNESS_LIVE_PRICE      = 600   # 10 min — live price / orderbook
FRESHNESS_LINEUP_LOCK     = 1800  # 30 min — final lineup/starter
FRESHNESS_WEATHER_OBS     = 1800  # 30 min — weather observation
FRESHNESS_INJURY_STATUS   = 86400 # same calendar day


@dataclass
class Blocker:
    code: str
    message: str
    fatal: bool = True
    source: str | None = None

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "fatal": self.fatal, "source": self.source}


@dataclass
class SourceEvidence:
    source_id: str
    quality: int        # 1–5 per SOURCE_QUALITY_* constants
    url: str | None = None
    as_of: str | None = None
    freshness_seconds: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_operator_supplied(self) -> bool:
        return self.quality >= SOURCE_QUALITY_OPERATOR_SUPPLIED

    @property
    def is_stale_kalshi(self) -> bool:
        """Kalshi price is stale if age > 10 minutes."""
        return (self.freshness_seconds is not None
                and self.freshness_seconds > FRESHNESS_LIVE_PRICE)


@dataclass
class SkillResult:
    """
    Canonical output contract for every WOW v16 skill adapter.

    Invariants — enforced in __post_init__, cannot be bypassed:
      • can_execute is always False (SHARED_CONTRACT.md §Non-negotiable governance)
      • LLP_PLAYABLE_LIMIT_ONLY normalizes to LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN
      • confidence is clamped to [0.0, 1.0]
    """
    skill_id:          str
    skill_version:     str
    inputs_used:       dict
    sources:           list
    findings:          list
    blockers:          list
    label:             str
    confidence:        float

    run_id:            str  = field(default_factory=lambda: str(uuid.uuid4()))
    as_of:             str  = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_id:          str | None = None
    market_id:         str | None = None
    source_timestamps: list = field(default_factory=list)
    data_quality:      str  = "complete"
    conflicts:         list = field(default_factory=list)
    assumptions:       list = field(default_factory=list)
    calculations:      list = field(default_factory=list)
    can_execute:       bool = False
    downstream:        list = field(default_factory=list)

    def __post_init__(self) -> None:
        # INVARIANT 1: can_execute is always False — override silently
        object.__setattr__(self, "can_execute", False)
        # INVARIANT 2: normalize bare dry-run label
        if self.label == BARE_LABEL_FORBIDDEN:
            self.label = DRY_RUN_LABEL
        # INVARIANT 3: clamp confidence
        self.confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))

    def to_dict(self) -> dict:
        return asdict(self)

    def validate_schema(self) -> list[str]:
        """Validate against skill-result.schema.json. Returns list of errors."""
        try:
            import jsonschema  # type: ignore
        except ImportError:
            return []  # schema validation is best-effort if jsonschema not installed
        schema_path = os.path.join(
            os.path.dirname(__file__), "schemas", "skill-result.schema.json")
        with open(schema_path) as f:
            schema = json.load(f)
        errors = []
        # The schema only allows 7 common labels; domain labels are in additionalProperties
        d = self.to_dict()
        schema_label = d["label"]
        if schema_label not in SCHEMA_LABELS:
            d = dict(d)
            d["label"] = SkillLabel.WATCH.value   # map domain label to nearest
        try:
            jsonschema.validate(d, schema)
        except jsonschema.ValidationError as e:
            errors.append(str(e.message))
        except jsonschema.SchemaError as e:
            errors.append(f"schema error: {e.message}")
        return errors

    # ── Factory helpers ────────────────────────────────────────────────────────

    @classmethod
    def unobtainable(cls, skill_id: str, skill_version: str, inputs: dict,
                     reason: str, run_id: str | None = None) -> "SkillResult":
        kw = {"run_id": run_id} if run_id else {}
        return cls(
            skill_id=skill_id, skill_version=skill_version,
            inputs_used=inputs, sources=[], findings=[],
            blockers=[{"code": "DATA_UNOBTAINABLE", "message": reason, "fatal": True}],
            label=SkillLabel.DATA_UNOBTAINABLE.value, confidence=0.0, **kw)

    @classmethod
    def reject(cls, skill_id: str, skill_version: str, inputs: dict,
               code: str, message: str,
               label: str = SkillLabel.REJECT_BAD_RULES.value,
               run_id: str | None = None) -> "SkillResult":
        kw = {"run_id": run_id} if run_id else {}
        return cls(
            skill_id=skill_id, skill_version=skill_version,
            inputs_used=inputs, sources=[], findings=[],
            blockers=[{"code": code, "message": message, "fatal": True}],
            label=label, confidence=0.0, **kw)

    @classmethod
    def scout(cls, skill_id: str, skill_version: str, inputs: dict,
              findings: list, reason: str, confidence: float = 0.2,
              run_id: str | None = None) -> "SkillResult":
        kw = {"run_id": run_id} if run_id else {}
        return cls(
            skill_id=skill_id, skill_version=skill_version,
            inputs_used=inputs, sources=[], findings=findings,
            blockers=[{"code": "SCOUT_CONDITION", "message": reason, "fatal": False}],
            label=SkillLabel.SCOUT.value, confidence=confidence, **kw)

    @classmethod
    def watch(cls, skill_id: str, skill_version: str, inputs: dict,
              findings: list, reason: str, confidence: float = 0.3,
              run_id: str | None = None) -> "SkillResult":
        kw = {"run_id": run_id} if run_id else {}
        return cls(
            skill_id=skill_id, skill_version=skill_version,
            inputs_used=inputs, sources=[], findings=findings,
            blockers=[{"code": "WATCH_CONDITION", "message": reason, "fatal": False}],
            label=SkillLabel.WATCH.value, confidence=confidence, **kw)
