"""WOW-native Swarm: zero-credit, bounded workers and fail-closed evidence tests."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.wow_specialized_swarm import (  # noqa: E402
    WORKERS, build_manifest, run_swarm, validate_submission,
)
import tools.wow_specialized_swarm as swarm  # noqa: E402

NOW = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)
EVENT = {
    "sport_key": "basketball_nba", "official_event_id": "nba-test-0001",
    "commence_time": "2026-11-24T01:00:00Z", "home_team": "Home",
    "away_team": "Away", "can_execute": False,
}
REQ = {
    "BASELINE": ("opponent_adjusted_strength", "recent_form_vs_baseline", "pace_or_event_volume"),
    "PERSONNEL": ("availability_and_lineups", "usage_or_opportunity_shift", "depth_and_rotation"),
    "SITUATION": ("rest_travel_fatigue",),
    "GAME_SCRIPT": ("leading_trailing_behaviors",),
    "MARKET": ("exact_current_quote",),
    "SPORT_SPECIFIC": ("minutes_and_foul_rotation", "shot_zones_vs_allowed"),
}


def observed(feature, value, *, event_id=EVENT["official_event_id"],
             rights="CLEARED_FOR_RESEARCH", captured_at="2026-11-22T14:50:00Z",
             source_class="ESTABLISHED_STATS_PROVIDER"):
    return {"official_event_id": event_id, "feature": feature, "value": value,
            "source_id": "stats-source", "source_class": source_class,
            "captured_at": captured_at, "confirmed": True,
            "source_rights_status": rights}


def sample():
    return {**EVENT, "matchup_metric_evidence": {
        "opponent_adjusted_strength": observed("opponent_adjusted_strength", 0),
        "availability_and_lineups": observed("availability_and_lineups", {"away": "confirmed"}),
        "minutes_and_foul_rotation": observed("minutes_and_foul_rotation", 24),
    }}


def _mock_contracts():
    modules = {key: ModuleType(key) for key in (
        "v17", "v17.matchup_discovery", "v17.sport_scout_registry",
        "v17.sport_research_brief")}
    matchup = modules["v17.matchup_discovery"]
    matchup.profile_for = lambda sport: "BASKETBALL" if sport == "basketball_nba" else None
    matchup.feature_requirements = lambda sport: REQ
    matchup._metric_rows = lambda candidate: candidate.get("matchup_metric_evidence", {})
    def screen(row, candidate, cutoff, now, feature):
        if row.get("official_event_id") != candidate["official_event_id"]:
            return "EVENT_ID_MISMATCH"
        parsed = datetime.fromisoformat(row["captured_at"].replace("Z", "+00:00"))
        if parsed >= cutoff or parsed > now:
            return "TIMESTAMP_NOT_VALID_PREGAME"
        if row.get("confirmed") is not True:
            return "STALE_OR_UNCONFIRMED_SOURCE"
        return "RESEARCH_SOURCE_SCREENED"
    matchup._screen_row = screen

    def discover(candidate, now=None):
        metrics = matchup._metric_rows(candidate)
        screened = [k for k in metrics if k in {x for v in REQ.values() for x in v}]
        missing = [k for v in REQ.values() for k in v if k not in screened]
        return {
            "official_event_id": candidate["official_event_id"],
            "screened_features": screened, "missing_critical_features": missing,
            "invalid_or_stale_features": {}, "can_execute": False,
            "prediction_authority": False, "review_requested": True,
        }
    matchup.discover_matchup = discover
    modules["v17.sport_scout_registry"].scout_team_for = lambda sport: SimpleNamespace(
        team_id="NBA_SCOUT_TEAM", sport_key=sport)
    briefs = modules["v17.sport_research_brief"]
    briefs.SUPPORTED_RESEARCH_WORKERS = WORKERS
    briefs.build_research_brief = lambda event, worker, now=None: {
        "worker_id": worker, "can_execute": False, "prediction_authority": False,
        "research_role": "MATCHUP_CONTEXT" if worker == WORKERS[2] else "LOCAL_RESEARCH",
    }
    return patch.dict(sys.modules, modules)


def _manifest():
    with _mock_contracts():
        return build_manifest(EVENT, now=NOW)


def _submission(manifest):
    return {
        "run_id": manifest["run_id"], "sport_key": EVENT["sport_key"],
        "official_event_id": EVENT["official_event_id"],
        "worker_id": WORKERS[0], "research_ceiling": "RESEARCH_INTEREST",
        "can_execute": False, "facts": [{k: v for k, v in observed("opponent_adjusted_strength", 0).items() if k not in ("confirmed",)}],
        "missing_evidence": [], "conflicts": [],
    }


def test_manifest_is_native_and_bounded_no_antigravity_or_vendor_calls():
    manifest = _manifest()
    assert [len(w["worker_ids"]) for w in manifest["waves"]] == [2, 2, 1]
    assert [worker["worker_id"] for worker in manifest["workers"]] == list(WORKERS)
    assert manifest["provider"] == "WOW_NATIVE_PYTHON"
    assert manifest["paid_provider_calls"] == manifest["network_calls"] == 0
    assert manifest["policy"]["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert manifest["policy"]["can_execute"] is False


def test_manifest_uses_event_identity_not_local_time_for_stable_plan_id():
    with _mock_contracts():
        a = build_manifest(EVENT, now=NOW, max_parallel=1)
        b = build_manifest(EVENT, now=NOW + timedelta(hours=1), max_parallel=3)
    assert a["run_id"] == b["run_id"]
    assert [len(w["worker_ids"]) for w in a["waves"]] == [1] * 5
    assert [len(w["worker_ids"]) for w in b["waves"]] == [3, 2]


@pytest.mark.parametrize("patch_event", [
    {"can_execute": True}, {"can_execute": None}, {"official_event_id": ""},
    {"sport_key": "unsupported_sport"}, {"commence_time": "2026-11-24T01:00:00"},
    {"commence_time": "2026-11-21T01:00:00Z"},
    {"official_event_id": ["unhashable"]},
])
def test_bad_candidate_fails_closed(patch_event):
    with _mock_contracts(), pytest.raises(ValueError):
        build_manifest({**EVENT, **patch_event}, now=NOW)


@pytest.mark.parametrize("invalid_cap", [0, 4, -1, 1.0, True])
def test_invalid_concurrency_caps_rejected(invalid_cap):
    with _mock_contracts(), pytest.raises(ValueError):
        build_manifest(EVENT, now=NOW, max_parallel=invalid_cap)


def test_native_run_executes_five_workers_and_delivers_research_only_handoff():
    with _mock_contracts():
        receipt = run_swarm(sample(), now=NOW)
    assert receipt["status"] == "RESEARCH_ONLY_HOLD"  # missing critical evidence
    assert receipt["waves_completed"] == 3
    assert len(receipt["worker_reports"]) == 5
    assert all(w["all_structurally_valid"] for w in receipt["wave_reviews"])
    assert receipt["handoff"]["source_screened_research_facts"]
    assert {f["feature"] for f in receipt["handoff"]["source_screened_research_facts"]} == {
        "opponent_adjusted_strength", "availability_and_lineups", "minutes_and_foul_rotation"}
    assert receipt["external_llm_calls"] == receipt["network_calls"] == receipt["paid_provider_calls"] == 0
    assert receipt["handoff"]["may_publish_or_qualify"] is False
    assert receipt["handoff"]["specialist_rescore_verified"] is False
    assert receipt["persisted_to_supabase"] is False
    assert receipt["policy"]["can_execute"] is False


def test_missing_source_is_a_hold_not_an_invented_scout_result():
    with _mock_contracts():
        receipt = run_swarm(EVENT, now=NOW)
    assert receipt["status"] == "RESEARCH_ONLY_HOLD"
    assert not receipt["handoff"]["source_screened_research_facts"]
    assert "NO_SCREENED_SOURCE_EVIDENCE" in receipt["handoff"]["missing_evidence"]


def test_unapproved_source_rights_prevent_fact_handoff():
    candidate = sample()
    for item in candidate["matchup_metric_evidence"].values():
        item["source_rights_status"] = "REVIEW_REQUIRED"
    with _mock_contracts():
        receipt = run_swarm(candidate, now=NOW)
    assert receipt["handoff"]["source_screened_research_facts"] == []
    assert receipt["handoff"]["source_rights_review_required"] is True
    assert receipt["status"] == "RESEARCH_ONLY_HOLD"


def test_bad_sibling_row_is_quarantined_despite_feature_screened():
    candidate = sample()
    valid = candidate["matchup_metric_evidence"]["opponent_adjusted_strength"]
    invalid = observed("opponent_adjusted_strength", 0.75, event_id="other-match")
    candidate["matchup_metric_evidence"]["opponent_adjusted_strength"] = [valid, invalid]
    with _mock_contracts():
        receipt = run_swarm(candidate, now=NOW)
    forwarded = [f for f in receipt["handoff"]["source_screened_research_facts"]
                 if f["feature"] == "opponent_adjusted_strength"]
    assert len(forwarded) == 1
    assert forwarded[0]["official_event_id"] == EVENT["official_event_id"]
    assert any("EVENT_ID_MISMATCH" in reason for reason in receipt["handoff"]["conflicts"])
    assert receipt["status"] == "RESEARCH_ONLY_HOLD"


def test_unconfirmed_sibling_row_is_not_laundered():
    candidate = sample()
    original = candidate["matchup_metric_evidence"]["availability_and_lineups"]
    candidate["matchup_metric_evidence"]["availability_and_lineups"] = [
        original, {**original, "confirmed": False, "source_id": "unconfirmed-beat"}]
    with _mock_contracts():
        receipt = run_swarm(candidate, now=NOW)
    forwarded = [f for f in receipt["handoff"]["source_screened_research_facts"]
                 if f["feature"] == "availability_and_lineups"]
    assert len(forwarded) == 1
    assert any("STALE_OR_UNCONFIRMED_SOURCE" in reason for reason in receipt["handoff"]["missing_evidence"])


def test_worker_wave_concurrency_never_exceeds_cap(monkeypatch):
    real = swarm._local_worker
    lock = threading.Lock()
    running = 0
    maximum = 0
    def tracked(*args, **kwargs):
        nonlocal running, maximum
        with lock:
            running += 1
            maximum = max(maximum, running)
        try:
            time.sleep(0.008)
            return real(*args, **kwargs)
        finally:
            with lock:
                running -= 1
    monkeypatch.setattr(swarm, "_local_worker", tracked)
    with _mock_contracts():
        receipt = run_swarm(sample(), now=NOW, max_parallel=2)
    assert 1 <= maximum <= 2
    assert receipt["max_parallel"] == 2


def test_prohibited_measurement_does_not_escape_as_fact():
    candidate = sample()
    candidate["matchup_metric_evidence"]["exact_current_quote"] = observed(
        "exact_current_quote", {"fitted_probability": 0.96})
    with _mock_contracts():
        receipt = run_swarm(candidate, now=NOW)
    assert receipt["status"] == "RESEARCH_ONLY_HOLD"
    assert all(f["feature"] != "exact_current_quote" for f in receipt["handoff"]["source_screened_research_facts"])
    assert any("INVALID_OR_CROSS_EVENT_FACT" in x for x in receipt["handoff"]["conflicts"])


def test_trusted_worker_exception_fails_closed_not_fake_completion(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("worker failed")
    monkeypatch.setattr(swarm, "_local_worker", fail)
    with _mock_contracts(), pytest.raises(RuntimeError, match="worker failed"):
        run_swarm(sample(), now=NOW)


def test_valid_research_receipt_is_still_not_a_prediction_or_source_verification():
    manifest = _manifest()
    result = validate_submission(manifest, _submission(manifest), now=NOW)
    assert result["valid_report"] is True
    assert result["facts_screened_structurally"] == 1
    assert result["may_fit_probability"] is False
    assert result["independent_source_verification"] is False
    assert result["may_publish"] is False


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(official_event_id="other-game"),
    lambda s: s.update(worker_id="unauthorized-worker"),
    lambda s: s.update(worker_id=[WORKERS[0]]),
    lambda s: s.update(worker_id={"name": WORKERS[0]}),
    lambda s: s.update(run_id="wrong"),
    lambda s: s.update(can_execute=True),
    lambda s: s.update(research_ceiling="CERTIFIED"),
    lambda s: s.update(fitted_probability=0.71),
    lambda s: s["facts"][0].update(official_event_id="wrong"),
    lambda s: s["facts"][0].update(captured_at="2026-11-25T01:00:00Z"),
    lambda s: s["facts"][0].update(captured_at="2026-11-22T14:00:00"),
    lambda s: s["facts"][0].update(source_id=""),
    lambda s: s["facts"][0].update(source_rights_status="CLEARED_FOR_TRAINING"),
    lambda s: s["facts"][0].update(value={"estimated_probability": 0.8}),
    lambda s: s["facts"][0].update(feature="fitted_probability", value=0.8),
    lambda s: s["facts"][0].update(feature="Model Win Probability", value=0.8),
    lambda s: s["facts"][0].update(conflict=True),
    lambda s: s.update(missing_evidence=[{}]),
    lambda s: s.update(conflicts=[False]),
])
def test_malformed_or_unauthorized_reports_are_rejected(mutation):
    manifest = _manifest()
    submission = _submission(manifest)
    mutation(submission)
    assert validate_submission(manifest, submission, now=NOW)["valid_report"] is False


def test_empty_report_without_hold_is_not_success():
    manifest = _manifest()
    submission = _submission(manifest)
    submission["facts"] = []
    assert validate_submission(manifest, submission, now=NOW)["valid_report"] is False
    assert validate_submission(manifest, None, now=NOW)["valid_report"] is False


def test_native_cli_uses_sibling_v17_without_pythonpath(tmp_path):
    """Root-invoked local worker cannot depend on hidden developer PYTHONPATH."""
    import shutil
    tools = tmp_path / "artifacts/wow-engine/tools"
    package = tmp_path / "artifacts/wow-engine/v17"
    tools.mkdir(parents=True)
    package.mkdir(parents=True)
    shutil.copyfile(ROOT / "tools/wow_specialized_swarm.py", tools / "wow_specialized_swarm.py")
    (package / "__init__.py").write_text("")
    (package / "matchup_discovery.py").write_text(
        "def profile_for(sport): return 'BASKETBALL'\n"
        f"def feature_requirements(sport): return {repr(REQ)}\n"
        "def _metric_rows(candidate): return candidate.get('matchup_metric_evidence', {})\n"
        "def _screen_row(row, candidate, cutoff, now, feature): return 'RESEARCH_SOURCE_SCREENED'\n"
        "def discover_matchup(candidate, now=None):\n"
        "    return {'official_event_id': candidate['official_event_id'], "
        "'screened_features': [], 'missing_critical_features': ['lineup'], "
        "'invalid_or_stale_features': {}, 'can_execute': False, 'prediction_authority': False}\n"
    )
    (package / "sport_scout_registry.py").write_text(
        "from types import SimpleNamespace\n"
        "def scout_team_for(sport): return SimpleNamespace(team_id='NBA_SCOUT_TEAM', sport_key=sport)\n")
    (package / "sport_research_brief.py").write_text(
        f"SUPPORTED_RESEARCH_WORKERS = {repr(WORKERS)}\n"
        "def build_research_brief(event, worker, now=None):\n"
        "    return {'worker_id': worker, 'prediction_authority': False, 'can_execute': False}\n")
    event = {**EVENT, "commence_time": "2099-01-01T00:00:00Z"}
    payload = tmp_path / "event.json"
    payload.write_text(json.dumps(event))
    env = {**os.environ, "PYTHONPATH": ""}
    proc = subprocess.run([
        sys.executable, str(tools / "wow_specialized_swarm.py"),
        "--candidate", str(payload)
    ], cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    receipt = json.loads(proc.stdout)
    assert receipt["schema_version"] == "wow.v17.native_swarm.research.v1"
    assert receipt["network_calls"] == 0
    assert receipt["status"] == "RESEARCH_ONLY_HOLD"


def test_real_canonical_v17_modules_in_hosted_repo():
    if not (ROOT / "v17/sport_research_brief.py").is_file():
        pytest.skip("needs real V17 checkout; executes in hosted CI")
    event = sample()
    receipt = run_swarm(event, now=NOW)
    assert receipt["scout_team_id"] == "NBA_SCOUT_TEAM"
    assert len(receipt["worker_reports"]) == 5
    assert all(wave["all_structurally_valid"] for wave in receipt["wave_reviews"])
    assert receipt["handoff"]["may_publish_or_qualify"] is False
    assert receipt["external_llm_calls"] == 0