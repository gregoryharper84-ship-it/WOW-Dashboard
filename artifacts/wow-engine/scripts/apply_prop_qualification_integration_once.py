from pathlib import Path


def load(path: str) -> str:
    return Path(path).read_text()


def save(path: str, text: str) -> None:
    Path(path).write_text(text)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start_marker: str, end_marker: str, replacement: str, label: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit(f"{label}: start marker missing")
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"{label}: end marker missing")
    return text[:start] + replacement + text[end:]


# 1. Immutable prediction ledger.
path = "ledger.py"
text = load(path)
text = replace_once(
    text,
    "from calibration import CalibrationStatus\n",
    "from calibration import CalibrationStatus\nfrom qualification_policy_v2 import classify_prop_probability\n",
    "ledger import",
)
start = '    money_resolved = (row.money_lane_status == "RESOLVED")'
end = '\n    return row\n'
replacement = '''    money_resolved = (row.money_lane_status == "RESOLVED")
    if not money_resolved and "money_lane_status != RESOLVED (payout unresolved)" not in row.blockers:
        row.blockers = list(row.blockers) + ["money_lane_status != RESOLVED (payout unresolved)"]

    qualification = classify_prop_probability(
        calibrated_probability=row.calibrated_probability,
        calibrated_lower_bound=row.calibrated_probability_lower_bound,
        calibration_status=row.calibration_status,
        blockers=row.data_gaps,
        probability_publishable=row.probability_publishable,
    )
    # Probability and money are separate objectives. The immutable
    # probability ceiling records the model verdict only.
    row.probability_ceiling = qualification.terminal_label
'''
text = replace_between(text, start, end, replacement, "ledger qualification block")
save(path, text)


# 2. Canonical /score-prop response semantics.
path = "api_prod_market.py"
text = load(path)
text = replace_once(
    text,
    "from prop_fitted_provider import PropFittedProviderUnavailable\n",
    "from prop_fitted_provider import PropFittedProviderUnavailable\nfrom qualification_policy_v2 import classify_prop_probability\nfrom prop_terminal_reducer_v2 import reduce_prop_terminal\n",
    "api qualification imports",
)
text = replace_once(
    text,
    '        "calibration_version": getattr(row, "calibration_version", None),\n        "bounds_method_version": getattr(row, "bounds_method_version", None),\n',
    '        "calibration_version": getattr(row, "calibration_version", None),\n        "calibrated_probability": getattr(row, "calibrated_probability", None),\n        "bounds_method_version": getattr(row, "bounds_method_version", None),\n',
    "api calibrated point evidence",
)
helper = '''def _probability_qualification(row: Any, market_lane: dict[str, Any], money_lane: dict[str, Any]) -> dict[str, Any]:
    qualification = classify_prop_probability(
        calibrated_probability=getattr(row, "calibrated_probability", None),
        calibrated_lower_bound=getattr(row, "calibrated_probability_lower_bound", None),
        calibration_status=getattr(row, "calibration_status", None),
        blockers=getattr(row, "data_gaps", None) or [],
        probability_publishable=bool(getattr(row, "probability_publishable", False)),
    )
    blockers = list(qualification.blockers)
    if market_lane.get("status") != "PASS":
        blockers.append("MARKET_DATA_UNAVAILABLE")
    if money_lane.get("status") != "PASS":
        blockers.append("PAYOUT_UNRESOLVED")
    terminal = reduce_prop_terminal(
        proposed_label=qualification.terminal_label,
        blockers=blockers,
        model_evaluated=True,
    )
    return {
        "terminal_label": terminal.terminal_label,
        "confidence_tier": qualification.confidence_tier,
        "rank_eligible": qualification.rank_eligible,
        "model_supported": qualification.model_supported,
        "model_evaluated": terminal.model_evaluated,
        "pick_rejected": terminal.pick_rejected,
        "verdict_class": terminal.verdict_class,
        "infrastructure_blocked": terminal.infrastructure_blocked,
        "downstream_money_evaluation_allowed": qualification.downstream_money_evaluation_allowed,
        "final_approved_allowed": False,
        "blockers": list(terminal.blockers),
        "can_execute": False,
    }


'''
marker = "def _aware_event_start(value: str) -> datetime:\n"
if text.count(marker) != 1:
    raise SystemExit("api qualification helper marker mismatch")
