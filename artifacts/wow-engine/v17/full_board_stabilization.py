"""V17 cross-sport full-board stabilization contracts.

This module closes the presentation/orchestration gaps exposed by the 2026-09-19
all-sports winner run without changing sporting model math, certification, or
terminal authority.

Key invariants:
- discovery rows never silently disappear;
- model capability, market acquisition, event status, governance, and ranking are
  independent axes;
- MODEL_UNAVAILABLE is reserved for absent certified capability/artifacts;
- missing/not-yet-available candidate inputs remain MODEL_INPUTS_INSUFFICIENT;
- a completed numerical model package either completes the publication chain or
  exposes the first exact typed blocker;
- market acquisition failure never erases completed sporting probability;
- can_execute is always false.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

CAN_EXECUTE = False
GLOBAL_TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
MODEL_INPUTS_INSUFFICIENT = "MODEL_INPUTS_INSUFFICIENT"
MODEL_SCORER_FAILED = "MODEL_SCORER_FAILED"
MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
EVENT_ALREADY_STARTED = "EVENT_ALREADY_STARTED"
MARKET_DATA_UNOBTAINABLE = "MARKET_DATA_UNOBTAINABLE"
RANK_ELIGIBLE = "RANK_ELIGIBLE"
HELD = "HELD"

TERMINAL_BUCKETS = (
    RANK_ELIGIBLE,
    EVENT_ALREADY_STARTED,
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_UNAVAILABLE,
    MODEL_SCORER_FAILED,
    MODEL_OUTPUT_INVALID,
    HELD,
    "IDENTITY_OR_STATUS_BLOCKED",
    "DUPLICATE_WITH_CANONICAL_MAPPING",
    "OTHER_TYPED_BLOCKER",
)


@dataclass(frozen=True)
class MarketAcquisitionStatus:
    provider: str
    status: str
    provider_code: str | None
    market_snapshot_present: bool
    auth_ok: bool | None
    blocker: str | None
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "market_acquisition_status": self.status,
            "provider_code": self.provider_code,
            "market_snapshot_present": self.market_snapshot_present,
            "auth_ok": self.auth_ok,
            "blocker": self.blocker,
            "can_execute": False,
        }


@dataclass(frozen=True)
class PublicationChainAudit:
    status: str
    rank_eligible: bool
    first_blocker: str | None
    stages: dict[str, bool]
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "rank_eligible": self.rank_eligible,
            "first_blocker": self.first_blocker,
            "stages": dict(self.stages),
            "global_terminal_authority": GLOBAL_TERMINAL_AUTHORITY,
            "can_execute": False,
        }


@dataclass
class FullBoardRow:
    candidate_id: str
    sport: str
    league: str | None = None
    official_event_id: str | None = None
    provider_event_ids: dict[str, str] = field(default_factory=dict)
    event_key: str | None = None
    disposition: str | None = None
    blocker: str | None = None
    model_status: str | None = None
    market_acquisition_status: str | None = None
    event_status: str | None = None
    governance_status: str | None = None
    rank_eligible: bool = False
    probability_publishable: bool = False
    calibrated_probability: float | None = None
    calibrated_lower_bound: float | None = None
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "sport": self.sport,
            "league": self.league,
            "official_event_id": self.official_event_id,
            "provider_event_ids": dict(self.provider_event_ids),
            "event_key": self.event_key,
            "disposition": self.disposition,
            "blocker": self.blocker,
            "model_status": self.model_status,
            "market_acquisition_status": self.market_acquisition_status,
            "event_status": self.event_status,
            "governance_status": self.governance_status,
            "rank_eligible": self.rank_eligible,
            "probability_publishable": self.probability_publishable,
            "calibrated_probability": self.calibrated_probability,
            "calibrated_lower_bound": self.calibrated_lower_bound,
            "can_execute": False,
        }


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def classify_market_acquisition(
    provider: str,
    *,
    provider_code: str | None,
    snapshot: Any = None,
) -> MarketAcquisitionStatus:
    """Normalize provider transport/auth state without touching model status."""
    name = _upper(provider)
    code = _upper(provider_code)
    present = snapshot not in (None, {}, [], "")

    if present and code in {"", "MARKET_EVIDENCE_FETCH_OK", "PASS", "OK"}:
        return MarketAcquisitionStatus(name, "PASS", provider_code, True, True, None)

    if code.endswith("_HTTP_401") or code.endswith("_HTTP_403") or "AUTH_FAILED" in code:
        return MarketAcquisitionStatus(name, "AUTH_FAILED", provider_code, False, False, code)
    if "CREDENTIAL_UNCONFIGURED" in code:
        return MarketAcquisitionStatus(name, "CREDENTIAL_UNCONFIGURED", provider_code, False, False, code)
    if "HTTP_429" in code or "RATE_LIMIT" in code:
        return MarketAcquisitionStatus(name, "RATE_LIMITED", provider_code, False, True, code)
    if code in {"", "NONE", "NO_MARKET", "DATA_UNOBTAINABLE", MARKET_DATA_UNOBTAINABLE}:
        return MarketAcquisitionStatus(name, MARKET_DATA_UNOBTAINABLE, provider_code, False, None, code or None)
    if present:
        return MarketAcquisitionStatus(name, "PASS_WITH_PROVIDER_WARNING", provider_code, True, None, code)
    return MarketAcquisitionStatus(name, MARKET_DATA_UNOBTAINABLE, provider_code, False, None, code or None)


def canonical_event_key(
    *,
    sport: str,
    league: str | None,
    official_event_id: str | None,
    scheduled_start_utc: str | None = None,
) -> str | None:
    """Build one provider-neutral key only after official identity is resolved."""
    official = str(official_event_id or "").strip()
    if not official:
        return None
    parts = [_upper(sport), _upper(league), official]
    if scheduled_start_utc:
        parts.append(str(scheduled_start_utc).strip())
    return ":".join(part for part in parts if part)


def canonicalize_provider_identity(
    *,
    sport: str,
    league: str | None,
    official_event_id: str | None,
    provider_event_ids: Mapping[str, Any] | None = None,
    scheduled_start_utc: str | None = None,
) -> dict[str, Any]:
    """Keep provider ids as aliases; never promote one into official identity by guess."""
    aliases = {
        str(provider).upper(): str(value)
        for provider, value in (provider_event_ids or {}).items()
        if value not in (None, "")
    }
    key = canonical_event_key(
        sport=sport,
        league=league,
        official_event_id=official_event_id,
        scheduled_start_utc=scheduled_start_utc,
    )
    if key is None:
        return {
            "status": "IDENTITY_UNRESOLVED",
            "canonical_event_key": None,
            "official_event_id": None,
            "provider_event_ids": aliases,
            "blocker": "OFFICIAL_EVENT_ID_REQUIRED_FOR_CANONICALIZATION",
            "can_execute": False,
        }
    return {
        "status": "PASS",
        "canonical_event_key": key,
        "official_event_id": str(official_event_id),
        "provider_event_ids": aliases,
        "blocker": None,
        "can_execute": False,
    }


def lineup_pending_status(
    *,
    sport: str,
    missing_inputs: Sequence[str],
    expected_refresh_at: str | None = None,
) -> dict[str, Any]:
    """Represent not-yet-published lineups as retryable input insufficiency."""
    return {
        "sport": _upper(sport),
        "model_status": MODEL_INPUTS_INSUFFICIENT,
        "input_availability": "NOT_YET_AVAILABLE",
        "missing_inputs": list(missing_inputs),
        "retry_recommended": True,
        "expected_refresh_at": expected_refresh_at,
        "permanent_failure": False,
        "rank_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _legacy_final_refresh(row: Mapping[str, Any]) -> tuple[bool | None, str | None]:
    """Read only explicit legacy refresh proof; event state is never proof."""
    values: list[bool] = []
    if "final_refresh_passed" in row:
        values.append(row.get("final_refresh_passed") is True)
    if "final_refresh_status" in row:
        values.append(_upper(row.get("final_refresh_status")) in {"PASS", "COMPLETE"})
    if not values:
        return None, None
    if len(set(values)) > 1:
        return False, "FINAL_REFRESH_EVIDENCE_CONFLICT"
    return values[0], None if values[0] else "FINAL_REFRESH_NOT_COMPLETE"


def _final_refresh_state(row: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Resolve canonical nested refresh proof, failing closed on ambiguity.

    ``llp_governance.final_refresh`` is authoritative when present. Top-level
    legacy proof is consulted only when that nested key is absent, and a
    PREGAME/SCHEDULED event state never substitutes for an actual refresh.
    """
    governance_present = "llp_governance" in row
    governance = row.get("llp_governance")
    if governance_present and not isinstance(governance, Mapping):
        return False, "LLP_GOVERNANCE_MALFORMED"

    if isinstance(governance, Mapping) and "final_refresh" in governance:
        refresh = governance.get("final_refresh")
        if not isinstance(refresh, Mapping):
            return False, "FINAL_REFRESH_EVIDENCE_MALFORMED"
        status = _upper(refresh.get("final_refresh_status") or refresh.get("status"))
        blockers = refresh.get("reasons") or refresh.get("blockers") or []
        if (
            not status
            or "can_execute" not in refresh
            or not isinstance(blockers, (list, tuple))
            or (
                "probability_invalidated" in refresh
                and not isinstance(refresh.get("probability_invalidated"), bool)
            )
            or (
                "rerun_required" in refresh
                and not isinstance(refresh.get("rerun_required"), bool)
            )
        ):
            return False, "FINAL_REFRESH_EVIDENCE_MALFORMED"
        nested_ok = bool(
            status in {"PASS", "COMPLETE"}
            and not blockers
            and refresh.get("probability_invalidated") is not True
            and refresh.get("rerun_required") is not True
            and refresh.get("can_execute") is False
        )
        legacy, legacy_blocker = _legacy_final_refresh(row)
        if legacy_blocker == "FINAL_REFRESH_EVIDENCE_CONFLICT":
            return False, legacy_blocker
        if legacy is not None and legacy != nested_ok:
            return False, "FINAL_REFRESH_EVIDENCE_CONFLICT"
        return nested_ok, None if nested_ok else "FINAL_REFRESH_NOT_COMPLETE"

    legacy, blocker = _legacy_final_refresh(row)
    if legacy is None:
        return False, "FINAL_REFRESH_NOT_COMPLETE"
    return legacy, blocker


