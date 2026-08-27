from __future__ import annotations

"""Assertion-based production integration patch for MLB V2 rolling probability."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"anchor count for {path}: expected 1, found {count}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1))


def insert_before_once(path: Path, anchor: str, addition: str) -> None:
    text = path.read_text()
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(f"anchor count for {path}: expected 1, found {count}: {anchor[:120]!r}")
    path.write_text(text.replace(anchor, addition + anchor, 1))


def patch_requirements() -> None:
    path = ROOT / "requirements.txt"
    text = path.read_text()
    additions = []
    if "scikit-learn==1.9.0" not in text:
        additions.append("scikit-learn==1.9.0")
    if "joblib>=1.5.3" not in text:
        additions.append("joblib>=1.5.3")
    if additions:
        path.write_text(text.rstrip() + "\n" + "\n".join(additions) + "\n")


def patch_registry() -> None:
    path = ROOT / "gate_engine" / "moneyline_probability.py"
    old = '''    "MLB": {
        "model_id":    "mlb-moneyline-logit-v1",
        "status":      ModelStatus.ACTIVE,
        "output_type": "binary",          # home_win | away_win
        "features":    ["run_line_spread", "starting_pitcher_era", "bullpen_era",
                        "home_away_flag", "season_win_pct", "last_10_win_pct"],
    },'''
    new = '''    "MLB": {
        "model_id":    "mlb-moneyline-v2-rolling-2026",
        "status":      ModelStatus.ACTIVE,
        "output_type": "binary",          # home_win | away_win
        "features":    ["mlb_v2_feature_vector:MLB_PREGAME_V2_20260827:41"],
        "probability_capability": "AVAILABLE",
        "probability_publication": "GOVERNED_V2_ROLLING",
        "can_execute": False,
        "can_approve_bets": False,
    },'''
    replace_once(path, old, new)


def patch_sport_model() -> None:
    path = ROOT / "gate_engine" / "moneyline" / "sport_model.py"
    replace_once(path, "import math\nfrom typing import Any\n", "import math\nfrom datetime import date\nfrom typing import Any\n")
    anchor = "# ---------------------------------------------------------------------------\n# Main entry point\n# ---------------------------------------------------------------------------\n"
    helper = '''def _mlb_v2_as_of_date(row: dict[str, Any], enrichment: dict[str, Any]) -> date:
    """Resolve the game date for artifact validity without reading market data."""
    for source in (row, enrichment):
        for key in ("game_date", "event_date", "slate_date", "date", "start_time", "commence_time"):
            value = source.get(key)
            if value in (None, ""):
                continue
            try:
                return date.fromisoformat(str(value)[:10])
            except ValueError:
                continue
    return date.today()


def _mlb_v2_independent_probability(
    row: dict[str, Any], clean_enrichment: dict[str, Any], orientation: OrientationResolution
) -> dict[str, Any]:
    """Use the validated rolling V2 + Platt artifact as MLB's sole Stage-3 model."""
    from gate_engine.mlb_v2_runtime import score_home_probability

    features = clean_enrichment.get("mlb_v2_feature_vector")
    scored = score_home_probability(features, as_of=_mlb_v2_as_of_date(row, clean_enrichment))
    if not scored.get("ok") or not scored.get("probability_publishable"):
        blockers = list(scored.get("blockers") or ["MLB_V2_PROBABILITY_UNAVAILABLE"])
        return {
            "independent_probability": None,
            "independent_probability_raw": None,
            "home_probability": None,
            "away_probability": None,
            "submodel_probs": {},
            "submodels_active": [],
            "ensemble_weights_used": {},
            "home_advantage_logit": 0.0,
            "soccer_three_state": None,
            "notes": [f"MLB_V2_NOT_READY:{b}" for b in blockers],
            "data_contract_status": "DATA_CONTRACT_FAIL",
            "model_id": scored.get("model_id") or "mlb-moneyline-v2-rolling-2026",
            "native_calibrated": True,
            "point_estimate_locked": True,
            "probability_publishable": False,
            "drift": scored.get("drift"),
            "can_execute": False,
        }
    p_home = float(scored["home_probability"])
    p_away = float(scored["away_probability"])
    return {
        "independent_probability": p_home,
        "independent_probability_raw": float(scored["raw_pre_platt_home_probability"]),
        "home_probability": p_home,
        "away_probability": p_away,
        "submodel_probs": {"mlb_v2_rolling": p_home},
        "submodels_active": ["mlb_v2_rolling"],
        "ensemble_weights_used": {"mlb_v2_rolling": 1.0},
        "home_advantage_logit": 0.0,
        "soccer_three_state": None,
        "notes": [
            "MLB_V2_ROLLING_ACTIVE:market_free_stage3",
            "MLB_V2_POINT_NATIVE_PLATT:downstream_market_weight=0",
            f"probability_perspective=HOME_WIN is_home={orientation.is_home}",
        ],
        "model_id": scored["model_id"],
        "schema_version": scored["schema_version"],
        "native_calibrated": True,
        "point_estimate_locked": True,
        "probability_publishable": True,
        "model_native_home_lower_bound": float(scored["home_probability_lower_bound"]),
        "model_native_home_upper_bound": float(scored["home_probability_upper_bound"]),
        "empirical_interval": scored.get("empirical_interval"),
        "drift": scored.get("drift"),
        "market_weight_in_point_probability": 0.0,
        "can_execute": False,
    }