text = text.replace(marker, helper + marker, 1)
old = "    market_lane = _market_lane(result.row)\n    money_lane = _money_lane(result.row)\n    return {\n"
new = "    market_lane = _market_lane(result.row)\n    money_lane = _money_lane(result.row)\n    probability_qualification = _probability_qualification(result.row, market_lane, money_lane)\n    return {\n"
text = replace_once(text, old, new, "api response qualification")
text = replace_once(
    text,
    '        "model_evidence": _discrete_model_evidence(result),\n        "evidence": evidence,\n',
    '        "model_evidence": _discrete_model_evidence(result),\n        "probability_qualification": probability_qualification,\n        "terminal_label": probability_qualification["terminal_label"],\n        "pick_rejected": probability_qualification["pick_rejected"],\n        "evidence": evidence,\n',
    "api response fields",
)
save(path, text)


# 3. Pick Request terminal and reconciliation semantics.
path = "pick_request_runtime.py"
text = load(path)
text = replace_once(
    text,
    "from prop_auto_hydration import PropAutoHydrationError, auto_hydrate_prop_evidence\n",
    "from prop_auto_hydration import PropAutoHydrationError, auto_hydrate_prop_evidence\nfrom qualification_policy_v2 import classify_prop_probability\nfrom prop_terminal_reducer_v2 import EVENT_BLOCKERS, TRUE_MODEL_REJECTION_LABELS, reduce_prop_terminal\n",
    "pick request imports",
)
terminal_fn = '''def _terminal(
    row_key: str,
    status: str,
    code: str,
    *,
    detail: Optional[dict[str, Any]] = None,
    snapshot_id: Optional[str] = None,
    acquisition: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload = detail or {}
    blocker_codes = [code]
    for key in ("blocker_code", "failure_class"):
        if payload.get(key):
            blocker_codes.append(str(payload[key]))
    if payload.get("blocker"):
        blocker_codes.append(str(payload["blocker"]))
    model_evaluated = bool(payload.get("model_evaluated") is True or payload.get("model_evidence"))
    if code in EVENT_BLOCKERS or str(payload.get("blocker") or "") in EVENT_BLOCKERS:
        proposed_label = "NO_PLAY"
        blocker_codes.append(str(payload.get("blocker") or code))
    elif code in TRUE_MODEL_REJECTION_LABELS:
        proposed_label = code
    else:
        proposed_label = str(payload.get("terminal_label") or "MODEL_UNAVAILABLE")
    decision = reduce_prop_terminal(
        proposed_label=proposed_label,
        blockers=blocker_codes,
        model_evaluated=model_evaluated,
    )
    effective_status = "REJECTED" if (decision.pick_rejected or decision.verdict_class == "EVENT_INVALIDATED") else "HELD"
    return {
        "row_key": row_key,
        "terminal_status": effective_status,
        "code": code,
        "terminal_label": decision.terminal_label,
        "verdict_class": decision.verdict_class,
        "model_evaluated": decision.model_evaluated,
        "pick_rejected": decision.pick_rejected,
        "infrastructure_blocked": decision.infrastructure_blocked,
        "blockers": list(decision.blockers),
        "source_snapshot_id": snapshot_id,
        "detail": payload,
        "acquisition": acquisition or {"mode": "NOT_COMPLETED", "can_execute": False},
        "probability_publishable": False,
        "can_execute": False,
    }
'''
text = replace_between(text, "def _terminal(\n", "\n\ndef _auto_hydration_hold", terminal_fn, "pick request terminal function")