def publication_chain_audit(row: Mapping[str, Any]) -> PublicationChainAudit:
    """Explain the first failed sporting-publication stage without guessing.

    The audit is diagnostic only. It never upgrades a row to rank eligible unless
    every required boolean/status already proves completion in the supplied row.
    """
    calibration_ok = bool(
        row.get("dynamic_calibration_complete") is True
        or _upper(row.get("calibration_status")) in {"PASS", "COMPLETE"}
    )
    package_ok = bool(
        row.get("probability_package_valid") is True
        or _upper(row.get("probability_validity_status")) in {"PASS", "COMPLETE"}
    )
    probability_audit_ok = bool(
        row.get("probability_audit_passed") is True
        or _upper(row.get("llp_probability_audit_result")) in {
            "PASS", "PASS_PROBABILITY_AUDIT"
        }
    )
    event_governor_ok = bool(
        row.get("event_governor_complete") is True
        or _upper(row.get("event_mutex_status")) == "PASS"
    )
    final_refresh_ok, final_refresh_blocker = _final_refresh_state(row)
    terminal_ok = bool(
        _upper(row.get("global_terminal_authority") or row.get("terminal_authority"))
        in {GLOBAL_TERMINAL_AUTHORITY, ""}
        and row.get("can_execute") is False
    )
    numeric_ok = all(
        row.get(field) is not None
        for field in ("calibrated_probability", "calibrated_lower_bound")
    ) or all(
        row.get(field) is not None
        for field in ("calibrated_home_probability", "calibrated_home_lower_bound")
    )

    stages = {
        "probability_package_valid": package_ok,
        "dynamic_calibration_complete": calibration_ok,
        "probability_audit_passed": probability_audit_ok,
        "event_governor_complete": event_governor_ok,
        "numerical_bounds_present": numeric_ok,
        "final_refresh_passed": final_refresh_ok,
        "terminal_authority_preserved": terminal_ok,
    }
    blocker_by_stage = {
        "probability_package_valid": "PROBABILITY_PACKAGE_NOT_VALID",
        "dynamic_calibration_complete": "DYNAMIC_CALIBRATION_NOT_COMPLETE",
        "probability_audit_passed": "PROBABILITY_AUDIT_NOT_COMPLETE",
        "event_governor_complete": "EVENT_GOVERNOR_NOT_COMPLETE",
        "numerical_bounds_present": "CALIBRATED_PROBABILITY_OR_LOWER_BOUND_MISSING",
        "final_refresh_passed": final_refresh_blocker or "FINAL_REFRESH_NOT_COMPLETE",
        "terminal_authority_preserved": "TERMINAL_AUTHORITY_OR_EXECUTION_INVARIANT_NOT_PROVEN",
    }
    first = next((blocker_by_stage[name] for name, ok in stages.items() if not ok), None)
    already_ranked = row.get("rank_eligible") is True
    publishable = row.get("probability_publishable") is True
    complete = first is None and already_ranked and publishable
    if complete:
        return PublicationChainAudit("PASS", True, None, stages)
    if first is None:
        first = "RANK_ELIGIBLE_OR_PROBABILITY_PUBLISHABLE_NOT_PROVEN"
    return PublicationChainAudit("BLOCKED", False, first, stages)


