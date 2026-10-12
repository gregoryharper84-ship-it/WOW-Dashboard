from __future__ import annotations

import pytest

from v17.engineering_resident_supervisor import active_engineering_workflow
from v17.p0_parallel_dispatch import select_parallel


def _row(issue: int, stream: str, lease: str, rank: int, keys: list[str]) -> dict:
    return {
        "issue_number": issue,
        "incident_id": str(issue),
        "severity": "P0",
        "execution_lane": "RAPID",
        "rapid_stream": stream,
        "lease_group": lease,
        "priority_rank": rank,
        "state": "OPEN",
        "conflict_keys": keys,
    }


ROWS = [
    _row(823, "A", "P0_ACQUISITION", 1, ["CANONICAL_EVENT_IDENTITY"]),
    _row(1496, "B", "P0_MLB_SCORING", 2, ["MLB_SCORER_INPUTS"]),
    _row(1388, "C", "P0_RUNTIME", 3, ["MEMORY_STABILITY"]),
]
MANIFEST = {"restoration": ROWS, "acceleration": []}


class _Client:
    def __init__(self, runs: list[dict]):
        self.runs = runs

    def get(self, suffix: str):
        if suffix.startswith("actions/runs?status="):
            state = suffix.split("status=", 1)[1].split("&", 1)[0]
            rows = [run for run in self.runs if run.get("_state") == state]
            return {"total_count": len(rows), "workflow_runs": rows}
        raise AssertionError(f"unexpected GitHub read: {suffix}")


def _run(path: str, title: str, *, state: str = "in_progress", branch: str = "main") -> dict:
    return {
        "id": 101,
        "path": path,
        "display_title": title,
        "name": title.split(" lease=", 1)[0],
        "head_branch": branch,
        "_state": state,
    }


def test_domain_worker_is_disjoint_in_both_controllers() -> None:
    active = _run(
        ".github/workflows/wow-v17-claude-engineering-worker.yml",
        "wow-v17-claude-engineering-worker lease=P0_RUNTIME incident=1388",
    )
    client = _Client([active])

    # Resident supervisor may admit acquisition/scoring, but not runtime.
    assert active_engineering_workflow(client, ROWS[0], MANIFEST) is False
    assert active_engineering_workflow(client, ROWS[1], MANIFEST) is False
    assert active_engineering_workflow(client, ROWS[2], MANIFEST) is True

    # Rapid selector must make the identical ownership decision.
    selected = select_parallel(ROWS, active_runs=[active])
    assert [row["incident_id"] for row in selected["selected"]] == ["823", "1496"]
    skipped = {row["incident_id"]: row["reason"] for row in selected["skipped"]}
    assert skipped["1388"] == "ACTIVE_WORKER_LEASE_CONFLICT"
    assert selected["can_execute"] is False


@pytest.mark.parametrize(
    "path,title",
    [
        (
            ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
            "wow-v17-chatgpt-engineering-worker lease=GLOBAL incident=AUTO",
        ),
        (
            ".github/workflows/wow-v17-claude-engineering-worker.yml",
            "wow-v17-claude-engineering-worker lease=GLOBAL incident=AUTO",
        ),
        (
            ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
            "WOW V17 provider source=manual lease=GLOBAL incident=AUTO",
        ),
    ],
)
def test_unresolved_global_writer_holds_all_lanes_in_both_controllers(path: str, title: str) -> None:
    active = _run(path, title)
    client = _Client([active])

    assert all(active_engineering_workflow(client, row, MANIFEST) is True for row in ROWS)
    selected = select_parallel(ROWS, active_runs=[active])
    assert selected["selected"] == []
    assert {row["reason"] for row in selected["skipped"]} == {
        "ACTIVE_GLOBAL_WORKER_TARGET_UNRESOLVED"
    }
    assert selected["can_execute"] is False


def test_untrusted_worker_provenance_fails_closed_in_both_controllers() -> None:
    active = _run(
        ".github/workflows/lookalike.yml",
        "wow-v17-claude-engineering-worker lease=P0_RUNTIME incident=1388",
        branch="other-branch",
    )
    client = _Client([active])

    # Resident view treats the untrusted run as busy; rapid view rejects it
    # only when it claims an approved workflow path with invalid provenance.
    assert active_engineering_workflow(client, ROWS[0], MANIFEST) is True

    forged = dict(active)
    forged["path"] = ".github/workflows/wow-v17-claude-engineering-worker.yml"
    with pytest.raises(ValueError, match="ACTIVE_WORKER_IDENTITY_UNRESOLVED"):
        select_parallel(ROWS, active_runs=[forged])