'''
    insert_before_once(path, anchor, helper)
    old = '''    is_home = orientation.is_home
    is_soccer = sport in ("SOCCER", "EPL", "MLS")

    submodel_probs:   dict[str, float] = {}'''
    new = '''    is_home = orientation.is_home
    is_soccer = sport in ("SOCCER", "EPL", "MLS")

    # MLB V2 is the validated, market-free independent model. Do not blend it
    # with the legacy H2H/Elo/power heuristic ensemble.
    if sport == "MLB":
        return _mlb_v2_independent_probability(row, clean_enrichment, orientation)

    submodel_probs:   dict[str, float] = {}'''
    replace_once(path, old, new)


def patch_pipeline() -> None:
    path = ROOT / "gate_engine" / "moneyline" / "pipeline.py"
    old = '''    result.sport_model   = sport_model_out
    independent_prob_raw = sport_model_out.get("independent_probability")   # P(home wins)

    if independent_prob_raw is None:'''
    new = '''    result.sport_model   = sport_model_out
    independent_prob_raw = sport_model_out.get("independent_probability")   # P(home wins)
    _mlb_v2_native = (
        sport == "MLB"
        and sport_model_out.get("native_calibrated") is True
        and sport_model_out.get("point_estimate_locked") is True
    )

    if independent_prob_raw is None:'''
    replace_once(path, old, new)

    old = '''    sim_result = run_game_state_simulation(row, clean_enr, independent_prob_raw,
                                           n_sims=n_sims, seed=seed)
    result.simulation = sim_result.to_dict()
    independent_prob_post_sim = sim_result.adjusted_prob   # P(home wins)
'''
    new = '''    sim_result = run_game_state_simulation(row, clean_enr, independent_prob_raw,
                                           n_sims=n_sims, seed=seed)
    result.simulation = sim_result.to_dict()
    if _mlb_v2_native:
        independent_prob_post_sim = float(independent_prob_raw)
        result.simulation["point_estimate_applied"] = False
        result.simulation["point_estimate_lock_reason"] = "MLB_V2_NATIVE_PLATT_ALREADY_VALIDATED"
    else:
        independent_prob_post_sim = sim_result.adjusted_prob   # P(home wins)
