"""WOW Native Research Engine: offline contract, acquisition, rights, dedup and handoff tests."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.wow_specialized_swarm import consume_research_receipts  # noqa: E402
from v17.certified_model_factory import receive_research_handoff  # noqa: E402
from v17.native_research import contract, rights  # noqa: E402
from v17.native_research.contract import BlockerInput, build_task, schedule_decision  # noqa: E402
from v17.native_research.extract import ResultRow, find_league_files, parse_results_csv  # noqa: E402
from v17.native_research.fetcher import FetchBlocked, NativeFetcher, canonical_url  # noqa: E402
from v17.native_research.runner import (run_native_research,  # noqa: E402
                                        soccer_results_research_after_forward)
from v17.native_research.store import MemoryResearchStore  # noqa: E402
from v17.native_research.verify import cross_check, results_freshness  # noqa: E402

NOW = datetime(2026, 11, 26, 4, 0, tzinfo=timezone.utc)
OF_URL = "https://raw.githubusercontent.com/openfootball/football.json/master/2026-27/en.1.json"
TEAMS = [("Arsenal", "Chelsea"), ("Liverpool", "Everton"), ("Man Utd", "Spurs"), ("Wolves", "Brentford"),
         ("Fulham", "Brighton"), ("Leeds", "Burnley")]
OF_NAMES = {"Man Utd": "Manchester United FC", "Spurs": "Tottenham Hotspur FC", "Wolves": "Wolverhampton Wanderers FC"}


def _matches():
    rows = []
    for week in range(3):
        for i, (h, a) in enumerate(TEAMS):
            rows.append((date(2026, 8, 22) + timedelta(days=7 * week), h, a, (i + week) % 4, i % 3))
    return rows


def openfootball_json(stale: bool = True) -> bytes:
    out = []
    for day, h, a, hg, ag in _matches():
        out.append({"date": day.isoformat(), "team1": OF_NAMES.get(h, h + " FC"),
                    "team2": OF_NAMES.get(a, a + " FC"), "score": {"ft": [hg, ag]}})
    if stale:  # past-dated rounds with no result
        out.append({"date": "2026-10-03", "team1": "Arsenal FC", "team2": "Leeds FC"})
    return json.dumps({"matches": out}).encode()


def candidate_csv(conflict: bool = False, latest: str = "2026-11-23") -> bytes:
    lines = ["gw,date,time,home,away,ft_home,ft_away"]
    for n, (day, h, a, hg, ag) in enumerate(_matches()):
        if conflict and n == 0:
            hg += 3
        lines.append(f"1,{day.isoformat()},15:00,{h},{a},{hg},{ag}")
    lines.append(f"12,{latest},15:00,Arsenal,Leeds,2,1")
    return ("\n".join(lines) + "\n").encode()


README_NC = b"""# results

## How it is built
Merges ESPN, FotMob and OpenLigaDB.