def test_existing_pr_claim_is_consistent_with_no_duplicate_writer() -> None:
    open_prs = [
        {
            "number": 1507,
            "title": "numeric evidence",
            "body": "Incident: #1496",
        },
        {
            "number": 9999,
            "title": "diagnostic mention #823",
            "body": "Related #1388; analysis only",
        },
    ]
    selected = select_parallel(ROWS, open_prs=open_prs)
    assert [row["incident_id"] for row in selected["selected"]] == ["823", "1388"]
    skipped = {row["incident_id"]: row["reason"] for row in selected["skipped"]}
    assert skipped["1496"] == "EXISTING_OPEN_PR_REQUIRES_REVIEW"


_UNSET_TOTAL = object()


class _InventoryClient(_Client):
    def __init__(self, runs, *, delta=0, total_override=_UNSET_TOTAL):
        super().__init__(runs)
        self.delta, self.total_override = delta, total_override

    def get(self, suffix):
        result = super().get(suffix)
        if "status=in_progress" in suffix:
            result["total_count"] = (self.total_override if self.total_override is not _UNSET_TOTAL
                                     else result["total_count"] + self.delta)
        return result


@pytest.mark.parametrize("total", [None, "1", True, -1, 2])
def test_resident_active_workflow_inventory_requires_exact_typed_count(total):
    trusted = _run(
        ".github/workflows/wow-v17-claude-engineering-worker.yml",
        "wow-v17-claude-engineering-worker lease=P0_RUNTIME incident=1388",
    )
    with pytest.raises(RuntimeError, match="ACTIVE_WORKFLOW_INVENTORY_INCOMPLETE"):
        active_engineering_workflow(
            _InventoryClient([trusted], total_override=total), ROWS[0], MANIFEST
        )


@pytest.mark.parametrize("path,expected", [
    (".github/workflows/wow-v17-engineering-provider-dispatcher.yml@refs/heads/main", True),
    (".github/workflows/wow-v17-engineering-provider-dispatcher.yml@refs/tags/v1", True),
    (".github/workflows/wow-v17-engineering-provider-dispatcher.yml@evil", None),
    (".github/workflows/wow-v17-engineering-provider-dispatcher.yml@refs/heads/main@evil", None),
])
def test_resident_live_provider_name_and_source_ref_fail_closed(path, expected):
    active = _run(
        path,
        "WOW V17 provider source=wow-v17-chatgpt-engineering-worker "
        "lease=GLOBAL incident=AUTO lease=GLOBAL incident=AUTO",
    )
    client = _Client([active])
    if expected is None:
        with pytest.raises(RuntimeError, match="ACTIVE_WORKFLOW_REF_INVALID"):
            active_engineering_workflow(client, ROWS[0], MANIFEST)
    else:
        assert active_engineering_workflow(client, ROWS[0], MANIFEST) is expected


def test_resident_provider_name_on_untrusted_path_cannot_hide_worker():
    active = _run(
        ".github/workflows/lookalike-provider.yml",
        "WOW V17 provider source=manual lease=GLOBAL incident=AUTO",
    )
    assert active_engineering_workflow(_Client([active]), ROWS[0], MANIFEST) is True


@pytest.mark.parametrize("body,expected", [
    ("Incident: `823`", True),
    ("Incident: #823", True),
    ("Refs #823", True),
    ("Fixes gregoryharper84-ship-it/WOW-Dashboard#823", True),
    ("Closes #823", True),
    ("Unrelated debugging mentions #823", False),
    ("Incident: `8230`", False),
    ("Incident: `823` analysis only", False),
    ("Refs #8230", False),
])
def test_resident_existing_pr_claim_exactness(body, expected):
    from v17.engineering_resident_supervisor import pending_pr_for_issue

    class PRClient:
        def get(self, suffix):
            assert suffix == "pulls?state=open&per_page=100&sort=created&direction=asc&page=1"
            return [{"number": 77, "body": body}]

    assert pending_pr_for_issue(PRClient(), 823) is expected