'''
    replace_once(path, old, new)

    old = '''    result.failure_path = fp_result.to_dict()

    # -----------------------------------------------------------------------
    # Stage 5.5: MLB Starter-Change analysis'''
    new = '''    result.failure_path = fp_result.to_dict()
    if _mlb_v2_native:
        # Failure-path simulation remains an uncertainty/diagnostic layer. The
        # live V2 vector already encodes the current pregame state, so it may not
        # overwrite the validated Platt point estimate.
        fp_result.adjusted_win_prob = float(independent_prob_post_sim)
        result.failure_path = fp_result.to_dict()
        result.failure_path["point_estimate_applied"] = False
        result.failure_path["point_estimate_lock_reason"] = "MLB_V2_NATIVE_PLATT_ALREADY_VALIDATED"

    # -----------------------------------------------------------------------
    # Stage 5.5: MLB Starter-Change analysis'''
    replace_once(path, old, new)

    replace_once(
        path,
        '''    if _sc_result.probability_adjustment != 0.0:
        _before_adj = fp_result.adjusted_win_prob''',
        '''    if _sc_result.probability_adjustment != 0.0 and not _mlb_v2_native:
        _before_adj = fp_result.adjusted_win_prob''',
    )
    old = '''        result.starter_change["probability_adjustment_applied"] = {
            "before":     round(_before_adj, 4),
            "adjustment": round(_sc_result.probability_adjustment, 4),
            "after":      round(fp_result.adjusted_win_prob, 4),
            "note":       "quality_delta_only:not_a_fixed_scratch_penalty",
        }

    # Inject uncertainty expansion into enrichment so calibration (stage 8) can'''
    new = '''        result.starter_change["probability_adjustment_applied"] = {
            "before":     round(_before_adj, 4),
            "adjustment": round(_sc_result.probability_adjustment, 4),
            "after":      round(fp_result.adjusted_win_prob, 4),
            "note":       "quality_delta_only:not_a_fixed_scratch_penalty",
        }
    elif _sc_result.probability_adjustment != 0.0 and _mlb_v2_native:
        result.starter_change["probability_adjustment_applied"] = {
            "suppressed": True,
            "proposed_adjustment": round(_sc_result.probability_adjustment, 4),
            "note": "MLB_V2_CURRENT_STARTER_ALREADY_IN_FEATURE_VECTOR:no_double_count",
        }

    # Inject uncertainty expansion into enrichment so calibration (stage 8) can'''
    replace_once(path, old, new)

    old = '''        market_no_vig=market_no_vig,
        market_inputs=market_inputs,
    )
    result.calibration = cal_result.to_dict()
'''
    new = '''        market_no_vig=(None if _mlb_v2_native else market_no_vig),
        market_inputs=({} if _mlb_v2_native else market_inputs),
    )
    if _mlb_v2_native:
        # MLB V2 already includes an independently-fitted Platt calibrator. Keep
        # the validated point estimate immutable and use the legacy calibration
        # layer only for uncertainty accounting. Market comparison remains Stage 11.
        cal_result.calibrated_probability = float(independent_prob_final)
        cal_result.model_weight = 1.0
        cal_result.market_weight = 0.0
        cal_result.market_no_vig_used = None
        cal_result.market_dependent_flag = False
        cal_result.net_edge = (
            float(independent_prob_final) - float(market_no_vig)
            if market_no_vig is not None else None
        )
        _home_lo = sport_model_out.get("model_native_home_lower_bound")
        _home_hi = sport_model_out.get("model_native_home_upper_bound")
        if _home_lo is not None and _home_hi is not None:
            if is_home:
                _emp_lo, _emp_hi = float(_home_lo), float(_home_hi)
            else:
                _emp_lo, _emp_hi = 1.0 - float(_home_hi), 1.0 - float(_home_lo)
            cal_result.calibrated_lower_bound = min(cal_result.calibrated_lower_bound, _emp_lo)
            cal_result.calibrated_upper_bound = max(cal_result.calibrated_upper_bound, _emp_hi)
        cal_result.calibration_notes.append("MLB_V2_NATIVE_PLATT_POINT_LOCK:market_weight=0")
        cal_result.calibration_notes.append("MLB_V2_BOUND=conservative_union_dynamic_and_empirical_calibration_interval")
    result.calibration = cal_result.to_dict()
'''
    replace_once(path, old, new)


