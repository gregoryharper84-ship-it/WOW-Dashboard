from pathlib import Path

p = Path(__file__).resolve().parents[1] / "mlb_v2_hydrator.py"
text = p.read_text()

old_import = "from mlb_v2_features import FEATURE_NAMES, build_feature_vector, starter_summary, team_summary\n"
new_import = old_import + "from mlb_v2_incremental import advance_state_to_target\n"
assert text.count(old_import) == 1
assert "from mlb_v2_incremental import advance_state_to_target" not in text
text = text.replace(old_import, new_import, 1)

old_block = '''    blockers: list[str] = []
    cutoff = str(state.get("cutoff_exclusive") or "")
    if cutoff != target.isoformat():
        blockers.append(f"MLB_V2_STATE_STALE:cutoff_exclusive={cutoff}:target={target.isoformat()}")
    if not bool(state.get("strict_prior_date_only")):
        blockers.append("MLB_V2_STATE_LEAKAGE_ATTESTATION_MISSING")
    if blockers:
        enrichment["mlb_v2_hydration"] = {"status": "NOT_READY", "blockers": blockers, "can_execute": False}
        return enrichment
'''
new_block = '''    blockers: list[str] = []
    refresh_meta: dict[str, Any] = {
        "status": "NOT_NEEDED",
        "from_cutoff": str(state.get("cutoff_exclusive") or ""),
        "to_cutoff": target.isoformat(),
        "days_advanced": 0,
        "games_added": 0,
        "source": "BUNDLED_OR_ALREADY_ADVANCED_STATE",
    }
    cutoff = str(state.get("cutoff_exclusive") or "")
    # If this Render process is carrying an older bundled cutoff, catch it up
    # transactionally from official final MLB results strictly before target.
    # The state lock serializes refreshes across concurrent scoring requests.
    if cutoff and cutoff < target.isoformat():
        try:
            with _state_lock:
                refresh_meta = advance_state_to_target(state, target)
        except Exception as exc:
            enrichment["mlb_v2_hydration"] = {
                "status": "NOT_READY",
                "blockers": [f"MLB_V2_INCREMENTAL_REFRESH_FAILED:{type(exc).__name__}:{exc}"],
                "state_refresh": refresh_meta,
                "strict_prior_date_only": True,
                "same_day_results_used": False,
                "can_execute": False,
            }
            return enrichment
    cutoff = str(state.get("cutoff_exclusive") or "")
    if cutoff != target.isoformat():
        blockers.append(f"MLB_V2_STATE_STALE:cutoff_exclusive={cutoff}:target={target.isoformat()}")
    if not bool(state.get("strict_prior_date_only")):
        blockers.append("MLB_V2_STATE_LEAKAGE_ATTESTATION_MISSING")
    if blockers:
        enrichment["mlb_v2_hydration"] = {
            "status": "NOT_READY",
            "blockers": blockers,
            "state_refresh": refresh_meta,
            "can_execute": False,
        }
        return enrichment
'''
assert text.count(old_block) == 1
text = text.replace(old_block, new_block, 1)

old_tail = '''        "same_day_results_used": False,
        "state_results_through": state.get("results_through"),
        "can_execute": False,
'''
new_tail = '''        "same_day_results_used": False,
        "state_results_through": state.get("results_through"),
        "state_refresh": refresh_meta,
        "can_execute": False,
'''
assert text.count(old_tail) == 1
text = text.replace(old_tail, new_tail, 1)

p.write_text(text)
print("MLB_V2_INCREMENTAL_HYDRATOR_PATCH=APPLIED")
