"""#2136: stale owner source must not repeatedly occupy the global adoption writer."""
from __future__ import annotations

import inspect

import pytest

from v17 import engineering_authorized_adoption as adoption

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
OWNER = "gregoryharper84-ship-it"
SHA_STALE = "a" * 40
SHA_GOOD = "c" * 40
MAIN_SHA = "d" * 40
BASE_SHA = "b" * 40
PATH = ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"


def _pr(number, sha):
    return {
        "number": number, "state": "open", "draft": False, "changed_files": 1,
        "user": {"login": OWNER},
        "head": {"sha": sha, "repo": {"full_name": REPO}},
        "base": {"sha": BASE_SHA, "ref": "main", "repo": {"full_name": REPO}},
    }


def _checks(sha):
    rows = [
        {"name": name, "app": {"id": adoption.ACTIONS_APP_ID}, "head_sha": sha,
         "id": n + 1, "status": "completed", "conclusion": "success"}
        for n, name in enumerate(adoption.REQUIRED_CHECKS)
    ]
    return {"total_count": len(rows), "check_runs": rows}


def _inventory(monkeypatch, *, include_second=True):
    rows = [_pr(1666, SHA_STALE)]
    if include_second:
        rows.append(_pr(2010, SHA_GOOD))
    requests = []

    def api(endpoint):
        requests.append(endpoint)
        if endpoint.startswith("pulls?state=open&base=main&per_page=100"):
            return rows
        if endpoint.startswith("pulls?state=all&head="):
            return []
        for row in rows:
            n, sha = row["number"], row["head"]["sha"]
            if endpoint == f"pulls/{n}/files?per_page=100":
                return [{"filename": PATH, "status": "modified"}]
            if endpoint == f"pulls/{n}":
                return row
            if endpoint == f"commits/{sha}/check-runs?per_page=100":
                return _checks(sha)
        raise AssertionError(f"Unexpected GitHub endpoint: {endpoint}")

    monkeypatch.setattr(adoption, "api", api)
    monkeypatch.setattr(
        adoption, "call",
        lambda *a, **kw: MAIN_SHA if a == ("git", "rev-parse",
                                            "refs/remotes/origin/main")
        else pytest.fail(f"Unexpected external operation: {a}"),
    )
    return requests


def test_stale_source_is_skipped_before_writer_and_next_good_source_is_selected(monkeypatch):
    requests = _inventory(monkeypatch)
    verified = []

    def verify(base, main, sha, paths):
        verified.append((base, main, sha, paths))
        if sha == SHA_STALE:
            raise adoption.AdoptionHold("MAIN_CHANGED_SOURCE_PATH_HOLD")
        return BASE_SHA

    monkeypatch.setattr(adoption, "verify_git_source", verify)
    result = adoption.discover(slot=0, check_main_drift=True)
    assert result == {
        "status": "SOURCE_READY", "pr_number": 2010,
        "source_sha": SHA_GOOD, "paths": [PATH], "can_execute": False,
    }
    assert [row[2] for row in verified] == [SHA_STALE, SHA_GOOD]
    assert all(row[0] == BASE_SHA and row[1] == MAIN_SHA for row in verified)
    assert len([q for q in requests if q.startswith("pulls?state=all&head=")]) == 2


def test_all_stale_returns_typed_no_candidate_without_writer(monkeypatch):
    _inventory(monkeypatch, include_second=False)

    def stale(*_):
        raise adoption.AdoptionHold("MAIN_CHANGED_SOURCE_PATH_HOLD")

    monkeypatch.setattr(adoption, "verify_git_source", stale)
    result = adoption.discover(slot=0, check_main_drift=True)
    assert result["status"] == "NO_ELIGIBLE_SOURCE"
    assert result["can_execute"] is False


@pytest.mark.parametrize("hold", [
    "READ_TOKEN_REQUIRED_FOR_SOURCE_FETCH_HOLD",
    "SOURCE_DIFF_NOT_EXACT_HOLD",
    "SOURCE_FILE_MODE_UNTRUSTED_HOLD",
    "MAIN_MOVED_DURING_PREFLIGHT_HOLD",
    "SOURCE_ANCESTRY_OR_MAIN_DRIFT_HOLD",
])
def test_nonstale_integrity_failures_remain_fail_closed(monkeypatch, hold):
    _inventory(monkeypatch)

    def reject(*_):
        raise adoption.AdoptionHold(hold)

    monkeypatch.setattr(adoption, "verify_git_source", reject)
    with pytest.raises(adoption.AdoptionHold, match=f"^{hold}$"):
        adoption.discover(slot=0, check_main_drift=True)


def test_default_library_discovery_keeps_readonly_api_contract(monkeypatch):
    _inventory(monkeypatch)
    monkeypatch.setattr(
        adoption, "verify_git_source",
        lambda *_: pytest.fail("Library default must not require git or credentials"),
    )
    receipt = adoption.discover(slot=0)
    assert receipt["status"] == "SOURCE_READY"
    assert receipt["pr_number"] == 1666


def test_protected_main_cli_enables_stale_filter_without_a_new_controller():
    source = inspect.getsource(adoption.main)
    assert "discover(check_main_drift=True)" in source
    assert 'parser.add_argument("operation", choices=("discover", "preflight", "integrate"))' in source