## License and attribution
Shared for informational, non-commercial use. You may not sell it. Attribute by linking here.
"""
README_CC0 = b"# results\n\n## License\nData released under CC0 1.0 public domain dedication.\n"


class _Resp:
    def __init__(self, status, body=b"", headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        pass


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, headers=None, **kwargs):
        self.calls.append((url, dict(headers or {})))
        assert "Authorization" not in (headers or {}) and "Cookie" not in (headers or {})
        for key, value in self.routes.items():
            if url.startswith(key) or (key.startswith("search:") and "/search/repositories" in url):
                return value(url, headers or {}) if callable(value) else _Resp(*value)
        return _Resp(404)


def routes(*, readme=README_NC, csv=None, search_items=None, of_stale=True):
    items = [{"full_name": "acme/results", "name": "results", "pushed_at": "2026-11-25T00:00:00Z",
              "default_branch": "main", "license": None}] if search_items is None else search_items
    return {
        "https://raw.githubusercontent.com/robots.txt": (404,),
        OF_URL: (200, openfootball_json(of_stale)),
        "search:": (200, json.dumps({"items": items}).encode()),
        "https://api.github.com/repos/acme/results/git/trees/main": (200, json.dumps({"tree": [
            {"path": "data/ENG_1_premier-league/results_2627.csv", "type": "blob"},
            {"path": "data/AUT_1_bundesliga/results_2627.csv", "type": "blob"},
            {"path": "README.md", "type": "blob"}]}).encode()),
        "https://api.github.com/repos/acme/results": (200, json.dumps(
            {"default_branch": "main", "license": None, "pushed_at": "2026-11-25T00:00:00Z"}).encode()),
        "https://raw.githubusercontent.com/acme/results/main/data/ENG_1_premier-league/results_2627.csv":
            (200, csv if csv is not None else candidate_csv()),
        "https://raw.githubusercontent.com/acme/results/main/README.md": (200, readme, {"ETag": '"r1"'}),
    }


def fetcher(session, **kw):
    return NativeFetcher(session=session, min_interval_s=0, sleep=lambda s: None, **kw)


def blocker(league="EPL", context=None):
    return BlockerInput(sport="SOCCER", league=league, season="2026-27",
                        market_route=f"SOCCER:{league}:OUTRIGHT_WINNER:THREE_WAY_1X2",
                        specialist_id=f"SOCCER_1X2_MULTINOMIAL_LOGIT_V1_{league}",
                        blocker_code="SOCCER_SOURCE_RESULTS_STALE", candidate_ready=True,
                        context=context or {"source": "OPENFOOTBALL:football.json"})


def run(store, session, now=NOW, blockers=None):
    return run_native_research(store, blockers or [blocker()], fetcher=fetcher(session), now=now, owner="test")


# -- 1. blocker -> task ----------------------------------------------------------
def test_blocker_maps_to_deterministic_task_reusing_certification_inventory():
    a, b = build_task(blocker()), build_task(blocker())
    assert a.task_id == b.task_id and len(a.task_id) == 64
    assert a.certification_status != "CERTIFIED_ROUTE"
    assert "CURRENT_DATA_NOT_CERTIFIED" in a.certification_blockers
    assert a.required_source_type == "RESULTS_FEED" and a.priority == 1
    assert a.missing_evidence_field == "official_results_current"
    assert build_task(blocker("LALIGA")).task_id != a.task_id
    unmapped = build_task(BlockerInput(sport="NBA", market_route="NBA:X", specialist_id="s", blocker_code="ODD"))
    assert unmapped.researchable is False
    assert schedule_decision(unmapped, None, [], now=NOW).reason == "NOT_RESEARCHABLE"


# -- 2-5, 7, 10: end to end ------------------------------------------------------
def test_end_to_end_discovers_verifies_persists_searches_and_hands_off():
    store, session = MemoryResearchStore(), FakeSession(routes())
    receipt = run(store, session)
    task = receipt["tasks"][0]
    assert receipt["status"] == "COMPLETED" and receipt["spend_usd"] == 0 and receipt["paid_providers_used"] == []
    assert task["outcome"] == "CANDIDATE_VERIFIED_RIGHTS_REVIEW_REQUIRED"
    assert (task["progress_before"], task["progress_after"]) == (0, 3)
    assert task["remaining_blockers"][0] == "SOCCER_SOURCE_RESULTS_STALE"  # never erased
    hosts = {urlsplit(u).hostname for u, _ in session.calls}
    assert hosts <= {"raw.githubusercontent.com", "api.github.com"}  # no Firecrawl, no Tavily
    findings = {f["finding_type"]: f for f in store.tables["wow_nre_findings"].values()}
    cand = findings["CANDIDATE_SOURCE"]
    assert cand["freshness"] == "FRESH" and cand["conflict_status"] == "CONSISTENT"
    assert cand["verification"]["file"] == "data/ENG_1_premier-league/results_2627.csv"  # not Austria
    assert findings["INCUMBENT_SOURCE_STATUS"]["freshness"] == "STALE"
    docs = store.tables["wow_nre_documents"]
    for doc_id in cand["document_ids"]:
        doc = docs[doc_id]
        assert doc["acquisition_receipt_sha256"] == contract.sha256_json(doc["acquisition_receipt"])
        assert doc["body"] is None  # non-commercial terms: metadata only
    right = next(iter(store.tables["wow_nre_source_rights"].values()))
    assert right["production_use_authorized"] is False and right["unresolved_upstream_rights"] is True
    assert right["commercial_restrictions"] == ["NON_COMMERCIAL_ONLY"]
    hits = store.search("results fresh", sport="SOCCER", league="EPL", evidence_type="RESULTS_DATASET")
    assert hits and hits[0]["title"].startswith("acme/results")
    assert store.search("dataset", team="Arsenal")
    assert task["factory_status"] == "HELD"
    attempt = next(iter(store.tables["wow_nre_research_attempts"].values()))
    factory = attempt["receipt"]["factory"]["factory_receipt"]
    assert factory["code"] == "FACTORY_HANDOFF_SOURCE_RIGHTS_REVIEW_REQUIRED"
    assert factory["certification_performed"] is False and factory["can_execute"] is False
    assert not cand["accepted_for_handoff"]


def test_clear_cc0_source_reaches_independent_review_queue_but_never_production():
    store = MemoryResearchStore()
    task = run(store, FakeSession(routes(readme=README_CC0)))["tasks"][0]
    assert task["outcome"] == "CANDIDATE_VERIFIED_PENDING_INDEPENDENT_REVIEW"
    assert task["factory_status"] == "QUEUED_FOR_INDEPENDENT_SOURCE_REVIEW"
    assert task["remaining_blockers"] == ["SOCCER_SOURCE_RESULTS_STALE", "SOURCE_INDEPENDENT_REVIEW_REQUIRED"]
    right = next(iter(store.tables["wow_nre_source_rights"].values()))
    assert right["production_use_authorized"] is False and right["review_status"] == "PENDING_INDEPENDENT_REVIEW"
    terms = [d for d in store.tables["wow_nre_documents"].values() if d["evidence_type"] == "SOURCE_TERMS"]
    assert terms and terms[0]["body"]  # storage permitted: body retained


def test_no_candidate_is_an_evidence_backed_negative_result():
    store = MemoryResearchStore()
    task = run(store, FakeSession(routes(search_items=[])))["tasks"][0]
    assert task["outcome"] == "NO_SOURCE_FOUND" and task["progress_after"] == 1
    neg = [f for f in store.tables["wow_nre_findings"].values() if f["finding_type"] == "NEGATIVE_RESULT"]
    assert neg and neg[0]["verification"]["queries"]


def test_failed_probe_is_not_reported_as_a_negative_result():
    r = routes()
    r["https://api.github.com/repos/acme/results"] = (500,)
    r = {"https://api.github.com/repos/acme/results": (500,), **r}
    store = MemoryResearchStore()
    task = run(store, FakeSession(r))["tasks"][0]
    assert task["outcome"] == "ACQUISITION_FAILED"
    assert not [f for f in store.tables["wow_nre_findings"].values() if f["finding_type"] == "NEGATIVE_RESULT"]


# -- 6. Swarm -------------------------------------------------------------------
def test_swarm_consumes_persisted_receipt_and_rejects_tampering():
    store = MemoryResearchStore()
    run(store, FakeSession(routes()))
    receipt = next(iter(store.tables["wow_nre_research_attempts"].values()))["receipt"]["swarm_receipt"]
    persisted = json.loads(json.dumps(receipt))  # jsonb round trip
    out = consume_research_receipts([persisted], "soccer_epl")
    assert len(out["consumed"]) == 1 and out["network_calls"] == 0 and out["evidence_only"] is True
    assert "SOCCER_SOURCE_RESULTS_STALE" in out["remaining_blockers"]
    assert consume_research_receipts([{**persisted, "outcome": "X"}], "soccer_epl")["rejected"] == [
        "NRE_RECEIPT_DIGEST_MISMATCH"]
    assert consume_research_receipts([persisted], "soccer_spain_la_liga")["rejected"] == [
        "NRE_RECEIPT_LEAGUE_MISMATCH"]
    assert consume_research_receipts([persisted], "basketball_nba")["rejected"] == ["NRE_RECEIPT_SPORT_MISMATCH"]
    forged = {k: v for k, v in persisted.items() if k != "receipt_sha256"} | {"can_execute": True}
    forged["receipt_sha256"] = contract.sha256_json(forged)
    assert consume_research_receipts([forged], "soccer_epl")["rejected"] == ["NRE_RECEIPT_AUTHORITY_INVALID"]


# -- 8. dedup and scheduling -----------------------------------------------------
def test_unchanged_blocker_is_not_researched_again():
    store, session = MemoryResearchStore(), FakeSession(routes())
    run(store, session)
    calls = len(session.calls)
    again = run(store, session, now=NOW + timedelta(hours=2))["tasks"][0]
    assert again["decision"] == "DEDUP_UNCHANGED" and len(session.calls) == calls
    assert len(store.tables["wow_nre_research_attempts"]) == 1
    changed = run(store, session, now=NOW + timedelta(hours=3),
                  blockers=[blocker(context={"source": "OPENFOOTBALL:football.json", "frozen": "new"})])
    assert changed["tasks"][0]["decision"] == "NEW_EVIDENCE"
    later = run(store, FakeSession(routes()), now=NOW + timedelta(days=4),
                blockers=[blocker(context={"source": "OPENFOOTBALL:football.json", "frozen": "new"})])
    assert later["tasks"][0]["decision"] == "FRESHNESS_TRIGGER"
    docs_before = len(store.tables["wow_nre_documents"])
    assert later["tasks"][0]["documents_duplicate"] >= 1  # identical content is not stored twice
    assert len(store.tables["wow_nre_documents"]) == docs_before


def test_transient_failures_retry_with_backoff_and_daily_limit():
    task = build_task(blocker())
    prior = {"blocker_fingerprint": task.blocker_fingerprint}
    fail = lambda h: {"outcome": "ACQUISITION_FAILED", "finished_at": (NOW + timedelta(hours=h)).isoformat()}  # noqa: E731
    assert schedule_decision(task, prior, [fail(0)], now=NOW + timedelta(minutes=30)).reason == "RETRY_BACKOFF"
    assert schedule_decision(task, prior, [fail(0)], now=NOW + timedelta(hours=1)).reason == "AUTHORIZED_RETRY"
    assert schedule_decision(task, prior, [fail(0), fail(1), fail(5)], now=NOW + timedelta(hours=6)).reason \
        == "RETRY_LIMIT_REACHED"
    held = {**prior, "lease_expires_at": (NOW + timedelta(minutes=5)).isoformat()}
    assert schedule_decision(task, held, [fail(0)], now=NOW).reason == "LEASE_HELD"


def test_lease_blocks_a_second_writer():
    store = MemoryResearchStore()
    store.now = NOW
    task = build_task(blocker())
    store.create_task({"task_id": task.task_id})
    assert store.claim_task(task.task_id, "a", 300) is True
    assert store.claim_task(task.task_id, "b", 300) is False


# -- 9. fail closed --------------------------------------------------------------
def test_conflicting_source_fails_closed():
    store = MemoryResearchStore()
    task = run(store, FakeSession(routes(csv=candidate_csv(conflict=True))))["tasks"][0]
    assert task["outcome"] == "CONFLICTING_EVIDENCE" and task["factory_status"] is None
    assert "SOURCE_EVIDENCE_CONFLICT" in task["remaining_blockers"]


def test_stale_candidate_is_never_verified():
    store = MemoryResearchStore()
    task = run(store, FakeSession(routes(csv=candidate_csv(latest="2026-09-01"))))["tasks"][0]
    assert task["outcome"] == "CANDIDATE_FOUND_UNVERIFIED"


def test_fetcher_refuses_unapproved_hosts_redirects_robots_and_access_controls():
    def redirect(url, headers):
        return _Resp(302, headers={"Location": "https://evil.example/data.csv"})

    session = FakeSession({"https://raw.githubusercontent.com/robots.txt": (404,),
                           "https://raw.githubusercontent.com/a/b/main/x": redirect,
                           "https://raw.githubusercontent.com/a/b/main/paywall": (402,),
                           "https://api.github.com/repos/a/b": (403, b"", {"X-RateLimit-Remaining": "0"})})
    f = fetcher(session)
    for url, code in (("https://www.tavily.com/search", "NRE_DOMAIN_NOT_APPROVED"),
                      ("http://raw.githubusercontent.com/a", "NRE_URL_NOT_HTTPS"),
                      ("https://raw.githubusercontent.com/a/b/main/x", "NRE_DOMAIN_NOT_APPROVED"),
                      ("https://raw.githubusercontent.com/a/b/main/paywall", "NRE_ACCESS_RESTRICTED"),
                      ("https://api.github.com/repos/a/b", "NRE_SOURCE_RATE_LIMITED")):
        with pytest.raises(FetchBlocked) as exc:
            f.get(url)
        assert exc.value.code == code
    robots = FakeSession({"https://example.org/robots.txt": (200, b"User-agent: *\nDisallow: /private\n")})
    g = fetcher(robots, approved_domains={"example.org"})
    with pytest.raises(FetchBlocked) as exc:
        g.get("https://example.org/private/x")
    assert exc.value.code == "NRE_ROBOTS_DISALLOWED"
    denied = fetcher(FakeSession({"https://example.org/robots.txt": (503,)}), approved_domains={"example.org"})
    with pytest.raises(FetchBlocked) as exc:
        denied.get("https://example.org/x")  # no robots evidence: fail closed
    assert exc.value.code == "NRE_ROBOTS_DISALLOWED"


def test_budget_etag_and_canonical_urls():
    assert canonical_url("https://API.github.com:443/x?b=2&a=1&utm_source=z#frag") == "https://api.github.com/x?a=1&b=2"
    session = FakeSession({"https://raw.githubusercontent.com/robots.txt": (404,),
                           "https://raw.githubusercontent.com/a": lambda u, h: _Resp(
                               304 if h.get("If-None-Match") == '"e1"' else 200, b"x", {"ETag": '"e1"'})})
    f = fetcher(session, max_requests=3)
    first = f.get("https://raw.githubusercontent.com/a/1")
    assert first.content_sha256 and first.etag == '"e1"'
    assert f.get("https://raw.githubusercontent.com/a/2", etag='"e1"').not_modified is True
    with pytest.raises(FetchBlocked) as exc:
        f.get("https://raw.githubusercontent.com/a/3")
    assert exc.value.code == "NRE_RETRIEVAL_BUDGET_EXHAUSTED"


# -- rights and verification -----------------------------------------------------
def test_software_license_is_never_a_data_license():
    rec = rights.assess_source_rights("s", software_spdx="MIT", terms_text="Code for scraping matches.",
                                      terms_url=None, robots_permitted=True, assessed_at=NOW)
    assert rec["software_license_not_data_license"] is True
    assert rec["model_training_permitted"] == "UNRESOLVED" and rec["production_use_authorized"] is False
    assert rights.storage_status(rec) == "METADATA_ONLY_RIGHTS_UNRESOLVED"
    cc0 = rights.assess_source_rights("s", software_spdx=None, terms_text="CC0 public domain dedication; uses ESPN",
                                      terms_url=None, robots_permitted=True, assessed_at=NOW)
    assert cc0["production_use_permitted"] == "UPSTREAM_RIGHTS_UNRESOLVED"  # compilation of a third party
    assert rights.training_permitted(cc0) is False


def test_freshness_cross_check_and_league_file_selection():
    rows = parse_results_csv(candidate_csv().decode())
    assert results_freshness(rows, NOW)["status"] == "FRESH"
    stale = rows + [ResultRow(date(2026, 11, 1), "A", "B", None, None)]
    assert results_freshness(stale, NOW)["status"] == "STALE"
    from v17.native_research.extract import parse_openfootball
    ref = parse_openfootball(json.loads(openfootball_json())["matches"])
    assert cross_check(rows, ref)["status"] == "CONSISTENT"
    assert cross_check(rows, ref)["overlap"] >= 10
    paths = ["d/AUT_1_bundesliga/results_2627.csv", "d/GER_2_2-bundesliga/results_2627.csv",
             "d/GER_1_bundesliga/results_2627.csv", "d/BRA_1_serie-a/results_2627.csv"]
    assert find_league_files(paths, "BUNDESLIGA", "2026-27") == ["d/GER_1_bundesliga/results_2627.csv"]
    assert find_league_files(paths, "SERIE_A", "2026-27") == []


# -- factory and hook ------------------------------------------------------------
def test_factory_rejects_probability_and_authority_in_handoff():
    base = {"schema_version": "WOW_NRE_FACTORY_HANDOFF_V1", "task_id": "t", "finding_id": "f", "sport": "SOCCER",
            "league": "EPL", "season": "2026-27", "blocker_code": "B", "source_id": "s", "freshness": "FRESH",
            "conflict_status": "CONSISTENT", "rights_id": "r",
            "rights": {"model_training_permitted": "YES", "unresolved_upstream_rights": False,
                       "commercial_restrictions": []},
            "evidence_only": True, "prediction_authority": False, "production_use_authorized": False,
            "can_execute": False}
    assert receive_research_handoff(base)["status"] == "QUEUED_FOR_INDEPENDENT_SOURCE_REVIEW"
    assert receive_research_handoff({**base, "win_probability": 0.6})["code"] == "FACTORY_HANDOFF_PROBABILITY_FORBIDDEN"
    assert receive_research_handoff({**base, "can_execute": True})["code"] == "FACTORY_HANDOFF_AUTHORITY_INVALID"
    assert receive_research_handoff({**base, "conflict_status": "CONFLICT"})["status"] == "HELD"
    for out in (receive_research_handoff(base), receive_research_handoff({})):
        assert out["certification_performed"] is False and out["probability_publishable"] is False


def test_maintenance_hook_researches_only_stale_leagues_and_never_raises(monkeypatch):
    seen = {}

    def fake_run(store, blockers, **kw):
        seen["leagues"] = [b.league for b in blockers]
        raise RuntimeError("boom")

    monkeypatch.setattr("v17.native_research.runner.run_native_research", fake_run)
    forward = {"leagues": {"EPL": {"blocked_by_code": {"SOCCER_SOURCE_RESULTS_STALE": 3}, "registration_id": "r"},
                           "BUNDESLIGA": {"status": "COMPLETED", "blocked_by_code": {}},
                           "LALIGA": {"blocked_by_code": {"SOCCER_KICKOFF_TIME_UNVERIFIED": 2}}}}
    with pytest.raises(RuntimeError):
        soccer_results_research_after_forward(object(), forward, now=NOW)
    assert seen["leagues"] == ["EPL"]
    assert soccer_results_research_after_forward(object(), {"leagues": {}}, now=NOW)["status"] == \
        "NO_RESEARCHABLE_BLOCKERS"


def test_run_never_raises_on_store_failure():
    class Broken(MemoryResearchStore):
        def load_task(self, task_id):
            from v17.native_research.store import StoreError
            raise StoreError("NRE_DB_42P01")

    out = run(Broken(), FakeSession(routes()))
    assert out["status"] == "COMPLETED" and out["tasks"][0]["code"] == "NRE_DB_42P01"
    assert out["can_execute"] is False and out["prediction_authority"] is False