def patch_app_moneyline_hydration() -> None:
    path = ROOT / "app.py"
    old = '''            _ow_sport = (_ow_row.get("sport") or "").upper()
            if _ow_sport in ("NBA", "MLB") and not any(
                _ow_enr.get(k)
                for k in ("home_win_pct", "away_win_pct", "home_power", "away_power")
            ):
'''
    new = '''            _ow_sport = (_ow_row.get("sport") or "").upper()
            if _ow_sport == "MLB" and not isinstance(_ow_enr.get("mlb_v2_feature_vector"), list):
                try:
                    from gate_engine.mlb_v2_hydrator import hydrate_mlb_v2_enrichment as _hydrate_mlb_v2
                    _ow_enr = _hydrate_mlb_v2(_ow_row, _ow_enr)
                    enrichment[_ow_row_id] = _ow_enr
                except Exception as _mlb_v2_exc:
                    _ow_enr["mlb_v2_hydration"] = {
                        "status": "NOT_READY",
                        "blockers": [f"MLB_V2_HYDRATION_ERROR:{type(_mlb_v2_exc).__name__}"],
                        "can_execute": False,
                    }
                    enrichment[_ow_row_id] = _ow_enr

            # NBA retains the legacy generic team-data path. MLB V2 deliberately
            # bypasses it because its 41-feature vector is the sole Stage-3 model.
            if _ow_sport == "NBA" and not any(
                _ow_enr.get(k)
                for k in ("home_win_pct", "away_win_pct", "home_power", "away_power")
            ):
'''
    replace_once(path, old, new)


