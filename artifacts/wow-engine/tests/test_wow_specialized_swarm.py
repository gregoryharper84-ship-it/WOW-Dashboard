"""Offline contract and negative tests for the read-only WOW Swarm adapter."""
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.wow_specialized_swarm import (  # noqa: E402
    WORKERS, antigravity_prompt, build_manifest, validate_submission,
)

NOW = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)
EVENT = {
    "sport_key": "basketball_nba",
    "official_event_id": "NBA-2026-TEST-0001",
    "commence_time": "2026-11-24T01:00:00Z",
    "home_team": "Home", "away_team": "Away", "can_execute": False,
}


def _mock_contracts():
    modules = {k: ModuleType(k) for k in (
        "v17", "v17.matchup_discovery", "v17.sport_scout_registry",
        "v17.sport_research_brief")}
    modules["v17.matchup_discovery"].profile_for = lambda sport: "BASKETBALL" if sport == "basketball_nba" else None
    modules["v17.sport_scout_registry"].scout_team_for = lambda sport: SimpleNamespace(
        team_id="NBA_SCOUT_TEAM", sport_key=sport)
    modules["v17.sport_research_brief"].SUPPORTED_RESEARCH_WORKERS = WORKERS
    modules["v17.sport_research_brief"].build_research_brief = lambda event, worker, now: {
        "worker_id": worker, "official_event_id": event["official_event_id"],
        "can_execute": False, "prediction_authority": False,
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
        "can_execute": False,
        "facts": [{"official_event_id": EVENT["official_event_id"],
                   "feature": "lineup", "value": "announced",
                   "source_id": "league-official", "source_class": "LEAGUE_OFFICIAL",
                   "source_rights_status": "REVIEW_REQUIRED",
                   "captured_at": "2026-11-22T14:55:00Z"}],
        "missing_evidence": ["confirmed starting lineup"], "conflicts": [],
    }


def test_research_manifest_reuses_worker_ids_and_caps_concurrency():
    manifest = _manifest()
    assert [len(w["worker_ids"]) for w in manifest["waves"]] == [2, 2, 1]
    assert [w["worker_id"] for w in manifest["workers"]] == list(WORKERS)
    assert manifest["coverage_type"] == "SPORT_SPECIFIC_RESEARCH"
    assert manifest["handoff"]["may_publish_or_qualify"] is False
    assert manifest["policy"]["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert manifest["policy"]["can_execute"] is False
    assert antigravity_prompt(manifest).startswith("/swarm ")
    assert "@matchup=" in antigravity_prompt(manifest)


def test_bounded_runs_are_deterministic_across_reference_times():
    with _mock_contracts():
        first = build_manifest(EVENT, max_parallel=1, now=NOW)
        second = build_manifest(EVENT, max_parallel=3,
                                now=datetime(2026, 11, 23, tzinfo=timezone.utc))
    assert first["run_id"] == second["run_id"]
    assert max(len(w["worker_ids"]) for w in first["waves"]) == 1
    assert max(len(w["worker_ids"]) for w in second["waves"]) == 3


@pytest.mark.parametrize("patch_event", [
    {"can_execute": True}, {"can_execute": None},
    {"official_event_id": ""}, {"sport_key": "unknown_sport"},
    {"commence_time": "2026-11-24T01:00:00"},
    {"commence_time": "2026-11-21T01:00:00Z"},
])
def test_bad_inputs_fail_closed(patch_event):
    with _mock_contracts(), pytest.raises(ValueError):
        build_manifest({**EVENT, **patch_event}, now=NOW)


def test_good_research_receipt_is_valid_not_prediction_authority():
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
    lambda s: s.update(run_id="wrong"),
    lambda s: s.update(can_execute=True),
    lambda s: s.update(research_ceiling="CERTIFIED"),
    lambda s: s.update(fitted_probability=0.71),
    lambda s: s["facts"][0].update(official_event_id="wrong-game"),
    lambda s: s["facts"][0].update(captured_at="2026-11-25T01:00:00Z"),
    lambda s: s["facts"][0].update(captured_at="2026-11-22T14:00:00"),
    lambda s: s["facts"][0].update(source_id=""),
    lambda s: s["facts"][0].update(source_rights_status="CLEARED_FOR_TRAINING"),
    lambda s: s["facts"][0].update(value={"estimated_probability": 0.8}),
    lambda s: s["facts"][0].update(conflict=True),
])
def test_unauthorized_research_receipts_are_rejected(mutation):
    manifest = _manifest()
    submission = _submission(manifest)
    mutation(submission)
    assert validate_submission(manifest, submission, now=NOW)["valid_report"] is False


def test_empty_and_malformed_reports_do_not_validate():
    manifest = _manifest()
    submission = _submission(manifest)
    submission["facts"] = []
    submission["missing_evidence"] = []
    assert validate_submission(manifest, submission, now=NOW)["valid_report"] is False
    assert validate_submission(manifest, None, now=NOW)["valid_report"] is False


def test_integration_with_canonical_v17_modules_when_present():
    if not (ROOT / "v17/sport_research_brief.py").is_file():
        pytest.skip("repository V17 modules unavailable in detached candidate bundle")
    manifest = build_manifest(EVENT, now=NOW)
    assert manifest["scout_team_id"] == "NBA_SCOUT_TEAM"
    assert len(manifest["workers"]) == len(WORKERS)
    assert manifest["workers"][2]["brief"]["research_role"] == "MATCHUP_CONTEXT"
