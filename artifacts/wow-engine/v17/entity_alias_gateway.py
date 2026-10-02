"""Deterministic V17 player identity reconciliation gateway.

Runtime resolution is exact-match only against the governed alias registry.
Unresolved identities are written by the database resolver to the durable audit
queue and fail closed. Fuzzy matching is intentionally absent from this module.

This is Class-B identity infrastructure. It never computes or substitutes a
sporting probability and never grants execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

CAN_EXECUTE = False
RESOLVER_RPC = "wow_resolve_player_alias"


class EntityIdentityUnresolved(RuntimeError):
    def __init__(
        self,
        *,
        source_feed: str,
        sport: str,
        raw_alias: str,
        run_id: str | None = None,
        row_key: str | None = None,
    ) -> None:
        super().__init__("REJECTED_UNRESOLVED_IDENTITY")
        self.code = "REJECTED_UNRESOLVED_IDENTITY"
        self.source_feed = source_feed
        self.sport = sport
        self.raw_alias = raw_alias
        self.run_id = run_id
        self.row_key = row_key
        self.can_execute = False

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "source_feed": self.source_feed,
            "sport": self.sport,
            "raw_alias": self.raw_alias,
            "run_id": self.run_id,
            "row_key": self.row_key,
            "probability_publishable": False,
            "can_execute": False,
        }


@dataclass(frozen=True)
class PlayerIdentityObservation:
    is_resolved: bool
    gateway_mode: str
    canonical_player_id: Optional[str]
    canonical_name: Optional[str]
    current_team_key: Optional[str]
    provider_player_id: Optional[str]
    source_feed: str
    sport: str
    raw_alias: str
    can_execute: bool = False


@dataclass(frozen=True)
class ResolvedPlayerIdentity:
    canonical_player_id: str
    canonical_name: str
    current_team_key: Optional[str]
    provider_player_id: Optional[str]
    source_feed: str
    sport: str
    raw_alias: str
    can_execute: bool = False


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


class EntityAliasGateway:
    """Exact-match reconciliation over the governed Postgres resolver."""

    def __init__(self, db: Any):
        self.db = db

    def observe_player(
        self,
        *,
        source_feed: str,
        sport: str,
        raw_alias: str,
        run_id: str | None = None,
        row_key: str | None = None,
        context_payload: Mapping[str, Any] | None = None,
    ) -> PlayerIdentityObservation:
        source = _clean(source_feed).lower()
        normalized_sport = _clean(sport).upper()
        alias = _clean(raw_alias)
        if not source or not normalized_sport or not alias:
            return PlayerIdentityObservation(
                is_resolved=False,
                gateway_mode="SHADOW",
                canonical_player_id=None,
                canonical_name=None,
                current_team_key=None,
                provider_player_id=None,
                source_feed=source,
                sport=normalized_sport,
                raw_alias=alias,
                can_execute=False,
            )

        result = self.db.rpc(
            RESOLVER_RPC,
            {
                "p_source_feed": source,
                "p_sport": normalized_sport,
                "p_raw_alias": alias,
                "p_run_id": _clean(run_id) or None,
                "p_row_key": _clean(row_key) or None,
                "p_context_payload": dict(context_payload or {}),
            },
        ).execute()
        rows = getattr(result, "data", None) or []
        row = dict(rows[0]) if rows and isinstance(rows[0], Mapping) else {}
        return PlayerIdentityObservation(
            is_resolved=row.get("is_resolved") is True and bool(row.get("canonical_player_id")),
            gateway_mode=_clean(row.get("gateway_mode")).upper() or "SHADOW",
            canonical_player_id=str(row["canonical_player_id"]) if row.get("canonical_player_id") else None,
            canonical_name=_clean(row.get("canonical_name")) or None,
            current_team_key=_clean(row.get("current_team_key")) or None,
            provider_player_id=_clean(row.get("provider_player_id")) or None,
            source_feed=source,
            sport=normalized_sport,
            raw_alias=alias,
            can_execute=False,
        )

    def resolve_player(
        self,
        *,
        source_feed: str,
        sport: str,
        raw_alias: str,
        run_id: str | None = None,
        row_key: str | None = None,
        context_payload: Mapping[str, Any] | None = None,
    ) -> ResolvedPlayerIdentity:
        source = _clean(source_feed).lower()
        normalized_sport = _clean(sport).upper()
        alias = _clean(raw_alias)
        if not source or not normalized_sport or not alias:
            raise EntityIdentityUnresolved(
                source_feed=source,
                sport=normalized_sport,
                raw_alias=alias,
                run_id=run_id,
                row_key=row_key,
            )

        observation = self.observe_player(
            source_feed=source,
            sport=normalized_sport,
            raw_alias=alias,
            run_id=run_id,
            row_key=row_key,
            context_payload=context_payload,
        )
        if not observation.is_resolved or not observation.canonical_player_id:
            raise EntityIdentityUnresolved(
                source_feed=source,
                sport=normalized_sport,
                raw_alias=alias,
                run_id=run_id,
                row_key=row_key,
            )

        return ResolvedPlayerIdentity(
            canonical_player_id=observation.canonical_player_id,
            canonical_name=observation.canonical_name or alias,
            current_team_key=observation.current_team_key,
            provider_player_id=observation.provider_player_id,
            source_feed=source,
            sport=normalized_sport,
            raw_alias=alias,
            can_execute=False,
        )


__all__ = [
    "CAN_EXECUTE",
    "EntityAliasGateway",
    "EntityIdentityUnresolved",
    "PlayerIdentityObservation",
    "RESOLVER_RPC",
    "ResolvedPlayerIdentity",
]
