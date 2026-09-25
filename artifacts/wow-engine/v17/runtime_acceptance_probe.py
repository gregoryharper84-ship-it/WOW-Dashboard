"""Non-secret live acceptance proof for the Sep-15 V17 runtime repairs.

This module verifies composition at the deployed runtime boundary without changing
model parameters, publication rules, or execution capability. It intentionally
returns only safe metadata: status/code/counts/booleans. Credentials, odds,
probabilities, participant identities, and raw provider payloads are never exposed.

Checks:
1. TheRundown V2 authenticated MLB moneyline snapshot path.
2. Optional market_prior sanitizer behavior at scorer ingress.
3. Existing immutable projected-lineup MLB score recovery path.

The probe runs once after startup in a background task and caches the result on
``app.state``. The public route only reads that cached result; it never causes a
provider request, so it cannot be abused to consume market-data quota.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

CAN_EXECUTE = False
_LOGGER = logging.getLogger("wow.v17.runtime.acceptance")
_TASKS: set[asyncio.Task] = set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _base_result(status: str, code: str) -> dict[str, Any]:
    return {
        "status": status,
        "code": code,
        "prediction_authority": False,
        "secret_value_exposed": False,
        "can_execute": False,
    }


def _moneyline_event_count(events: Any) -> int:
    """Count normalized events with a two-sided h2h market without reading prices."""
    if not isinstance(events, list):
        return 0
    count = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        found = False
        for book in event.get("bookmakers") or []:
            if not isinstance(book, dict):
                continue
            for market in book.get("markets") or []:
                if not isinstance(market, dict) or market.get("key") != "h2h":
                    continue
                named = [
                    outcome for outcome in (market.get("outcomes") or [])
                    if isinstance(outcome, dict) and str(outcome.get("name") or "").strip()
                ]
                if len(named) >= 2:
                    found = True
                    break
            if found:
                break
        if found:
            count += 1
    return count


def _safe_provider_health_fields(health: Any) -> dict[str, Any]:
    """Expose only value-free auth/access metadata from the strict provider probe."""
    payload = health if isinstance(health, dict) else {}
    catalog = payload.get("catalog_access") if isinstance(payload.get("catalog_access"), dict) else {}
    event = payload.get("event_access") if isinstance(payload.get("event_access"), dict) else {}
    return {
        "provider_health_status": payload.get("status"),
        "catalog_access_status": catalog.get("market_acquisition_status"),
        "catalog_provider_code": catalog.get("provider_code"),
        "catalog_http_status": catalog.get("http_status"),
        "catalog_auth_ok": catalog.get("auth_ok"),
        "event_access_status": event.get("market_acquisition_status"),
        "event_provider_code": event.get("provider_code"),
        "event_http_status": event.get("http_status"),
        "event_auth_ok": event.get("auth_ok"),
    }


def _rundown_acceptance() -> dict[str, Any]:
    """Exercise the authenticated moneyline path and localize auth/entitlement failures."""
    from v17 import market_evidence_native_live as live
    from v17 import market_evidence_sources as sources
    from v17.rundown_credential_diagnostic import rundown_credential_status
    from v17.rundown_provider_health import probe_rundown_provider_health

    credential = rundown_credential_status()
    slate_date = datetime.now(timezone.utc).date().isoformat()
    try:
        result = live.get_sport_date_odds_snapshot(
            "baseball_mlb",
            slate_date,
            capability="events",
            sport_id=3,
            market_ids=sources.rundown_winner_market_ids() or None,
            main_line=True,
            hide_closed=True,
        )
    except Exception as exc:  # fail closed; never publish exception text
        return {
            **_base_result("FAIL", "RUNDOWN_ACCEPTANCE_EXCEPTION"),
            "credential_configured": bool(credential.get("configured")),
            "sport_id": 3,
            "market_family": "MONEYLINE",
            "error_type": type(exc).__name__,
            "snapshot_retrieved": False,
            "events_returned": 0,
            "moneyline_events_returned": 0,
        }

    audit = result.request_audit if isinstance(result.request_audit, dict) else {}
    events_returned = len(result.data) if isinstance(result.data, list) else 0
    moneyline_events = _moneyline_event_count(result.data)
    passed = bool(result.ok and events_returned > 0 and moneyline_events > 0)

    provider_health: dict[str, Any] = {}
    # A bare 401/403 from the odds snapshot cannot distinguish a rejected
    # credential from a valid credential that lacks event/plan entitlement.
    # Only on that failure class, run the strict catalog -> events diagnostic.
    # The response is reduced to booleans/status codes below; no provider body,
    # price, participant, or credential value is ever returned.
    if not result.ok and result.status in {401, 403}:
        try:
            provider_health = _safe_provider_health_fields(
                probe_rundown_provider_health(
                    sport_key="baseball_mlb",
                    date=slate_date,
                )
            )
        except Exception as exc:  # diagnostic failure must stay typed and secret-free
            provider_health = {
                "provider_health_status": "DIAGNOSTIC_FAILED",
                "provider_health_error_type": type(exc).__name__,
            }

    return {
        **_base_result("PASS" if passed else "FAIL", "RUNDOWN_LIVE_BOARD_VERIFIED" if passed else str(result.code or "RUNDOWN_LIVE_BOARD_UNVERIFIED")),
        "credential_configured": bool(credential.get("configured")),
        "sport_id": 3,
        "market_family": "MONEYLINE",
        "http_status": result.status,
        "payload_classification": audit.get("payload_classification"),
        "snapshot_retrieved": bool(result.ok),
        "events_returned": events_returned,
        "moneyline_events_returned": moneyline_events,
        **provider_health,
    }


def _market_prior_acceptance(team_runtime: Any) -> dict[str, Any]:
    mapper = getattr(team_runtime, "_model_market_prior", None)
    if not callable(mapper):
        return _base_result("FAIL", "MARKET_PRIOR_SANITIZER_UNAVAILABLE")

    timestamp = _now_iso()
    valid = {
        "home_probability": 0.52,
        "away_probability": 0.48,
        "timestamp": timestamp,
        "quality": "ACCEPTANCE_FIXTURE",
        "source": "RUNTIME_ACCEPTANCE",
        "snapshot_id": "must-not-reach-scorer",
        "book_count": 2,
    }
    malformed = {"snapshot_id": "partial-optional-context"}
    try:
        mapped = mapper(valid)
        rejected = mapper(malformed)
    except Exception as exc:
        return {
            **_base_result("FAIL", "MARKET_PRIOR_SANITIZER_EXCEPTION"),
            "error_type": type(exc).__name__,
        }

    allowed = {"home_probability", "away_probability", "timestamp", "quality", "source"}
    passed = bool(
        isinstance(mapped, dict)
        and set(mapped).issubset(allowed)
        and {"home_probability", "away_probability", "timestamp"}.issubset(mapped)
        and rejected is None
    )
    return {
        **_base_result("PASS" if passed else "FAIL", "MARKET_PRIOR_INGRESS_INVARIANT_VERIFIED" if passed else "MARKET_PRIOR_INGRESS_INVARIANT_FAILED"),
        "valid_optional_context_accepted": isinstance(mapped, dict),
        "envelope_only_fields_forwarded": bool(isinstance(mapped, dict) and ({"snapshot_id", "book_count"} & set(mapped))),
        "malformed_optional_context_rejected_before_scorer": rejected is None,
        "sporting_model_eligibility_mutation_allowed": False,
    }


def _projected_lineup_acceptance(event_api: Any) -> dict[str, Any]:
    """Prove the installed runtime can recover one existing immutable held score."""
    get_client = getattr(event_api, "get_client", None)
    request_type = getattr(event_api, "ScoreEventRequest", None)
    score_event = getattr(event_api, "score_event", None)
    if not callable(get_client) or request_type is None or not callable(score_event):
        return _base_result("FAIL", "MLB_PROJECTED_ACCEPTANCE_RUNTIME_UNAVAILABLE")

    now = datetime.now(timezone.utc).isoformat()
    try:
        rows = (
            get_client().table("wow_mlb_forward_shadow_events")
            .select(
                "official_event_id,official_date,event_start_time,home_team,away_team,venue_name,"
                "home_probable_pitcher,away_probable_pitcher,snapshot_id,snapshot_timestamp,"
                "feature_hydration_status,model_score_status"
            )
            .eq("feature_hydration_status", "PASS")
            .eq("model_score_status", "SHADOW_SCORED_LINEUP_PENDING")
            .gt("event_start_time", now)
            .order("snapshot_timestamp", desc=True)
            .limit(8)
            .execute().data
            or []
        )
    except Exception as exc:
        return {
            **_base_result("FAIL", "MLB_PROJECTED_ACCEPTANCE_QUERY_FAILED"),
            "error_type": type(exc).__name__,
            "eligible_event_found": False,
        }

    row = next((dict(value) for value in rows if isinstance(value, dict) and all(
        str(value.get(field) or "").strip()
        for field in (
            "official_event_id", "official_date", "event_start_time", "home_team", "away_team",
            "venue_name", "home_probable_pitcher", "away_probable_pitcher", "snapshot_id",
            "snapshot_timestamp",
        )
    )), None)
    if row is None:
        return {
            **_base_result("NOT_APPLICABLE", "NO_ELIGIBLE_PROJECTED_LINEUP_EVENT"),
            "eligible_event_found": False,
        }

    try:
        req = request_type(
            research_run_id="v17-runtime-acceptance",
            requested_slate_date=str(row["official_date"]),
            requested_timezone="America/Chicago",
            scan_stage="PREGAME",
            event_key=f"runtime-acceptance:{row['official_event_id']}",
            official_event_id=str(row["official_event_id"]),
            event_start_time_utc=str(row["event_start_time"]),
            sport="MLB",
            league="MLB",
            market_family="OUTRIGHT_WINNER",
            settlement_basis="FULL_GAME_INCLUDING_EXTRA_INNINGS",
            home_team=str(row["home_team"]),
            away_team=str(row["away_team"]),
            venue=str(row["venue_name"]),
            home_starting_pitcher=str(row["home_probable_pitcher"]),
            away_starting_pitcher=str(row["away_probable_pitcher"]),
            home_starter_status="PROBABLE",
            away_starter_status="PROBABLE",
            home_lineup_status="PROJECTED",
            away_lineup_status="PROJECTED",
            latest_material_update_timestamp=str(row["snapshot_timestamp"]),
            source_snapshot_id=str(row["snapshot_id"]),
            market_prior=None,
        )
        receipt = score_event(req)
    except Exception as exc:
        detail = getattr(exc, "detail", None)
        blocker = detail.get("blocker_code") if isinstance(detail, dict) else None
        return {
            **_base_result("FAIL", "MLB_PROJECTED_LINEUP_RUNTIME_PATH_FAILED"),
            "error_type": type(exc).__name__,
            "blocker_code": blocker,
            "eligible_event_found": True,
            "sport_model_invoked": False,
            "rank_eligible": False,
        }

    receipt = receipt if isinstance(receipt, dict) else {}
    passed = bool(
        receipt.get("code") == "REAL_FITTED_MODEL_PATH_PROVEN"
        and receipt.get("sport_model_invoked") is True
        and receipt.get("sport_model_invocation_source") == "IMMUTABLE_PROJECTED_SCORE_SNAPSHOT"
        and receipt.get("rank_eligible") is False
        and receipt.get("can_execute") is False
    )
    return {
        **_base_result("PASS" if passed else "FAIL", "MLB_PROJECTED_LINEUP_RUNTIME_VERIFIED" if passed else "MLB_PROJECTED_LINEUP_RUNTIME_UNVERIFIED"),
        "eligible_event_found": True,
        "receipt_code": receipt.get("code"),
        "sport_model_invoked": receipt.get("sport_model_invoked") is True,
        "invocation_source": receipt.get("sport_model_invocation_source"),
        "probability_fields_withheld_at_bridge": receipt.get("probability_fields_withheld") is True,
        "rank_eligible": bool(receipt.get("rank_eligible")),
    }


def run_runtime_acceptance_probe(*, event_api: Any, team_runtime: Any) -> dict[str, Any]:
    checks = {
        "therundown_live_board": _rundown_acceptance(),
        "market_prior_ingress": _market_prior_acceptance(team_runtime),
        "mlb_projected_lineup": _projected_lineup_acceptance(event_api),
    }
    statuses = [str(check.get("status")) for check in checks.values()]
    if any(status == "FAIL" for status in statuses):
        overall = "FAIL"
    elif all(status == "PASS" for status in statuses):
        overall = "PASS"
    else:
        overall = "PARTIAL"
    return {
        "status": overall,
        "runtime_generation": "V17_ACTIVE",
        "checked_at": _now_iso(),
        "checks": checks,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "probability_values_exposed": False,
        "secret_value_exposed": False,
        "can_execute": False,
    }


def install_runtime_acceptance_probe(*, app: Any, market_api: Any, team_runtime: Any) -> bool:
    """Install cached read-only status route plus one background startup probe."""
    if getattr(app.state, "v17_runtime_acceptance_probe_installed", False):
        return True

    app.state.v17_runtime_acceptance = {
        "status": "PENDING",
        "runtime_generation": "V17_ACTIVE",
        "checked_at": None,
        "checks": {},
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "probability_values_exposed": False,
        "secret_value_exposed": False,
        "can_execute": False,
    }

    @app.get("/runtime-acceptance")
    def runtime_acceptance_status():
        return app.state.v17_runtime_acceptance

    @app.on_event("startup")
    async def runtime_acceptance_after_startup():
        event_api = getattr(getattr(market_api, "prod", None), "event_api", None)
        if event_api is None:
            app.state.v17_runtime_acceptance = {
                **app.state.v17_runtime_acceptance,
                "status": "FAIL",
                "checked_at": _now_iso(),
                "checks": {"runtime": _base_result("FAIL", "EVENT_API_UNAVAILABLE")},
            }
            return

        async def _run() -> None:
            await asyncio.sleep(2.0)
            result = await asyncio.to_thread(
                run_runtime_acceptance_probe,
                event_api=event_api,
                team_runtime=team_runtime,
            )
            app.state.v17_runtime_acceptance = result
            _LOGGER.info(
                "V17_RUNTIME_ACCEPTANCE status=%s rundown=%s market_prior=%s projected_lineup=%s secret_value_exposed=false probability_values_exposed=false can_execute=false",
                result["status"],
                result["checks"]["therundown_live_board"]["status"],
                result["checks"]["market_prior_ingress"]["status"],
                result["checks"]["mlb_projected_lineup"]["status"],
            )

        task = asyncio.create_task(_run())
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)

    app.state.v17_runtime_acceptance_probe_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "install_runtime_acceptance_probe",
    "run_runtime_acceptance_probe",
]