@pytest.mark.parametrize("source_pair,target_pair", [
    ("lease=P0_RUNTIME incident=1388", "lease=P0_ACQUISITION incident=823"),
    ("lease=GLOBAL incident=AUTO", "lease=P0_ACQUISITION incident=823"),
    ("lease=P0_RUNTIME incident=1388", "lease=P0_RUNTIME incident=999"),
])
def test_provider_conflicting_source_and_target_never_admit_disjoint_writer(
    source_pair, target_pair,
):
    active = _run(
        ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
        f"WOW V17 provider source=wow-v17-chatgpt-engineering-worker "
        f"{source_pair} {target_pair}",
    )
    # Without validating BOTH identity pairs, first-pair routing would
    # incorrectly permit the unrelated acquisition/scoring lane.
    assert active_engineering_workflow(_Client([active]), ROWS[0], MANIFEST) is True
    assert active_engineering_workflow(_Client([active]), ROWS[1], MANIFEST) is True


def test_provider_consistent_duplicate_identity_preserves_disjoint_lane():
    active = _run(
        ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
        "WOW V17 provider source=wow-v17-chatgpt-engineering-worker "
        "lease=P0_RUNTIME incident=1388 lease=P0_RUNTIME incident=1388",
    )
    assert active_engineering_workflow(_Client([active]), ROWS[0], MANIFEST) is False
    assert active_engineering_workflow(_Client([active]), ROWS[2], MANIFEST) is True


class _PagedPullRequestClient:
    def __init__(self, pages, *, mutate_first_page=False):
        self.pages = pages
        self.mutate_first_page = mutate_first_page
        self.requests = []

    def get(self, suffix):
        assert suffix.startswith("pulls?state=open&per_page=100&sort=created&direction=asc&page=")
        page = int(suffix.rsplit("page=", 1)[1])
        self.requests.append(page)
        result = self.pages.get(page, [])
        if self.mutate_first_page and page == 1 and self.requests.count(1) > 1:
            return [dict(result[0], body="Incident: #9999"), *result[1:]]
        return result


def _pr_page(start, count=100, *, claimed_incident=None):
    return [
        {"number": start + i,
         "body": "Incident: #" + str(claimed_incident) if claimed_incident is not None and i == count - 1
                 else "Unrelated engineering discussion"}
        for i in range(count)
    ]


def test_open_pr_claim_on_second_page_blocks_duplicate_writer():
    from v17.engineering_resident_supervisor import pending_pr_for_issue

    client = _PagedPullRequestClient({
        1: _pr_page(1), 2: _pr_page(101, 1, claimed_incident=823)
    })
    assert pending_pr_for_issue(client, 823) is True
    assert client.requests == [1, 2, 1]


def test_full_open_pr_inventory_without_claim_can_admit():
    from v17.engineering_resident_supervisor import pending_pr_for_issue

    client = _PagedPullRequestClient({1: _pr_page(1), 2: _pr_page(101, 1)})
    assert pending_pr_for_issue(client, 823) is False
    assert client.requests == [1, 2, 1]


@pytest.mark.parametrize("defect,code", [
    ("duplicate", "OPEN_PR_INVENTORY_DUPLICATE"),
    ("nonlist", "OPEN_PR_INVENTORY_INVALID"),
    ("bad_identity", "OPEN_PR_INVENTORY_IDENTITY_INVALID"),
    ("mutation", "OPEN_PR_INVENTORY_CHANGED_DURING_SCAN"),
    ("page_limit", "OPEN_PR_INVENTORY_PAGE_LIMIT"),
])
def test_open_pr_inventory_fails_closed_on_untrusted_or_unstable_pagination(defect, code):
    from v17.engineering_resident_supervisor import pending_pr_for_issue

    pages = {1: _pr_page(1)}
    if defect == "duplicate":
        pages[2] = [{"number": 1, "body": "Incident: #823"}]
    elif defect == "nonlist":
        pages[2] = {"error": "rate limited"}
    elif defect == "bad_identity":
        pages[2] = [{"number": "101", "body": ""}]
    elif defect == "page_limit":
        pages = {i: _pr_page((i - 1) * 100 + 1) for i in range(1, 11)}
    else:
        pages[2] = _pr_page(101, 1)
    client = _PagedPullRequestClient(pages, mutate_first_page=(defect == "mutation"))
    with pytest.raises(RuntimeError, match=code):
        pending_pr_for_issue(client, 823)


def test_open_pr_third_page_claim_is_not_missed():
    from v17.engineering_resident_supervisor import pending_pr_for_issue

    client = _PagedPullRequestClient({
        1: _pr_page(1), 2: _pr_page(101), 3: _pr_page(201, 1, claimed_incident=823)
    })
    assert pending_pr_for_issue(client, 823)
    assert client.requests == [1, 2, 3, 1]