def classify_terminal_disposition(row: Mapping[str, Any]) -> str:
    """Map one final row to one exact reconciliation bucket."""
    explicit = _upper(row.get("disposition"))
    if explicit in TERMINAL_BUCKETS:
        return explicit
    event_status = _upper(row.get("event_status") or row.get("official_event_status"))
    if event_status in {"STARTED", "LIVE", "IN_PROGRESS", "FINAL", "FINISHED"}:
        return EVENT_ALREADY_STARTED
    model_status = _upper(row.get("model_status") or row.get("code") or row.get("terminal_status"))
    if model_status in {MODEL_INPUTS_INSUFFICIENT, MODEL_UNAVAILABLE, MODEL_SCORER_FAILED, MODEL_OUTPUT_INVALID}:
        return model_status
    if row.get("rank_eligible") is True and row.get("probability_publishable") is True:
        return RANK_ELIGIBLE
    if _upper(row.get("status")) in {"HOLD", "HELD", "WATCH", "MODEL_QUALIFIED_HOLD"}:
        return HELD
    if "IDENTITY" in model_status or "STATUS" in model_status:
        return "IDENTITY_OR_STATUS_BLOCKED"
    return "OTHER_TYPED_BLOCKER"


def reconcile_full_board(
    discovered_rows: Iterable[Mapping[str, Any]],
    final_rows: Iterable[Mapping[str, Any]],
    *,
    inventoried_sports: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Preserve every discovered row and prove exact-once termination.

    ``candidate_id`` is mandatory for reconciliation. Missing final dispositions do
    not vanish; they become an explicit internal blocker so publication can fail
    closed while the user still sees the discovered row.
    """
    discovered: dict[str, dict[str, Any]] = {}
    duplicate_ids: list[str] = []
    for raw in discovered_rows:
        row = dict(raw)
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError("FULL_BOARD_DISCOVERY_ROW_MISSING_CANDIDATE_ID")
        if candidate_id in discovered:
            duplicate_ids.append(candidate_id)
            continue
        row["candidate_id"] = candidate_id
        row["can_execute"] = False
        discovered[candidate_id] = row

    finals: dict[str, dict[str, Any]] = {}
    duplicate_terminal_ids: list[str] = []
    for raw in final_rows:
        row = dict(raw)
        candidate_id = str(row.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError("FULL_BOARD_FINAL_ROW_MISSING_CANDIDATE_ID")
        if candidate_id in finals:
            duplicate_terminal_ids.append(candidate_id)
            continue
        row["candidate_id"] = candidate_id
        row["can_execute"] = False
        finals[candidate_id] = row

    reconciled: list[dict[str, Any]] = []
    for candidate_id, discovery in discovered.items():
        terminal = finals.get(candidate_id)
        if terminal is None:
            terminal = {
                "candidate_id": candidate_id,
                "sport": discovery.get("sport"),
                "league": discovery.get("league"),
                "status": "INTERNAL_RECONCILIATION_BLOCKED",
                "code": "DISCOVERED_ROW_MISSING_TERMINAL_DISPOSITION",
                "blocker": "DISCOVERED_ROW_MISSING_TERMINAL_DISPOSITION",
                "model_status": None,
                "rank_eligible": False,
                "probability_publishable": False,
                "can_execute": False,
            }
        merged = {**discovery, **terminal}
        merged["disposition"] = classify_terminal_disposition(merged)
        merged["can_execute"] = False
        reconciled.append(merged)

    orphan_terminal_ids = sorted(set(finals).difference(discovered))
    counts = Counter(row["disposition"] for row in reconciled)

    sport_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in reconciled:
        sport_rows[_upper(row.get("sport"))].append(row)

    inventory = [_upper(sport) for sport in (inventoried_sports or ()) if _upper(sport)]
    for sport in sorted(sport_rows):
        if sport and sport not in inventory:
            inventory.append(sport)

    sport_coverage: list[dict[str, Any]] = []
    for sport in inventory:
        rows = sport_rows.get(sport, [])
        if not rows:
            coverage_status = "NO_CURRENT_PREGAME_EVENTS"
        elif any(row.get("rank_eligible") is True for row in rows):
            coverage_status = "CANDIDATES_FOUND_AND_ROUTED"
        elif all(_upper(row.get("model_status") or row.get("code")) == MODEL_UNAVAILABLE for row in rows):
            coverage_status = "MODEL_CAPABILITY_UNAVAILABLE"
        elif any(_upper(row.get("model_status") or row.get("code")) == MODEL_INPUTS_INSUFFICIENT for row in rows):
            coverage_status = "MODEL_INPUTS_INSUFFICIENT"
        else:
            coverage_status = "SOURCE_OR_STATUS_BLOCKED"
        sport_coverage.append({
            "sport": sport,
            "coverage_status": coverage_status,
            "rows_discovered": len(rows),
            "rows_rank_eligible": sum(1 for row in rows if row.get("rank_eligible") is True),
            "dispositions": dict(Counter(row["disposition"] for row in rows)),
            "can_execute": False,
        })

    exact_once_ok = not duplicate_terminal_ids and not orphan_terminal_ids and len(reconciled) == len(discovered)
    missing_terminal = [
        row["candidate_id"]
        for row in reconciled
        if row.get("code") == "DISCOVERED_ROW_MISSING_TERMINAL_DISPOSITION"
    ]
    exact_once_ok = exact_once_ok and not missing_terminal

    return {
        "rows_discovered": len(discovered),
        "rows_terminal": len(reconciled),
        "rows_by_disposition": dict(counts),
        "duplicate_discovery_candidate_ids": sorted(set(duplicate_ids)),
        "duplicate_terminal_candidate_ids": sorted(set(duplicate_terminal_ids)),
        "orphan_terminal_candidate_ids": orphan_terminal_ids,
        "missing_terminal_candidate_ids": missing_terminal,
        "sport_coverage": sport_coverage,
        "rows": reconciled,
        "reconciliation_status": "PASS" if exact_once_ok else "FAIL",
        "publication_complete": exact_once_ok,
        "global_terminal_authority": GLOBAL_TERMINAL_AUTHORITY,
        "can_execute": False,
    }


def capability_matrix() -> dict[str, Any]:
    """Expose catalog, runtime registration, and certification before scoring."""
    from v17.team_event_bridge_runtime import team_event_bridge_health

    health = team_event_bridge_health()
    return {
        "runtime_generation": "V17_ACTIVE",
        "registered_models": [sport for sport, state in health.items() if state.get("registered")],
        "sports": health,
        "global_terminal_authority": GLOBAL_TERMINAL_AUTHORITY,
        "can_execute": False,
    }


def compact_event_page(
    events: Sequence[Mapping[str, Any]],
    *,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """Bound large scoreboard/discovery payloads to identity-only pages.

    This helper intentionally returns only the fields needed to route/hydrate an
    event later, preventing all-at-once upstream scoreboard payloads from becoming
    the transport bottleneck.
    """
    if page < 1 or page_size < 1:
        raise ValueError("INVALID_PAGINATION")
    start = (page - 1) * page_size
    stop = start + page_size
    items: list[dict[str, Any]] = []
    for raw in events[start:stop]:
        items.append({
            "provider_event_id": raw.get("provider_event_id") or raw.get("id"),
            "official_event_id": raw.get("official_event_id"),
            "sport": raw.get("sport"),
            "league": raw.get("league"),
            "scheduled_start_utc": raw.get("scheduled_start_utc") or raw.get("start_time"),
            "home_team": raw.get("home_team"),
            "away_team": raw.get("away_team"),
            "event_status": raw.get("event_status") or raw.get("status"),
        })
    total = len(events)
    return {
        "page": page,
        "page_size": page_size,
        "total_events": total,
        "has_more": stop < total,
        "events": items,
        "can_execute": False,
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "CAN_EXECUTE",
    "GLOBAL_TERMINAL_AUTHORITY",
    "MarketAcquisitionStatus",
    "PublicationChainAudit",
    "FullBoardRow",
    "capability_matrix",
    "canonical_event_key",
    "canonicalize_provider_identity",
    "classify_market_acquisition",
    "classify_terminal_disposition",
    "compact_event_page",
    "lineup_pending_status",
    "publication_chain_audit",
    "reconcile_full_board",
    "utc_now_iso",
]
