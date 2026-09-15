"""Governed source-entitlement registry for V17 fitted-model development.

A source may be valid for discovery, identity, or market evidence without being
authorized for fitted-model training. Availability alone never makes a source a
sporting-probability authority. Training permission and commercial entitlement
are explicit, fail-closed runtime contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Literal

CAN_EXECUTE = False

SourceUse = Literal[
    "EVENT_MARKET_ONLY",
    "TRAINING_WITH_CREDENTIAL",
    "TRAINING_WITH_ENTITLEMENT",
    "CANDIDATE_FIRST_PARTY_UNDOCUMENTED",
    "RESEARCH_ONLY",
    "NO_APPROVED_SOURCE",
]


@dataclass(frozen=True)
class SourceEntitlement:
    source_id: str
    sports: tuple[str, ...]
    use: SourceUse
    credential_env: tuple[str, ...] = ()
    entitlement_env: str | None = None
    market_feature_allowed: bool = False
    fitted_training_allowed_when_ready: bool = False
    certification_source_review_required: bool = True
    probability_source: bool = False
    can_execute: bool = False


SOURCES: dict[str, SourceEntitlement] = {
    "THERUNDOWN": SourceEntitlement(
        "THERUNDOWN",
        ("MLB", "NFL", "NBA", "WNBA", "NCAAB", "NCAAF", "NHL", "SOCCER", "TENNIS"),
        "EVENT_MARKET_ONLY",
        credential_env=("THERUNDOWN_API_KEY", "RUNDOWN_API_KEY", "WOW_RUNDOWN_API_KEY"),
        market_feature_allowed=False,
        fitted_training_allowed_when_ready=False,
        certification_source_review_required=False,
    ),
    "BALLDONTLIE": SourceEntitlement(
        "BALLDONTLIE",
        ("NBA", "WNBA"),
        "TRAINING_WITH_CREDENTIAL",
        credential_env=("BALLDONTLIE_API_KEY", "balldontlie"),
        fitted_training_allowed_when_ready=True,
        certification_source_review_required=True,
    ),
    "CFBD": SourceEntitlement(
        "CFBD",
        ("NCAAF",),
        "TRAINING_WITH_CREDENTIAL",
        credential_env=("CFBD_API_KEY",),
        fitted_training_allowed_when_ready=True,
        certification_source_review_required=True,
    ),
    "NHL_PUBLIC_WEB_API": SourceEntitlement(
        "NHL_PUBLIC_WEB_API",
        ("NHL",),
        "CANDIDATE_FIRST_PARTY_UNDOCUMENTED",
        fitted_training_allowed_when_ready=True,
        certification_source_review_required=True,
    ),
    "FOOTBALL_DATA_ORG": SourceEntitlement(
        "FOOTBALL_DATA_ORG",
        ("SOCCER",),
        "TRAINING_WITH_ENTITLEMENT",
        credential_env=("FOOTBALL_DATA_API_KEY",),
        entitlement_env="WOW_FOOTBALL_DATA_TRAINING_ENTITLED",
        fitted_training_allowed_when_ready=True,
        certification_source_review_required=True,
    ),
    "LIVE_TENNIS_API": SourceEntitlement(
        "LIVE_TENNIS_API",
        ("TENNIS",),
        "TRAINING_WITH_ENTITLEMENT",
        credential_env=("LIVE_TENNIS_API_KEY",),
        entitlement_env="WOW_LIVE_TENNIS_COMMERCIAL_TRAINING_ENTITLED",
        fitted_training_allowed_when_ready=True,
        certification_source_review_required=True,
    ),
    "JEFF_SACKMANN_TENNIS_ARCHIVE": SourceEntitlement(
        "JEFF_SACKMANN_TENNIS_ARCHIVE",
        ("TENNIS",),
        "RESEARCH_ONLY",
        fitted_training_allowed_when_ready=False,
        certification_source_review_required=True,
    ),
    "DATA_GOLF": SourceEntitlement(
        "DATA_GOLF",
        ("PGA",),
        "TRAINING_WITH_ENTITLEMENT",
        credential_env=("DATA_GOLF_API_KEY",),
        entitlement_env="WOW_DATA_GOLF_COMMERCIAL_TRAINING_ENTITLED",
        fitted_training_allowed_when_ready=True,
        certification_source_review_required=True,
    ),
    "BOXING_DATA_API": SourceEntitlement(
        "BOXING_DATA_API",
        ("BOXING",),
        "TRAINING_WITH_ENTITLEMENT",
        credential_env=("BOXING_DATA_API_KEY", "RAPIDAPI_KEY"),
        entitlement_env="WOW_BOXING_DATA_TRAINING_ENTITLED",
        fitted_training_allowed_when_ready=True,
        certification_source_review_required=True,
    ),
    "UFCSTATS_PUBLIC": SourceEntitlement(
        "UFCSTATS_PUBLIC",
        ("MMA",),
        "CANDIDATE_FIRST_PARTY_UNDOCUMENTED",
        fitted_training_allowed_when_ready=True,
        certification_source_review_required=True,
    ),
    "NCAAB_APPROVED_STATS": SourceEntitlement(
        "NCAAB_APPROVED_STATS",
        ("NCAAB",),
        "NO_APPROVED_SOURCE",
        fitted_training_allowed_when_ready=False,
        certification_source_review_required=True,
    ),
}


@dataclass(frozen=True)
class SourceReadiness:
    source_id: str
    ready_for_candidate_training: bool
    blockers: tuple[str, ...]
    probability_source: bool = False
    can_execute: bool = False


def _truthy_env(name: str | None) -> bool:
    if not name:
        return True
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "approved"}


def _credential_present(names: tuple[str, ...]) -> bool:
    return not names or any(str(os.getenv(name, "")).strip() for name in names)


def source_readiness(source_id: str) -> SourceReadiness:
    source = SOURCES.get(str(source_id or "").strip().upper())
    if source is None:
        return SourceReadiness(
            source_id=str(source_id or "").strip().upper(),
            ready_for_candidate_training=False,
            blockers=("MODEL_SOURCE_UNCLASSIFIED",),
        )
    blockers: list[str] = []
    if source.probability_source:
        blockers.append("MODEL_SOURCE_PROBABILITY_REUSE_FORBIDDEN")
    if not source.fitted_training_allowed_when_ready:
        blockers.append("MODEL_SOURCE_NOT_AUTHORIZED_FOR_FITTED_TRAINING")
    if source.credential_env and not _credential_present(source.credential_env):
        blockers.append("MODEL_SOURCE_CREDENTIAL_MISSING")
    if source.entitlement_env and not _truthy_env(source.entitlement_env):
        blockers.append("MODEL_SOURCE_TRAINING_ENTITLEMENT_NOT_CONFIRMED")
    return SourceReadiness(
        source_id=source.source_id,
        ready_for_candidate_training=not blockers,
        blockers=tuple(sorted(set(blockers))),
    )


def sport_source_readiness(sport: str) -> dict[str, SourceReadiness]:
    wanted = str(sport or "").strip().upper()
    return {
        source_id: source_readiness(source_id)
        for source_id, source in SOURCES.items()
        if wanted in source.sports
    }


def assert_source_registry() -> None:
    for key, source in SOURCES.items():
        if source.source_id != key:
            raise RuntimeError(f"MODEL_SOURCE_IDENTITY_DRIFT:{key}")
        if source.probability_source:
            raise RuntimeError(f"MODEL_SOURCE_CANNOT_BE_PROBABILITY_AUTHORITY:{key}")
        if source.market_feature_allowed:
            raise RuntimeError(f"MARKET_FEATURE_NOT_ALLOWED_IN_FITTED_MODEL:{key}")
        if source.can_execute:
            raise RuntimeError(f"MODEL_SOURCE_EXECUTION_FORBIDDEN:{key}")
        if source.use == "RESEARCH_ONLY" and source.fitted_training_allowed_when_ready:
            raise RuntimeError(f"RESEARCH_SOURCE_CANNOT_TRAIN_PRODUCTION_MODEL:{key}")
        if source.use == "NO_APPROVED_SOURCE" and source.fitted_training_allowed_when_ready:
            raise RuntimeError(f"UNAPPROVED_SOURCE_CANNOT_TRAIN_MODEL:{key}")


assert_source_registry()


__all__ = [
    "CAN_EXECUTE",
    "SOURCES",
    "SourceEntitlement",
    "SourceReadiness",
    "assert_source_registry",
    "source_readiness",
    "sport_source_readiness",
]