completed_helper = '''def _completed_scored_outcome(
    *,
    row_key: str,
    scored: dict[str, Any],
    snapshot_id: str,
    fingerprint: str,
    acquisition: dict[str, Any],
) -> dict[str, Any]:
    prediction = scored.get("prediction") or {}
    payload = scored.get("probability_qualification")
    if isinstance(payload, dict) and payload.get("terminal_label"):
        terminal_label = str(payload["terminal_label"])
        confidence_tier = str(payload.get("confidence_tier") or "UNKNOWN")
        rank_eligible = bool(payload.get("rank_eligible"))
        model_supported = bool(payload.get("model_supported"))
        money_allowed = bool(payload.get("downstream_money_evaluation_allowed"))
        blockers = list(payload.get("blockers") or [])
    else:
        qualification = classify_prop_probability(
            calibrated_probability=prediction.get("calibrated_probability"),
            calibrated_lower_bound=prediction.get("calibrated_probability_lower_bound"),
            calibration_status=prediction.get("calibration_status"),
            blockers=prediction.get("data_gaps") or [],
            probability_publishable=bool(scored.get("probability_publishable")),
        )
        terminal_label = qualification.terminal_label
        confidence_tier = qualification.confidence_tier
        rank_eligible = qualification.rank_eligible
        model_supported = qualification.model_supported
        money_allowed = qualification.downstream_money_evaluation_allowed
        blockers = list(qualification.blockers)
        lanes = scored.get("objective_lanes") or {}
        if (lanes.get("MARKET") or {}).get("status") != "PASS":
            blockers.append("MARKET_DATA_UNAVAILABLE")
        if (lanes.get("MONEY") or {}).get("status") != "PASS":
            blockers.append("PAYOUT_UNRESOLVED")
    decision = reduce_prop_terminal(
        proposed_label=terminal_label,
        blockers=blockers,
        model_evaluated=True,
    )
    return {
        "row_key": row_key,
        "terminal_status": "REJECTED" if decision.pick_rejected else "COMPLETED",
        "code": decision.terminal_label,
        "terminal_label": decision.terminal_label,
        "confidence_tier": confidence_tier,
        "rank_eligible": rank_eligible,
        "model_supported": model_supported,
        "model_evaluated": True,
        "pick_rejected": decision.pick_rejected,
        "verdict_class": decision.verdict_class,
        "infrastructure_blocked": decision.infrastructure_blocked,
        "downstream_money_evaluation_allowed": money_allowed,
        "source_snapshot_id": snapshot_id,
        "evidence_fingerprint": fingerprint,
        "acquisition": acquisition,
        "result": scored,
        "probability_publishable": bool(scored.get("probability_publishable")),
        "can_execute": False,
    }


'''
telemetry_marker = "def _telemetry(outcomes: list[dict[str, Any]]) -> dict[str, int]:\n"
if text.count(telemetry_marker) != 1:
    raise SystemExit("pick request telemetry marker mismatch")
text = text.replace(telemetry_marker, completed_helper + telemetry_marker, 1)
text = replace_once(
    text,
    '        if outcome.get("terminal_status") == "COMPLETED":\n            model_completed += 1\n',
    '        if outcome.get("model_evaluated") is True or outcome.get("terminal_status") == "COMPLETED":\n            model_completed += 1\n',
    "pick request telemetry model count",
)
completed_marker = "\n        completed = sum(\n"
completed_pos = text.find(completed_marker)
if completed_pos < 0:
    raise SystemExit("pick request completed-count marker missing")
success_start = text.rfind("            outcomes.append(\n", 0, completed_pos)
if success_start < 0:
    raise SystemExit("pick request success append missing")
success_replacement = '''            outcomes.append(
                _completed_scored_outcome(
                    row_key=row_key,
                    scored=scored,
                    snapshot_id=snapshot_id,
                    fingerprint=fingerprint,
                    acquisition=acquisition,
                )
            )
'''
text = text[:success_start] + success_replacement + text[completed_pos:]
rows_in_marker = "        rows_in = len(batch.rows)\n"
if text.count(rows_in_marker) != 1:
    raise SystemExit("pick request rows_in marker mismatch")
