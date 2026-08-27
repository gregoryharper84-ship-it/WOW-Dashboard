from __future__ import annotations

"""Assertion-based integration of the validated MLB V2 rolling model into wow-engine."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api.py"
REQ = ROOT / "requirements.txt"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"anchor count for {path}: expected 1, found {count}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1))


def patch_requirements() -> None:
    text = REQ.read_text()
    text = text.replace("scikit-learn>=1.4", "scikit-learn==1.9.0")
    if "joblib>=1.5.3" not in text:
        text = text.rstrip() + "\njoblib>=1.5.3\n"
    if "requests>=2.31.0" not in text:
        text = text.rstrip() + "\nrequests>=2.31.0\n"
    REQ.write_text(text)


def patch_api() -> None:
    replace_once(
        API,
        'app = FastAPI(title="WOW External Governed Backend", version="1.1.0")',
        'app = FastAPI(title="WOW External Governed Backend", version="1.2.0")',
    )

    old_governance = '''@app.get("/governance")
def governance():
    return {
        "governed_probability_capability": GOVERNED_PROBABILITY_CAPABILITY,
        "governed_probability_status": "NOT_PRODUCED" if GOVERNED_PROBABILITY_CAPABILITY == "UNAVAILABLE" else "PRODUCED",
        "patch_id": "WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE",
        "patch_revision": "v2",
        "note": f"governed_probability_capability stays UNAVAILABLE until all "
                f"{DEPLOYMENT_GATE_COUNT} deployment gate items pass against this live "
                f"deployment. Section 8A Manual Estimate Lane is the correct fallback "
                f"until then.",
    }
'''
    new_governance = '''@app.get("/governance")
def governance():
    # Prop and event capabilities are independent. The prop lane remains
    # fail-closed until its own fitted distribution artifacts clear the 11-point
    # deployment gate. MLB full-game outright-winner now has a separately
    # validated rolling V2 probability artifact.
    try:
        from mlb_v2_runtime import artifact_health as _mlb_event_artifact_health
        _event_health = _mlb_event_artifact_health(date.today())
        _mlb_event_capability = _event_health.get("probability_capability", "UNAVAILABLE")
        _mlb_event_blockers = _event_health.get("blockers") or []
    except Exception as _event_exc:
        _mlb_event_capability = "UNAVAILABLE"
        _mlb_event_blockers = [f"MLB_V2_RUNTIME_IMPORT_FAILED:{type(_event_exc).__name__}"]
    return {
        "governed_probability_capability": GOVERNED_PROBABILITY_CAPABILITY,
        "governed_probability_status": "NOT_PRODUCED" if GOVERNED_PROBABILITY_CAPABILITY == "UNAVAILABLE" else "PRODUCED",
        "event_probability_capabilities": {
            "MLB_OUTRIGHT_WINNER": {
                "capability": _mlb_event_capability,
                "model_id": "mlb-moneyline-v2-rolling-2026",
                "probability_publishable_when_live_gates_pass": _mlb_event_capability == "AVAILABLE",
                "blockers": _mlb_event_blockers,
                "can_execute": False,
                "can_approve_bets": False,
            }
        },
        "patch_id": "WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE",
        "patch_revision": "v3-mlb-event-v2",
        "note": f"Prop governed_probability_capability stays UNAVAILABLE until all "
                f"{DEPLOYMENT_GATE_COUNT} prop deployment gate items pass. MLB_OUTRIGHT_WINNER "
                f"is governed separately by its validated rolling V2 artifact and live pregame gates.",
    }
'''
    replace_once(API, old_governance, new_governance)

    old_score_event = '''@app.post("/score-event", dependencies=[Depends(_require_action_api_key)])
def score_event(req: ScoreEventRequest):
    """Validate one MLB full-game ML event, then fail closed until fitted.

    V1 intentionally has no scoring/persistence positive path. This route
    proves identity/auth/routing separation without turning an absent
    model artifact into an invented probability.
    """
    errors = _score_event_contract_errors(req)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EVENT_CONTRACT_INVALID",
                "probability_publishable": False,
                "errors": errors,
                "can_execute": False,
            },
        )

    raise HTTPException(
        status_code=409,
        detail={
            "ok": False,
            "code": "GOVERNED_EVENT_MODEL_UNAVAILABLE",
            "sport": "MLB",
            "market_family": "OUTRIGHT_WINNER",
            "controlling_specialist": "wow.mlb-game-win-probability-expert",
            "governed_probability_capability": "UNAVAILABLE",
            "governed_probability_status": "NOT_PRODUCED",
            "probability_publishable": False,
            "fallback": "SECTION_8A_MANUAL_ESTIMATE_LANE",
            "blockers": [
                "MLB_FITTED_MODEL_ARTIFACT_UNAVAILABLE",
                "MLB_EVENT_CALIBRATOR_UNAVAILABLE",
            ],
            "can_execute": False,
        },
    )
'''
    new_score_event = '''def _person_identity(value: str) -> str:
    """Accent-insensitive alphanumeric identity for starter cross-checks."""
    import unicodedata
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in ascii_text.casefold() if ch.isalnum())


def _event_unavailable(*, blockers: list[str], capability: str = "AVAILABLE", hydration: Optional[dict] = None):
    detail = {
        "ok": False,
        "code": "GOVERNED_EVENT_MODEL_UNAVAILABLE",
        "sport": "MLB",
        "market_family": "OUTRIGHT_WINNER",
        "controlling_specialist": "wow.mlb-game-win-probability-expert",
        "governed_probability_capability": capability,
        "governed_probability_status": "NOT_PRODUCED",
        "probability_publishable": False,
        "fallback": "SECTION_8A_MANUAL_ESTIMATE_LANE",
        "blockers": blockers,
        "can_execute": False,
        "can_approve_bets": False,
    }
    if hydration is not None:
        detail["hydration"] = hydration
    raise HTTPException(status_code=409, detail=detail)


@app.post("/score-event", dependencies=[Depends(_require_action_api_key)])
def score_event(req: ScoreEventRequest):
    """Publish one governed MLB full-game win probability when every V2 gate passes.

    The point estimate is the independently fitted rolling V2 model followed by
    its Platt calibrator. Optional market_prior is audit-only and has exactly zero
    weight in the published probability. This endpoint never authorizes execution.
    """
    errors = _score_event_contract_errors(req)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "EVENT_CONTRACT_INVALID",
                "probability_publishable": False,
                "errors": errors,
                "can_execute": False,
                "can_approve_bets": False,
            },
        )

    from mlb_v2_hydrator import hydrate_mlb_v2_enrichment
    from mlb_v2_runtime import artifact_health, score_home_probability

    slate_date = date.fromisoformat(req.requested_slate_date)
    health = artifact_health(slate_date)
    if not health.get("healthy"):
        _event_unavailable(
            blockers=list(health.get("blockers") or ["MLB_V2_ARTIFACT_UNAVAILABLE"]),
            capability="UNAVAILABLE",
        )

    row = {
        "sport": "MLB",
        "team": req.home_team,
        "opponent": req.away_team,
        "game_date": req.requested_slate_date,
        "event_id": req.official_event_id,
    }
    try:
        row["gamePk"] = int(str(req.official_event_id).replace("MLBAM", ""))
    except (TypeError, ValueError):
        pass
    enrichment = hydrate_mlb_v2_enrichment(row, {})
    hydration = enrichment.get("mlb_v2_hydration") or {}
    features = enrichment.get("mlb_v2_feature_vector")
    if hydration.get("status") != "FEATURES_READY" or not isinstance(features, list):
        _event_unavailable(
            blockers=list(hydration.get("blockers") or ["MLB_V2_LIVE_FEATURES_UNAVAILABLE"]),
            capability="AVAILABLE",
            hydration=hydration,
        )

    identity_blockers: list[str] = []
    official_home_sp = str(hydration.get("home_starter_name") or "")
    official_away_sp = str(hydration.get("away_starter_name") or "")
    if _person_identity(req.home_starting_pitcher) != _person_identity(official_home_sp):
        identity_blockers.append(
            f"MLB_V2_HOME_STARTER_IDENTITY_MISMATCH:request={req.home_starting_pitcher}:official={official_home_sp}"
        )
    if _person_identity(req.away_starting_pitcher) != _person_identity(official_away_sp):
        identity_blockers.append(
            f"MLB_V2_AWAY_STARTER_IDENTITY_MISMATCH:request={req.away_starting_pitcher}:official={official_away_sp}"
        )
    if identity_blockers:
        _event_unavailable(blockers=identity_blockers, capability="AVAILABLE", hydration=hydration)

    scored = score_home_probability(features, as_of=slate_date)
    if not scored.get("ok") or not scored.get("probability_publishable"):
        _event_unavailable(
            blockers=list(scored.get("blockers") or ["MLB_V2_RUNTIME_GATE_FAILED"]),
            capability="AVAILABLE",
            hydration=hydration,
        )

    home_p = float(scored["home_probability"])
    away_p = float(scored["away_probability"])
    home_lo = float(scored["home_probability_lower_bound"])
    home_hi = float(scored["home_probability_upper_bound"])
    market_audit = None
    if req.market_prior is not None:
        market_audit = {
            "source": req.market_prior.source,
            "timestamp": req.market_prior.timestamp,
            "quality": req.market_prior.quality,
            "home_no_vig_probability": req.market_prior.home_probability,
            "away_no_vig_probability": req.market_prior.away_probability,
            "home_model_edge": round(home_p - req.market_prior.home_probability, 6),
            "away_model_edge": round(away_p - req.market_prior.away_probability, 6),
            "used_in_point_probability": False,
            "market_weight_in_point_probability": 0.0,
        }

    return {
        "ok": True,
        "code": "GOVERNED_EVENT_MODEL_AVAILABLE",
        "sport": "MLB",
        "market_family": "OUTRIGHT_WINNER",
        "controlling_specialist": "wow.mlb-game-win-probability-expert",
        "governed_probability_capability": "AVAILABLE",
        "governed_probability_status": "PRODUCED",
        "probability_publishable": True,
        "model_id": scored.get("model_id"),
        "schema_version": scored.get("schema_version"),
        "home_team": hydration.get("home_team"),
        "away_team": hydration.get("away_team"),
        "home_probability": round(home_p, 6),
        "away_probability": round(away_p, 6),
        "home_probability_lower_bound": round(max(0.01, home_lo), 6),
        "home_probability_upper_bound": round(min(0.99, home_hi), 6),
        "away_probability_lower_bound": round(max(0.01, 1.0 - home_hi), 6),
        "away_probability_upper_bound": round(min(0.99, 1.0 - home_lo), 6),
        "native_calibrated": True,
        "market_weight_in_point_probability": 0.0,
        "market_audit": market_audit,
        "empirical_interval": scored.get("empirical_interval"),
        "drift": scored.get("drift"),
        "hydration": hydration,
        "source_snapshot_id": req.source_snapshot_id,
        "fallback": None,
        "blockers": [],
        "can_execute": False,
        "can_approve_bets": False,
    }
'''
    replace_once(API, old_score_event, new_score_event)


def main() -> None:
    patch_requirements()
    patch_api()
    print("WOW_ENGINE_MLB_V2_EVENT_INTEGRATION=APPLIED")


if __name__ == "__main__":
    main()
