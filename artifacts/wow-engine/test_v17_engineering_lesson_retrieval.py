"""Deterministic, offline regressions for verified AMRT lesson retrieval."""
import copy
import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).parent / "v17" / "engineering_lesson_retrieval.py"
spec = importlib.util.spec_from_file_location("v17_engineering_lesson_retrieval", MODULE)
lesson = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lesson)


def verified_record(incident_id="PM-2026-09-25-001", category="OBSERVABILITY_CORRELATION"):
    return {
        "postmortem_id": incident_id,
        "state": "VERIFIED_CLOSED",
        "root_cause_status": "CONFIRMED",
        "primary_subsystem": "WOW_HOST_ORCHESTRATION",
        "updated_utc": "2026-09-25T19:31:23Z",
        "postmortem_path": f"artifacts/wow-engine/v17/postmortems/{incident_id}.md",
        "review": {"status": "PASS"},
        "qa": {"status": "PASS"},
        "release": {"status": "PRODUCTION_VERIFIED", "production_verified": True, "deployed_commit": "4ec2bed8"},
        "reporter_closure": {"status": "FIXED_VERIFIED"},
        "learning": {
            "failure_category": category,
            "architecture_lesson": "Correlate internal Action callers explicitly.",
            "preventive_control": "Require deterministic request-ID stability tests.",
            "regression_test_added": "test_live_probe_authenticates_more_and_less",
        },
    }


def ledger(*rows):
    return {
        "runtime_generation": "V17_ACTIVE",
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
        "records": list(rows),
    }


def test_exact_category_retrieves_verified_lessons_only_and_is_advisory():
    result = lesson.retrieve_verified_lessons(ledger(verified_record()), failure_category="observability_correlation")
    assert len(result) == 1
    assert result[0]["incident_id"] == "PM-2026-09-25-001"
    assert result[0]["advisory_only"] is True
    assert result[0]["automatic_adoption_allowed"] is False


@pytest.mark.parametrize("field,value", [
    ("state", "DEPLOYED_PENDING_VERIFY"),
    ("root_cause_status", "NOT_CONFIRMED"),
    ("review", {"status": "PENDING"}),
    ("qa", {"status": "FAIL"}),
    ("release", {"status": "PRODUCTION_VERIFIED", "production_verified": False, "deployed_commit": "4ec2bed8"}),
    ("release", {"status": "PRODUCTION_VERIFIED", "production_verified": True}),
    ("reporter_closure", {"status": "PENDING"}),
])
def test_unverified_closures_cannot_be_retrieved(field, value):
    item = verified_record()
    item[field] = value
    assert lesson.retrieve_verified_lessons(ledger(item), subsystem="WOW_HOST_ORCHESTRATION") == []


def test_legacy_state_only_closed_is_not_trusted():
    assert lesson.retrieve_verified_lessons(
        ledger({"state": "VERIFIED_CLOSED", "postmortem_id": "OLD"}),
        subsystem="WOW_HOST_ORCHESTRATION"
    ) == []


def test_missing_learning_fields_does_not_promote():
    item = verified_record()
    del item["learning"]["regression_test_added"]
    assert lesson.retrieve_verified_lessons(ledger(item), failure_category="OBSERVABILITY_CORRELATION") == []


def test_category_and_subsystem_both_require_match():
    d = ledger(verified_record())
    assert lesson.retrieve_verified_lessons(d, failure_category="UNRELATED", subsystem="WOW_HOST_ORCHESTRATION") == []
    assert len(lesson.retrieve_verified_lessons(
        d, failure_category="OBSERVABILITY_CORRELATION", subsystem="WOW_HOST_ORCHESTRATION"
    )) == 1


def test_dedup_sorted_recent_and_no_mutation():
    newer = verified_record("PM-2026-09-26-001")
    newer["updated_utc"] = "2026-09-26T19:31:23Z"
    original = ledger(verified_record(), newer, copy.deepcopy(newer))
    snapshot = copy.deepcopy(original)
    results = lesson.retrieve_verified_lessons(original, subsystem="WOW_HOST_ORCHESTRATION", limit=1)
    assert [x["incident_id"] for x in results] == ["PM-2026-09-26-001"]
    assert original == snapshot


@pytest.mark.parametrize("key,value", [
    ("runtime_generation", "V16"),
    ("terminal_authority", "OTHER"),
    ("can_execute", True),
    ("records", None),
])
def test_invalid_ledger_fails_closed(key, value):
    d = ledger(verified_record())
    d[key] = value
    with pytest.raises(ValueError):
        lesson.retrieve_verified_lessons(d, subsystem="WOW_HOST_ORCHESTRATION")


@pytest.mark.parametrize("category,subsystem,limit", [
    (None, None, 1),
    ("X", None, 0),
    ("X", None, 21),
    ("X", None, True),
])
def test_missing_search_and_bad_limit_rejected(category, subsystem, limit):
    with pytest.raises(ValueError):
        lesson.retrieve_verified_lessons(
            ledger(verified_record()), failure_category=category, subsystem=subsystem, limit=limit
        )