def add_score_event_endpoint() -> None:
    path = ROOT / "app.py"
    anchor = '''if __name__ == "__main__":
    port = int(os.environ.get("PORT", 25643))
    app.run(host="0.0.0.0", port=port, debug=False)
'''
    addition = '''# ---------------------------------------------------------------------------
# MLB V2 governed event probability — probability publication only
# ---------------------------------------------------------------------------
@app.route("/wow/score-event", methods=["POST"])
@require_api_key
def wow_score_event():
    """Produce a governed MLB event probability without granting bet execution."""
    body = request.get_json(silent=True) or {}
    sport = str(body.get("sport") or "").upper().strip()
    market_family = str(body.get("market_family") or body.get("market") or "OUTRIGHT_WINNER").upper().strip()
    specialist = "wow.mlb-game-win-probability-expert"

    if sport != "MLB" or market_family not in {"OUTRIGHT_WINNER", "MONEYLINE", "ML"}:
        return jsonify({
            "ok": False,
            "code": "GOVERNED_EVENT_MODEL_UNSUPPORTED",
            "sport": sport,
            "market_family": market_family,
            "controlling_specialist": specialist if sport == "MLB" else None,
            "probability_publishable": False,
            "can_execute": False,
            "can_approve_bets": False,
        }), 400

    from gate_engine.mlb_v2_hydrator import canonical_team, hydrate_mlb_v2_enrichment
    from gate_engine.mlb_v2_runtime import artifact_health, score_home_probability

    health = artifact_health()
    if not health.get("healthy"):
        return jsonify({
            "ok": False,
            "code": "GOVERNED_EVENT_MODEL_UNAVAILABLE",
            "sport": "MLB",
            "market_family": "OUTRIGHT_WINNER",
            "controlling_specialist": specialist,
            "governed_probability_capability": "UNAVAILABLE",
            "governed_probability_status": "NOT_PRODUCED",
            "probability_publishable": False,
            "fallback": "SECTION_8A_MANUAL_ESTIMATE_LANE",
            "blockers": health.get("blockers") or ["MLB_V2_ARTIFACT_UNAVAILABLE"],
            "can_execute": False,
            "can_approve_bets": False,
        }), 409

    enrichment = hydrate_mlb_v2_enrichment(body, {})
    hyd = enrichment.get("mlb_v2_hydration") or {}
    features = enrichment.get("mlb_v2_feature_vector")
    if hyd.get("status") != "FEATURES_READY" or not isinstance(features, list):
        return jsonify({
            "ok": False,
            "code": "GOVERNED_EVENT_MODEL_UNAVAILABLE",
            "sport": "MLB",
            "market_family": "OUTRIGHT_WINNER",
            "controlling_specialist": specialist,
            "governed_probability_capability": "AVAILABLE",
            "governed_probability_status": "NOT_PRODUCED",
            "probability_publishable": False,
            "fallback": "SECTION_8A_MANUAL_ESTIMATE_LANE",
            "blockers": hyd.get("blockers") or ["MLB_V2_LIVE_FEATURES_UNAVAILABLE"],
            "hydration": hyd,
            "can_execute": False,
            "can_approve_bets": False,
        }), 409

    game_date = date.fromisoformat(str(hyd.get("game_date")))
    scored = score_home_probability(features, as_of=game_date)
    if not scored.get("ok") or not scored.get("probability_publishable"):
        return jsonify({
            "ok": False,
            "code": "GOVERNED_EVENT_MODEL_UNAVAILABLE",
            "sport": "MLB",
            "market_family": "OUTRIGHT_WINNER",
            "controlling_specialist": specialist,
            "governed_probability_capability": "AVAILABLE",
            "governed_probability_status": "NOT_PRODUCED",
            "probability_publishable": False,
            "fallback": "SECTION_8A_MANUAL_ESTIMATE_LANE",
            "blockers": scored.get("blockers") or ["MLB_V2_RUNTIME_GATE_FAILED"],
            "hydration": hyd,
            "drift": scored.get("drift"),
            "can_execute": False,
            "can_approve_bets": False,
        }), 409

    candidate = canonical_team(body.get("team") or body.get("participant") or body.get("player"))
    home_team = hyd.get("home_team")
    away_team = hyd.get("away_team")
    if candidate == home_team:
        candidate_side = "HOME"
        p = float(scored["home_probability"])
        lo = float(scored["home_probability_lower_bound"])
        hi = float(scored["home_probability_upper_bound"])
    elif candidate == away_team:
        candidate_side = "AWAY"
        p = float(scored["away_probability"])
        lo = 1.0 - float(scored["home_probability_upper_bound"])
        hi = 1.0 - float(scored["home_probability_lower_bound"])
    else:
        return jsonify({
            "ok": False,
            "code": "GOVERNED_EVENT_MODEL_INPUT_UNAVAILABLE",
            "sport": "MLB",
            "market_family": "OUTRIGHT_WINNER",
            "controlling_specialist": specialist,
            "probability_publishable": False,
            "blockers": ["MLB_V2_CANDIDATE_TEAM_UNRESOLVED"],
            "hydration": hyd,
            "can_execute": False,
            "can_approve_bets": False,
        }), 409

    return jsonify({
        "ok": True,
        "code": "GOVERNED_EVENT_MODEL_AVAILABLE",
        "sport": "MLB",
        "market_family": "OUTRIGHT_WINNER",
        "controlling_specialist": specialist,
        "governed_probability_capability": "AVAILABLE",
        "governed_probability_status": "PRODUCED",
        "probability_publishable": True,
        "model_id": scored.get("model_id"),
        "schema_version": scored.get("schema_version"),
        "candidate_team": candidate,
        "candidate_side": candidate_side,
        "probability": round(p, 6),
        "probability_lower_bound": round(max(0.01, lo), 6),
        "probability_upper_bound": round(min(0.99, hi), 6),
        "home_probability": round(float(scored["home_probability"]), 6),
        "away_probability": round(float(scored["away_probability"]), 6),
        "native_calibrated": True,
        "market_weight_in_point_probability": 0.0,
        "empirical_interval": scored.get("empirical_interval"),
        "drift": scored.get("drift"),
        "hydration": hyd,
        "fallback": None,
        "blockers": [],
        "can_execute": False,
        "can_approve_bets": False,
    }), 200


'''
    # app.py currently imports datetime's date? Ensure date is available without
    # disturbing its top-level import graph by using a local import line.
    addition = addition.replace(
        '    game_date = date.fromisoformat(str(hyd.get("game_date")))',
        '    from datetime import date as _date\n    game_date = _date.fromisoformat(str(hyd.get("game_date")))',
    )
    insert_before_once(path, anchor, addition)


def main() -> None:
    patch_requirements()
    patch_registry()
    patch_sport_model()
    patch_pipeline()
    patch_app_moneyline_hydration()
    add_score_event_endpoint()
    print("MLB_V2_GOVERNED_INTEGRATION_PATCH=APPLIED")


if __name__ == "__main__":
    main()
