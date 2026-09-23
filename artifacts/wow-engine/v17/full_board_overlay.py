"""Cross-sport Full Board publication overlay for WOW V17.

The underlying discovery/scoring module already preserves discovered rows. This
overlay makes that preservation part of the returned run contract: every sport
gets a coverage status, every row exposes independent model/market/event/
governance axes, and any numeric-but-not-ranked row exposes the first publication
chain blocker.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from v17.full_board_stabilization import capability_matrix, publication_chain_audit, reconcile_full_board

CAN_EXECUTE = False
FULL_BOARD_STAGES = (
    "DISCOVERED",
    "MODEL_CAPABILITY_CHECK",
    "MODEL_ATTEMPT",
    "GOVERNANCE",
    "FINAL",
)


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _candidate_id(row: Mapping[str, Any]) -> str:
    return str(
        row.get("candidate_id")
        or row.get("event_key")
        or row.get("official_event_id")
        or ""
    ).strip()


def _market_status(detail: Mapping[str, Any]) -> str | None:
    direct = detail.get("market_acquisition_status") or detail.get("market_status")
    if direct:
        return str(direct)
    envelope = detail.get("candidate_envelope")
    if isinstance(envelope, Mapping):
        value = envelope.get("market_acquisition_status") or envelope.get("market_status")
        if value:
            return str(value)
    market = detail.get("market_evidence")
    if isinstance(market, Mapping):
        value = market.get("market_acquisition_status") or market.get("status")
        if value:
            return str(value)
    return None


def _governance_status(detail: Mapping[str, Any]) -> str | None:
    direct = detail.get("governance_status") or detail.get("terminal_label") or detail.get("terminal_status")
    if direct:
        return str(direct)
    governance = detail.get("llp_governance")
    if isinstance(governance, Mapping):
        value = governance.get("status") or governance.get("terminal_label")
        if value:
            return str(value)
    return None


def _first_numeric(detail: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = detail.get(name)
        if value is not None:
            return value
    return None


def _flat_terminal_row(row: Mapping[str, Any]) -> dict[str, Any]:
    detail = dict(row.get("detail") or {}) if isinstance(row.get("detail"), Mapping) else {}
    candidate_id = _candidate_id(row)
    result = {
        "candidate_id": candidate_id,
        "sport": row.get("sport"),
        "league": row.get("league"),
        "official_event_id": row.get("official_event_id"),
        "event_key": row.get("event_key"),
        "event_status": row.get("event_status"),
        "source_bucket": row.get("bucket"),
        "model_status": row.get("model_status") or detail.get("model_status") or detail.get("code"),
        "market_acquisition_status": _market_status(detail),
        "governance_status": _governance_status(detail),
        "rank_eligible": row.get("rank_eligible") is True,
        "probability_publishable": row.get("probability_publishable") is True,
        "calibrated_probability": _first_numeric(
            detail,
            "calibrated_probability",
            "calibrated_home_probability",
            "governed_calibrated_probability",
        ),
        "calibrated_lower_bound": _first_numeric(
            detail,
            "calibrated_lower_bound",
            "calibrated_home_lower_bound",
            "governed_lower_bound",
        ),
        "blocker": detail.get("blocker") or detail.get("blocker_code") or detail.get("reason"),
        "can_execute": False,
    }
    return result


def _discovery_rows(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        candidate_id = _candidate_id(row)
        if not candidate_id:
            candidate_id = f"UNRESOLVED:{len(out)}"
        out.append({
            "candidate_id": candidate_id,
            "sport": row.get("sport"),
            "league": row.get("league"),
            "official_event_id": row.get("official_event_id"),
            "event_key": row.get("event_key"),
            "event_status": row.get("event_status"),
            "can_execute": False,
        })
    return out


def _source_status_by_sport(payload: Mapping[str, Any]) -> dict[str, str]:
    reconciliation = payload.get("reconciliation")
    audit = reconciliation.get("acquisition_audit") if isinstance(reconciliation, Mapping) else None
    statuses: dict[str, str] = {}
    for row in audit or []:
        if not isinstance(row, Mapping):
            continue
        sport = _upper(row.get("family") or row.get("sport"))
        status = _upper(row.get("request_status"))
        if sport:
            statuses[sport] = status
    return statuses


def _enhance_sport_coverage(payload: Mapping[str, Any], coverage: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_status = _source_status_by_sport(payload)
    no_event_statuses = {"NO_EVENTS_RETURNED"}
    source_blocked = {
        "NO_CONFIGURED_DISCOVERY_FEED",
        "PROVIDER_REQUEST_FAILED",
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_SCHEMA_FAILURE",
        "DISCOVERY_BUDGET_EXHAUSTED",
    }
    out: list[dict[str, Any]] = []
    for row in coverage:
        item = dict(row)
        acquisition = source_status.get(_upper(item.get("sport")))
        item["discovery_acquisition_status"] = acquisition
        if item.get("rows_discovered") == 0:
            if acquisition in source_blocked:
                item["coverage_status"] = "SOURCE_OR_STATUS_BLOCKED"
            elif acquisition in no_event_statuses:
                item["coverage_status"] = "NO_CURRENT_PREGAME_EVENTS"
        item["can_execute"] = False
        out.append(item)
    return out


def _publication_audit_for_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    detail = dict(row.get("detail") or {}) if isinstance(row.get("detail"), Mapping) else {}
    numeric_or_package = any(
        detail.get(key) is not None
        for key in (
            "probability_package_valid",
            "calibrated_probability",
            "calibrated_home_probability",
            "calibrated_lower_bound",
            "calibrated_home_lower_bound",
        )
    )
    if not numeric_or_package:
        return None
    merged = {**detail, **dict(row)}
    return publication_chain_audit(merged).as_dict()


def _safe_score_wrapper(score_row: Callable[..., Any]) -> Callable[..., Mapping[str, Any]]:
    """Convert scorer completion failures into row-level V17 typed results."""
    def safe_score(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
        try:
            result = score_row(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - exact row fails closed
            return {
                "code": "MODEL_SCORER_FAILED",
                "model_status": "MODEL_SCORER_FAILED",
                "scorer_status": "MODEL_SCORER_FAILED",
                "error_type": type(exc).__name__,
                "model_invoked": True,
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
        if result is None:
            return {
                "code": "MODEL_SCORER_FAILED",
                "model_status": "MODEL_SCORER_FAILED",
                "scorer_status": "EMPTY_MODEL_COMPLETION",
                "model_invoked": True,
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
        if not isinstance(result, Mapping):
            return {
                "code": "MODEL_OUTPUT_INVALID",
                "model_status": "MODEL_OUTPUT_INVALID",
                "scorer_status": "NON_MAPPING_MODEL_COMPLETION",
                "returned_type": type(result).__name__,
                "model_invoked": True,
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
        payload = dict(result)
        if not payload:
            return {
                "code": "MODEL_SCORER_FAILED",
                "model_status": "MODEL_SCORER_FAILED",
                "scorer_status": "EMPTY_MODEL_COMPLETION",
                "model_invoked": True,
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            }
        payload["can_execute"] = False
        return payload

    return safe_score


def enrich_full_board_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the mandatory cross-sport completeness/reporting contract."""
    out = dict(payload)
    base_rows = [dict(row) for row in (payload.get("rows") or []) if isinstance(row, Mapping)]
    discovery = payload.get("discovery") if isinstance(payload.get("discovery"), Mapping) else {}
    inventoried_sports = list(discovery.get("sports_queried") or [])

    discovered_rows = _discovery_rows(base_rows)
    terminal_rows = [_flat_terminal_row(row) for row in base_rows]
    reconciled = reconcile_full_board(
        discovered_rows,
        terminal_rows,
        inventoried_sports=inventoried_sports,
    )
    reconciled["sport_coverage"] = _enhance_sport_coverage(
        payload, list(reconciled.get("sport_coverage") or [])
    )

    enriched_rows: list[dict[str, Any]] = []
    for row in base_rows:
        item = dict(row)
        detail = dict(item.get("detail") or {}) if isinstance(item.get("detail"), Mapping) else {}
        item["market_acquisition_status"] = _market_status(detail)
        item["governance_status"] = _governance_status(detail)
        item["publication_chain_audit"] = _publication_audit_for_row(item)
        item["can_execute"] = False
        enriched_rows.append(item)

    capability_checked = sum(
        1
        for row in enriched_rows
        if _upper(row.get("bucket"))
        not in {
            "EVENT_WRONG_DATE",
            "EVENT_STARTED_OR_FINAL",
            "EVENT_CANCELLED_OR_POSTPONED",
            "EVENT_IDENTITY_UNRESOLVED",
        }
    )
    model_attempted = sum(
        1
        for row in enriched_rows
        if (
            isinstance(row.get("detail"), Mapping)
            and row["detail"].get("model_invoked") is True
        )
        or row.get("probability_publishable") is True
    )
    governance_reached = sum(
        1
        for row in enriched_rows
        if row.get("governance_status") is not None
        or row.get("rank_eligible") is True
        or row.get("publication_chain_audit") is not None
    )

    out["rows"] = enriched_rows
    out["full_board_contract"] = {
        "stages": list(FULL_BOARD_STAGES),
        "rows_discovered": len(enriched_rows),
        "rows_model_capability_checked": capability_checked,
        "rows_model_attempted": model_attempted,
        "rows_governance_reached": governance_reached,
        "rows_final": len(enriched_rows),
        "silent_sport_omission_prohibited": True,
        "silent_row_omission_prohibited": True,
        "can_execute": False,
    }
    out["capability_preflight"] = capability_matrix()
    out["full_board_reconciliation"] = reconciled
    out["publication_complete"] = bool(reconciled.get("publication_complete"))
    out["global_terminal_authority"] = "V17_TERMINAL_REDUCER"
    out["can_execute"] = False
    return out


def install_cross_sport_full_board_overlay() -> bool:
    """Wrap the canonical cross-sport scan so every caller gets the full contract."""
    from v17 import cross_sport_winner_discovery as discovery

    if getattr(discovery, "_v17_full_board_overlay_installed", False):
        return True
    original = getattr(discovery, "run_cross_sport_winner_scan", None)
    if not callable(original):
        return False

    def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        call_kwargs = dict(kwargs)
        # Full-board means all configured competition regimes, not only the
        # regular-season provider id. Explicit callers can still override this.
        call_kwargs.setdefault("include_regime_variants", True)
        score_row = call_kwargs.get("score_row")
        if callable(score_row):
            call_kwargs["score_row"] = _safe_score_wrapper(score_row)
        return enrich_full_board_result(original(*args, **call_kwargs))

    discovery._v17_full_board_original_scan = original
    discovery.run_cross_sport_winner_scan = wrapped
    discovery._v17_full_board_overlay_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "FULL_BOARD_STAGES",
    "enrich_full_board_result",
    "install_cross_sport_full_board_overlay",
]