counts = '        pick_rejected_count = sum(1 for outcome in outcomes if outcome.get("pick_rejected") is True)\n        infrastructure_blocked_count = sum(1 for outcome in outcomes if outcome.get("infrastructure_blocked") is True)\n'
text = text.replace(rows_in_marker, counts + rows_in_marker, 1)
text = replace_once(
    text,
    '            "rows_rejected": rejected,\n            "reconciliation_pass": reconciliation_pass,\n',
    '            "rows_rejected": rejected,\n            "pick_rejected_count": pick_rejected_count,\n            "infrastructure_blocked_count": infrastructure_blocked_count,\n            "reconciliation_pass": reconciliation_pass,\n',
    "pick request response counts",
)
save(path, text)


# 4. Expand acquisition blocker coverage.
path = "prop_terminal_reducer_v2.py"
text = load(path)
text = replace_once(
    text,
    '    "PROP_EVIDENCE_PERSISTENCE_UNAVAILABLE",\n    "STALE_EVIDENCE",\n',
    '    "PROP_EVIDENCE_PERSISTENCE_UNAVAILABLE",\n    "PROP_PLAYER_IDENTITY_UNRESOLVED",\n    "PROP_EVENT_IDENTITY_CONFLICT",\n    "MLB_RECENT_STARTS_INSUFFICIENT",\n    "MLB_STARTER_STATUS_UNRESOLVED",\n    "STALE_EVIDENCE",\n',
    "terminal reducer acquisition blockers",
)
save(path, text)


# 5. Modernize Pick Request test fixture and changed assertions structurally.
path = "test_pick_request_runtime.py"
text = load(path)
score_start = text.find("    def score(req, x_wow_model_identity=None):\n")
score_end = text.find("\n\n    monkeypatch.setattr", score_start)
if score_start < 0 or score_end < 0:
    raise SystemExit("pick request test score fixture boundary missing")
score_fn = '''    def score(req, x_wow_model_identity=None):
        scored.append((req, x_wow_model_identity))
        return {
            "ok": True,
            "prediction": {
                "prediction_id": "00000000-0000-0000-0000-000000000001",
                "calibrated_probability": 0.63,
                "calibrated_probability_lower_bound": 0.56,
                "calibration_status": "PRECALIBRATION_SHRINKAGE",
            },
            "model_evidence": {
                "calibrated_probability": 0.63,
                "calibrated_probability_lower_bound": 0.56,
            },
            "probability_qualification": {
                "terminal_label": "MODEL_QUALIFIED_HOLD",
                "confidence_tier": "STANDARD",
                "rank_eligible": True,
                "model_supported": True,
                "downstream_money_evaluation_allowed": False,
                "blockers": ["MARKET_DATA_UNAVAILABLE", "PAYOUT_UNRESOLVED"],
            },
            "probability_publishable": True,
            "can_execute": False,
        }
'''
text = text[:score_start] + score_fn + text[score_end:]
text = text.replace('"MODEL_QUALIFIED"', '"MODEL_QUALIFIED_HOLD"')

fn_start = text.find("def test_bad_row_cannot_erase_good_sibling_and_reconciliation_is_exact")
fn_end = text.find("\n\ndef test_missing_evidence_auto_hydrates", fn_start)
if fn_start < 0 or fn_end < 0:
    raise SystemExit("pick request bad-row regression boundary missing")
bad_fn = text[fn_start:fn_end]
bad_fn = bad_fn.replace('assert body["rows_held"] == 0', 'assert body["rows_held"] == 1')
bad_fn = bad_fn.replace('assert body["rows_rejected"] == 1', 'assert body["rows_rejected"] == 0')
bad_fn = bad_fn.replace('assert by_key["bad"]["terminal_status"] == "REJECTED"', 'assert by_key["bad"]["terminal_status"] == "HELD"')
bad_fn += '\n    assert by_key["bad"]["pick_rejected"] is False\n    assert by_key["bad"]["verdict_class"] == "ACQUISITION_BLOCKED"\n    assert body["pick_rejected_count"] == 0\n    assert body["infrastructure_blocked_count"] >= 1'
text = text[:fn_start] + bad_fn + text[fn_end:]
save(path, text)
