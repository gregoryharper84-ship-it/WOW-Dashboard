"""WOW Native Research Engine runner.

blocker -> task -> dedup/lease -> discovery -> acquisition -> extraction ->
verification -> rights -> persisted evidence -> Swarm receipt -> factory handoff.

Never raises to its caller; the run receipt carries every outcome. Spend is
zero by construction: only free public APIs and public files are contacted.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from . import AUTHORITY_CEILING, ENGINE_VERSION, INERT, RESEARCH_WORKER_ID
from .contract import (PROGRESS_STAGE, BlockerInput, ResearchTask, build_task, next_eligible_after,
                       remaining_blockers, schedule_decision, sha256_json, task_row)
from .discovery import (SourceCandidate, crossref_search, github_dataset_search, github_queries, github_raw,
                        github_repo, github_tree, rank_candidates, readme_linked_paths, registered_sources,
                        safe_search)
from .extract import find_league_files, markdown_section, parse_openfootball, parse_results_csv
from .fetcher import FetchBlocked, FetchResult, NativeFetcher
from .learning import order_strategies, source_reliability
from .rights import assess_source_rights, storage_status, training_permitted
from .store import StoreError
from .verify import cross_check, results_freshness

RUN_SCHEMA = "WOW_NRE_RUN_RECEIPT_V1"
SWARM_SCHEMA = "WOW_NRE_SWARM_RECEIPT_V1"
HANDOFF_SCHEMA = "WOW_NRE_FACTORY_HANDOFF_V1"
MAX_CANDIDATES = 3
LEASE_TTL_SECONDS = 900


@dataclass
class Work:
    outcome: str = "NOT_ATTEMPTED"
    strategies: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    rights: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _now(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.utcoffset() is None:
        raise ValueError("NRE_NOW_NOT_TIMEZONE_AWARE")
    return value.astimezone(timezone.utc)


def _best(outcomes: Sequence[str]) -> str:
    return max(outcomes, key=lambda o: (PROGRESS_STAGE[o], o == "CONFLICTING_EVIDENCE")) if outcomes \
        else "NO_SOURCE_FOUND"


# -- persistence helpers ------------------------------------------------------
def _document(task: ResearchTask, attempt_id: str, result: FetchResult, *, source_id: str, title: str,
              excerpt: str, evidence_type: str, source_quality: str, freshness: str, rights_status: str,
              body: str | None = None, observed_at: str | None = None, teams: Sequence[str] = ()
              ) -> dict[str, Any]:
    receipt = {"attempt_id": attempt_id, "canonical_url": result.canonical_url, "status": result.status,
               "content_sha256": result.content_sha256, "retrieved_at": result.retrieved_at,
               "access_basis": result.access_basis, "etag": result.etag, "last_modified": result.last_modified}
    return {
        "document_id": sha256_json({"url": result.canonical_url, "sha": result.content_sha256}),
        "canonical_url": result.canonical_url,
        "domain": urlsplit(result.canonical_url).hostname,
        "source_id": source_id,
        "content_sha256": result.content_sha256,
        "content_type": result.content_type,
        "title": title[:300],
        "body": body if rights_status == "STORAGE_PERMITTED" else None,
        "excerpt": excerpt[:2000],
        "etag": result.etag,
        "last_modified": result.last_modified,
        "observed_at": observed_at,
        "retrieved_at": result.retrieved_at,
        "sport": task.sport, "league": task.league, "season": task.season,
        "teams": sorted(set(teams))[:60], "players": [], "event_id": None, "market": task.market_route,
        "keywords": " ".join(sorted(set(teams))[:60]),
        "evidence_type": evidence_type,
        "source_quality": source_quality,
        "freshness": freshness,
        "rights_status": rights_status,
        "task_id": task.task_id,
        "acquisition_receipt": receipt,
        "acquisition_receipt_sha256": sha256_json(receipt),
    }


def _finding(task: ResearchTask, attempt_id: str, *, source_id: str, finding_type: str, outcome: str,
             freshness: str | None, conflict_status: str | None, verification: Mapping[str, Any],
             rights_id: str | None = None, document_ids: Sequence[str] = ()) -> dict[str, Any]:
    body = {"task_id": task.task_id, "attempt_id": attempt_id, "source_id": source_id,
            "finding_type": finding_type, "outcome": outcome, "freshness": freshness,
            "conflict_status": conflict_status, "verification": dict(verification), "rights_id": rights_id,
            "document_ids": list(document_ids)}
    return {"finding_id": sha256_json(body), **body, "sport": task.sport, "league": task.league,
            "season": task.season, "required_source_type": task.required_source_type,
            "accepted_for_handoff": False, **INERT}


# -- soccer results feeds ------------------------------------------------------
def _openfootball_reference(task: ResearchTask, fetcher: NativeFetcher, attempt_id: str, now: datetime,
                            work: Work) -> list:
    from v17.soccer_openfootball_candidate import BASE_URL, COMPETITIONS, SOURCE_LICENSE

    rows: list = []
    for cand in registered_sources(task.sport, "RESULTS_FEED"):
        entry = {"strategy": "REGISTERED_SOURCE", "candidates": 1, "verified": 0}
        try:
            result = fetcher.get(f"{BASE_URL}/{task.season}/{COMPETITIONS[task.league]}.json")
            rows = parse_openfootball(json.loads(result.text()).get("matches") or [])
        except (FetchBlocked, ValueError, KeyError) as exc:
            entry["error"] = getattr(exc, "code", "NRE_REFERENCE_PARSE_FAILED")
            work.strategies.append(entry)
            continue
        fresh = results_freshness(rows, now)
        entry["verified"] = int(fresh["status"] == "FRESH")
        doc = _document(task, attempt_id, result, source_id=cand.source_id,
                        title=f"{cand.title} {task.league} {task.season}",
                        excerpt=(f"Incumbent {task.league} {task.season} results source ({SOURCE_LICENSE}). "
                                 f"Freshness {fresh['status']}: {fresh['scored']} results, latest "
                                 f"{fresh['latest_result_date']}, {fresh['unscored_past_dated']} past-dated "
                                 "matches without a result."),
                        evidence_type="RESULTS_DATASET", source_quality="REGISTERED_INCUMBENT",
                        freshness=fresh["status"], rights_status="STORAGE_PERMITTED",
                        observed_at=fresh["latest_result_date"], teams=[r.home for r in rows])
        work.documents.append(doc)
        work.findings.append(_finding(task, attempt_id, source_id=cand.source_id,
                                      finding_type="INCUMBENT_SOURCE_STATUS", outcome="CANDIDATE_FOUND_UNVERIFIED"
                                      if fresh["status"] != "FRESH" else "CANDIDATE_VERIFIED_PENDING_INDEPENDENT_REVIEW",
                                      freshness=fresh["status"], conflict_status="REFERENCE_SOURCE",
                                      verification={"freshness": fresh}, document_ids=[doc["document_id"]]))
        work.strategies.append(entry)
    return rows


def _probe_github_results(task: ResearchTask, cand: SourceCandidate, reference: list, fetcher: NativeFetcher,
                          store: Any, attempt_id: str, now: datetime, work: Work) -> str | None:
    """Probe one discovered repository. Returns the candidate outcome, or None if not a fit."""
    try:
        meta = github_repo(fetcher, cand.locator)
        if meta.get("archived") or meta.get("private") or meta.get("disabled"):
            return None
        branch = str(meta.get("default_branch") or cand.default_branch or "main")
        spdx = (meta.get("license") or {}).get("spdx_id")
        paths = github_tree(fetcher, cand.locator, branch)
        listing = "GIT_TREE_API"
    except FetchBlocked as exc:
        if exc.code not in ("NRE_ACCESS_RESTRICTED", "NRE_SOURCE_RATE_LIMITED"):
            raise
        # API unavailable: use the file links the repository publishes itself.
        meta, branch, spdx = {}, str(cand.default_branch or "main"), cand.software_spdx
        paths = readme_linked_paths(fetcher, cand.locator, branch)
        paths += ["README.md"]
        listing = f"README_LINKS_AFTER_{exc.code}"
    spdx = None if spdx in (None, "NOASSERTION", "NONE") else spdx
    files = find_league_files(paths, task.league or "", task.season or "")
    if not files:
        return None
    data = None
    for path in files[:3]:  # inferred paths are verified by fetching them
        try:
            data = github_raw(fetcher, cand.locator, branch, path)
            files = [path]
            break
        except FetchBlocked as exc:
            if exc.code != "NRE_SOURCE_NOT_FOUND":
                raise
    if data is None:
        return None
    if data.truncated:
        raise FetchBlocked("NRE_DOCUMENT_TRUNCATED", data.canonical_url)
    try:
        rows = parse_results_csv(data.text()) if files[0].lower().endswith(".csv") \
            else parse_openfootball(json.loads(data.text()).get("matches") or [])
    except ValueError:
        return None
    if not rows:
        return None
    fresh = results_freshness(rows, now)
    check = cross_check(rows, reference)

    # Terms: README plus any LICENSE file; conditional GET carries prior rights forward.
    terms_paths = [p for p in paths if p.lower() in ("readme.md", "readme", "license", "license.md",
                                                     "license.txt", "data_license.md")]
    terms_text, terms_docs, carried = "", [], None
    for path in terms_paths[:3]:
        prior = store.latest_document(f"https://raw.githubusercontent.com/{cand.locator}/{branch}/{path}")
        result = github_raw(fetcher, cand.locator, branch, path, max_bytes=300_000,
                            etag=(prior or {}).get("etag"), last_modified=(prior or {}).get("last_modified"))
        if result.not_modified:
            carried = store.latest_rights(cand.source_id)
            continue
        terms_text += "\n" + result.text()
        terms_docs.append((path, result))
    if carried and not terms_docs:
        rights = dict(carried)
    else:
        rights = assess_source_rights(cand.source_id, software_spdx=spdx, terms_text=terms_text,
                                      terms_url=f"https://github.com/{cand.locator}", robots_permitted=True,
                                      assessed_at=now)
        work.rights.append(rights)
    status = storage_status(rights)
    doc_ids = []
    for path, result in terms_docs:
        section = (markdown_section(result.text(), r"licen[cs]e|terms|attribution", 1000)
                   or result.text()[:600])
        doc = _document(task, attempt_id, result, source_id=cand.source_id, title=f"{cand.locator} {path} terms",
                        excerpt=section, evidence_type="SOURCE_TERMS", source_quality="DISCOVERED_UNREVIEWED",
                        freshness="CURRENT", rights_status=status, body=result.text()[:100_000])
        work.documents.append(doc)
        doc_ids.append(doc["document_id"])
    doc = _document(task, attempt_id, data, source_id=cand.source_id, title=f"{cand.locator} {files[0]}",
                    excerpt=(f"Discovered {task.league} {task.season} results dataset {cand.locator}/{files[0]}. "
                             f"Freshness {fresh['status']}: {fresh['scored']} results, latest "
                             f"{fresh['latest_result_date']}, {fresh['unscored_past_dated']} past-dated matches "
                             f"without a result. Cross-check {check['status']} on {check['overlap']} shared "
                             f"matches. Data licence {rights['data_license_label']}."),
                    evidence_type="RESULTS_DATASET", source_quality="DISCOVERED_UNREVIEWED",
                    freshness=fresh["status"], rights_status=status, observed_at=fresh["latest_result_date"],
                    teams=[r.home for r in rows])
    work.documents.append(doc)
    doc_ids.append(doc["document_id"])
    if check["status"] == "CONFLICT":
        outcome = "CONFLICTING_EVIDENCE"
    elif fresh["status"] != "FRESH" or check["status"] != "CONSISTENT":
        outcome = "CANDIDATE_FOUND_UNVERIFIED"
    elif training_permitted(rights):
        outcome = "CANDIDATE_VERIFIED_PENDING_INDEPENDENT_REVIEW"
    else:
        outcome = "CANDIDATE_VERIFIED_RIGHTS_REVIEW_REQUIRED"
    work.findings.append(_finding(
        task, attempt_id, source_id=cand.source_id, finding_type="CANDIDATE_SOURCE", outcome=outcome,
        freshness=fresh["status"], conflict_status=check["status"],
        verification={"freshness": fresh, "cross_check": check, "file": files[0], "branch": branch,
                      "discovered_via": cand.discovered_via, "pushed_at": meta.get("pushed_at") or cand.pushed_at,
                      "listing_method": listing},
        rights_id=rights["rights_id"], document_ids=doc_ids))
    return outcome


def _url_lead_findings(task: ResearchTask, attempt_id: str, leads: Sequence[SourceCandidate], work: Work) -> None:
    """Reported non-GitHub sources are recorded, never fetched, until their domain is approved."""
    for lead in leads:
        if lead.kind != "URL":
            continue
        host = urlsplit(lead.locator).hostname or ""
        work.findings.append(_finding(task, attempt_id, source_id=lead.source_id, finding_type="CANDIDATE_SOURCE",
                                      outcome="CANDIDATE_FOUND_UNVERIFIED", freshness="UNVERIFIED",
                                      conflict_status="NOT_CHECKED",
                                      verification={"discovered_via": lead.discovered_via, "url": lead.locator,
                                                    "domain_approval_required": host,
                                                    "claim_status": "REPORTED_UNVERIFIED"}))


def _research_soccer_results(task: ResearchTask, fetcher: NativeFetcher, store: Any, attempt_id: str,
                             now: datetime, history: Sequence[Mapping[str, Any]],
                             leads: Sequence[SourceCandidate] = ()) -> Work:
    work = Work()
    reference = _openfootball_reference(task, fetcher, attempt_id, now, work)
    prior = store.findings(sport=task.sport, source_type=task.required_source_type)
    reliability = source_reliability(prior)
    candidates: dict[str, SourceCandidate] = {}
    queries: list[str] = []
    for strategy in order_strategies(task.required_source_type, task.permitted_methods, history):
        if strategy == "EXTERNAL_EVIDENCE_INTAKE":
            github_leads = [lead for lead in leads if lead.kind == "GITHUB_REPOSITORY"]
            for lead in github_leads:
                candidates.setdefault(lead.source_id, lead)
            _url_lead_findings(task, attempt_id, leads, work)
            work.strategies.append({"strategy": strategy, "candidates": len(leads), "verified": 0})
        elif strategy == "LOCAL_INDEX":
            known: dict[str, str | None] = {}
            for f in prior:
                if f.get("finding_type") == "CANDIDATE_SOURCE" and f["source_id"].startswith("github:"):
                    known.setdefault(f["source_id"], (f.get("verification") or {}).get("branch"))
            for sid, branch in sorted(known.items()):
                candidates.setdefault(sid, SourceCandidate(sid, "GITHUB_REPOSITORY", sid[7:], "LOCAL_INDEX",
                                                           default_branch=branch))
            work.strategies.append({"strategy": "LOCAL_INDEX", "candidates": len(known), "verified": 0})
        elif strategy == "GITHUB_DATASET_DISCOVERY":
            queries = github_queries(task.sport, task.league, task.season, task.required_source_type, now)
            found, error = safe_search(github_dataset_search, fetcher, queries)
            entry = {"strategy": strategy, "candidates": len(found), "verified": 0, "queries": queries}
            if error:
                entry["error"] = error
                work.errors.append(error)
            for cand in found:
                if cand.source_id not in candidates or not candidates[cand.source_id].default_branch:
                    candidates[cand.source_id] = cand
            work.strategies.append(entry)
    registered = {c.source_id for c in registered_sources(task.sport, task.required_source_type)}
    outcomes: list[str] = []
    probed = []
    for cand in rank_candidates([c for c in candidates.values() if c.source_id not in registered],
                                reliability):
        if len([p for p in probed if p["fit"]]) >= MAX_CANDIDATES or len(probed) >= MAX_CANDIDATES * 3:
            break
        try:
            outcome = _probe_github_results(task, cand, reference, fetcher, store, attempt_id, now, work)
        except FetchBlocked as exc:
            probed.append({"source_id": cand.source_id, "fit": False, "error": exc.code})
            work.errors.append(exc.code)
            if exc.code in ("NRE_RETRIEVAL_BUDGET_EXHAUSTED", "NRE_RETRIEVAL_DEADLINE_EXCEEDED"):
                break
            continue
        probed.append({"source_id": cand.source_id, "fit": outcome is not None, "outcome": outcome})
        if outcome:
            outcomes.append(outcome)
    _credit_strategies(work)
    if outcomes:
        work.outcome = _best(outcomes)
    elif any(e in ("NRE_RETRIEVAL_BUDGET_EXHAUSTED", "NRE_RETRIEVAL_DEADLINE_EXCEEDED") for e in work.errors):
        work.outcome = "BUDGET_EXHAUSTED"
    elif work.errors:
        # A failed probe or search is not evidence that no source exists.
        work.outcome = "ACQUISITION_FAILED"
    else:
        work.outcome = "NO_SOURCE_FOUND"
        work.findings.append(_finding(task, attempt_id, source_id="search:github", finding_type="NEGATIVE_RESULT",
                                      outcome="NO_SOURCE_FOUND", freshness=None, conflict_status=None,
                                      verification={"queries": queries, "candidates": len(candidates),
                                                    "probed": probed}))
    return work


def _credit_strategies(work: Work) -> None:
    """Credit each verified source to the strategy that first surfaced it (learning input)."""
    for entry in work.strategies:
        if entry["strategy"] in ("GITHUB_DATASET_DISCOVERY", "LOCAL_INDEX", "EXTERNAL_EVIDENCE_INTAKE"):
            entry["verified"] = sum(1 for f in work.findings if f["finding_type"] == "CANDIDATE_SOURCE"
                                    and f["outcome"].startswith("CANDIDATE_VERIFIED")
                                    and f["verification"].get("discovered_via") == entry["strategy"])


FEED_QUERIES = {
    "INJURY_REPORT_FEED": ("{sport} injury report data", "{sport} injuries dataset csv"),
    "GOALIE_CONFIRMATION_FEED": ("nhl starting goalies data", "nhl goalie confirmations"),
    "LINEUP_FEED": ("{sport} starting lineups data", "{sport} lineups dataset"),
}


def _research_feed_sources(task: ResearchTask, fetcher: NativeFetcher, store: Any, attempt_id: str,
                           now: datetime, history: Sequence[Mapping[str, Any]],
                           leads: Sequence[SourceCandidate] = ()) -> Work:
    """Source discovery + rights evidence for live-context feeds (injuries, goalies, lineups).

    No freshness verifier exists for these feeds yet, so a candidate can reach
    CANDIDATE_FOUND_UNVERIFIED at most. Nothing here feeds a specialist."""
    from datetime import timedelta as _td

    work = Work()
    candidates: dict[str, SourceCandidate] = {}
    prior = store.findings(sport=task.sport, source_type=task.required_source_type)
    for strategy in order_strategies(task.required_source_type, task.permitted_methods, history):
        if strategy == "EXTERNAL_EVIDENCE_INTAKE":
            for lead in leads:
                if lead.kind == "GITHUB_REPOSITORY":
                    candidates.setdefault(lead.source_id, lead)
            _url_lead_findings(task, attempt_id, leads, work)
            work.strategies.append({"strategy": strategy, "candidates": len(leads), "verified": 0})
        elif strategy == "LOCAL_INDEX":
            known = sorted({f["source_id"] for f in prior if f["source_id"].startswith("github:")})
            for sid in known:
                candidates.setdefault(sid, SourceCandidate(sid, "GITHUB_REPOSITORY", sid[7:], "LOCAL_INDEX"))
            work.strategies.append({"strategy": strategy, "candidates": len(known), "verified": 0})
        elif strategy == "GITHUB_DATASET_DISCOVERY":
            since = (now - _td(days=30)).date().isoformat()
            queries = [q.format(sport=task.sport.lower()) + f" pushed:>{since}"
                       for q in FEED_QUERIES.get(task.required_source_type, ())]
            found, error = safe_search(github_dataset_search, fetcher, queries)
            entry = {"strategy": strategy, "candidates": len(found), "verified": 0, "queries": queries}
            if error:
                entry["error"] = error
                work.errors.append(error)
            for cand in found:
                candidates.setdefault(cand.source_id, cand)
            work.strategies.append(entry)
    outcomes = ["CANDIDATE_FOUND_UNVERIFIED" for f in work.findings]
    for cand in rank_candidates(list(candidates.values()), source_reliability(prior))[:MAX_CANDIDATES]:
        try:
            branch = cand.default_branch
            spdx = cand.software_spdx
            pushed = cand.pushed_at
            if not branch:
                try:
                    meta = github_repo(fetcher, cand.locator)
                    branch, spdx, pushed = meta.get("default_branch"), (meta.get("license") or {}).get("spdx_id"), \
                        meta.get("pushed_at")
                except FetchBlocked as exc:
                    if exc.code not in ("NRE_ACCESS_RESTRICTED", "NRE_SOURCE_RATE_LIMITED"):
                        raise
                    branch = "main"
            readme = github_raw(fetcher, cand.locator, branch or "main", "README.md", max_bytes=300_000)
        except FetchBlocked as exc:
            work.errors.append(exc.code)
            continue
        rights = assess_source_rights(cand.source_id, software_spdx=None if spdx in (None, "NOASSERTION") else spdx,
                                      terms_text=readme.text(), terms_url=f"https://github.com/{cand.locator}",
                                      robots_permitted=True, assessed_at=now)
        work.rights.append(rights)
        section = markdown_section(readme.text(), r"licen[cs]e|terms|attribution", 1000) or readme.text()[:600]
        doc = _document(task, attempt_id, readme, source_id=cand.source_id, title=f"{cand.locator} README terms",
                        excerpt=section, evidence_type="SOURCE_TERMS", source_quality="DISCOVERED_UNREVIEWED",
                        freshness="CURRENT", rights_status=storage_status(rights), body=readme.text()[:100_000])
        work.documents.append(doc)
        work.findings.append(_finding(task, attempt_id, source_id=cand.source_id, finding_type="CANDIDATE_SOURCE",
                                      outcome="CANDIDATE_FOUND_UNVERIFIED", freshness="UNVERIFIED",
                                      conflict_status="NOT_CHECKED",
                                      verification={"discovered_via": cand.discovered_via, "pushed_at": pushed,
                                                    "branch": branch, "verifier": "NOT_IMPLEMENTED_FOR_SOURCE_TYPE",
                                                    "claim_status": "REPORTED_UNVERIFIED"
                                                    if cand.discovered_via == "EXTERNAL_EVIDENCE_INTAKE"
                                                    else "DISCOVERED"},
                                      rights_id=rights["rights_id"], document_ids=[doc["document_id"]]))
        outcomes.append("CANDIDATE_FOUND_UNVERIFIED")
    _credit_strategies(work)
    if outcomes:
        work.outcome = _best(outcomes)
    elif work.errors:
        work.outcome = "ACQUISITION_FAILED"
    else:
        work.outcome = "NO_SOURCE_FOUND"
        work.findings.append(_finding(task, attempt_id, source_id="search:github", finding_type="NEGATIVE_RESULT",
                                      outcome="NO_SOURCE_FOUND", freshness=None, conflict_status=None,
                                      verification={"candidates": 0}))
    return work


def _research_literature(task: ResearchTask, fetcher: NativeFetcher, store: Any, attempt_id: str,
                         now: datetime, history: Sequence[Mapping[str, Any]],
                         leads: Sequence[SourceCandidate] = ()) -> Work:
    work = Work()
    query = f"{task.sport.lower()} {task.market_route.split(':')[-1].lower().replace('_', ' ')} " \
            "probability calibration forecasting"
    refs, error = safe_search(crossref_search, fetcher, query)
    work.strategies.append({"strategy": "CROSSREF", "candidates": len(refs), "verified": 0,
                            **({"error": error} if error else {})})
    for ref in refs:
        if not ref.get("doi"):
            continue
        fake = FetchResult(url=str(ref.get("url")), canonical_url=f"https://doi.org/{ref['doi']}", status=200,
                           content=None, content_sha256=sha256_json(ref), etag=None, last_modified=None,
                           not_modified=False, truncated=False, content_type="application/vnd.crossref+json",
                           retrieved_at=now.isoformat(), access_basis="OFFICIAL_PUBLIC_API")
        doc = _document(task, attempt_id, fake, source_id=f"doi:{ref['doi']}", title=ref["title"],
                        excerpt=f"{ref['title']} ({ref.get('venue') or 'unknown venue'}, {ref.get('year')}). "
                                f"DOI {ref['doi']}.", evidence_type="SCIENTIFIC_REFERENCE",
                        source_quality="CROSSREF_METADATA", freshness="CURRENT",
                        rights_status="METADATA_ONLY_RIGHTS_UNRESOLVED")
        work.documents.append(doc)
        work.findings.append(_finding(task, attempt_id, source_id=f"doi:{ref['doi']}",
                                      finding_type="SCIENTIFIC_REFERENCE", outcome="CANDIDATE_FOUND_UNVERIFIED",
                                      freshness="CURRENT", conflict_status=None,
                                      verification={"licenses": ref.get("licenses")},
                                      document_ids=[doc["document_id"]]))
    work.outcome = ("ACQUISITION_FAILED" if error else
                    "CANDIDATE_FOUND_UNVERIFIED" if work.findings else "NO_SOURCE_FOUND")
    return work


HANDLERS = {
    ("SOCCER", "RESULTS_FEED"): _research_soccer_results,
    ("*", "SCIENTIFIC_LITERATURE"): _research_literature,
    ("*", "INJURY_REPORT_FEED"): _research_feed_sources,
    ("*", "GOALIE_CONFIRMATION_FEED"): _research_feed_sources,
    ("*", "LINEUP_FEED"): _research_feed_sources,
}


# -- receipts ------------------------------------------------------------------
def swarm_receipt(task: ResearchTask, outcome: str, work: Work) -> dict[str, Any]:
    """Network-free research context for the Native Swarm (evidence only)."""
    rights_by_id = {r["rights_id"]: r for r in work.rights}
    sources = []
    for f in work.findings:
        r = rights_by_id.get(f.get("rights_id") or "", {})
        sources.append({"finding_id": f["finding_id"], "source_id": f["source_id"],
                        "finding_type": f["finding_type"], "finding_outcome": f["outcome"],
                        "freshness": f["freshness"], "cross_check": f["conflict_status"],
                        "rights_summary": {"data_license": r.get("data_license_label"),
                                           "storage": r.get("storage_permitted"),
                                           "training": r.get("model_training_permitted"),
                                           "production": r.get("production_use_permitted"),
                                           "upstream_unresolved": r.get("unresolved_upstream_rights"),
                                           "restrictions": r.get("commercial_restrictions") or []} if r else None})
    body = {"schema_version": SWARM_SCHEMA, "task_id": task.task_id, "sport": task.sport, "league": task.league,
            "season": task.season, "blocker_code": task.blocker_code, "outcome": outcome,
            "progress_stage": PROGRESS_STAGE[outcome], "remaining_blockers": remaining_blockers(task, outcome),
            "sources": sources, "evidence_only": True, "prediction_authority": False, "can_execute": False}
    return {**body, "receipt_sha256": sha256_json(body)}


def factory_handoff(task: ResearchTask, work: Work) -> dict[str, Any] | None:
    """Hand the best verified candidate to the Certified Model Factory (no fitting)."""
    from v17.certified_model_factory import receive_research_handoff

    verified = [f for f in work.findings if f["finding_type"] == "CANDIDATE_SOURCE"
                and f["outcome"].startswith("CANDIDATE_VERIFIED")]
    if not verified:
        return None
    best = max(verified, key=lambda f: PROGRESS_STAGE[f["outcome"]])
    rights = next((r for r in work.rights if r["rights_id"] == best["rights_id"]), None) or {}
    handoff = {"schema_version": HANDOFF_SCHEMA, "task_id": task.task_id, "finding_id": best["finding_id"],
               "sport": task.sport, "league": task.league, "season": task.season,
               "blocker_code": task.blocker_code, "source_id": best["source_id"],
               "freshness": best["freshness"], "conflict_status": best["conflict_status"],
               "rights_id": best["rights_id"],
               "rights": {k: rights.get(k) for k in ("data_license_label", "storage_permitted",
                                                     "model_training_permitted", "production_use_permitted",
                                                     "unresolved_upstream_rights", "commercial_restrictions",
                                                     "attribution_required", "review_status")},
               **INERT}
    return {"handoff": handoff, "factory_receipt": receive_research_handoff(handoff)}


# -- orchestration -------------------------------------------------------------
def _run_task(store: Any, blocker: BlockerInput, fetcher: NativeFetcher, now: datetime, owner: str
              ) -> dict[str, Any]:
    task = build_task(blocker)
    summary: dict[str, Any] = {"task_id": task.task_id, "sport": task.sport, "league": task.league,
                               "blocker_code": task.blocker_code, "certification_status": task.certification_status}
    prior = store.load_task(task.task_id)
    attempts = store.attempts(task.task_id) if prior else []
    decision = schedule_decision(task, prior, attempts, now=now)
    summary["decision"] = decision.reason
    summary["progress_before"] = int((prior or {}).get("best_progress_stage") or 0)
    if not decision.schedule:
        if not task.researchable and prior is None:
            store.create_task(task_row(task, status="NOT_RESEARCHABLE", outcome="NOT_ATTEMPTED", best_stage=0,
                                       evidence_refs=[], attempts=0, last_attempt_at=None, next_eligible_at=None,
                                       now=now))
        summary["progress_after"] = summary["progress_before"]
        return summary
    handler = HANDLERS.get((task.sport, task.required_source_type)) or HANDLERS.get(("*", task.required_source_type))
    if prior is None:
        store.create_task(task_row(task, status="OPEN", outcome="NOT_ATTEMPTED", best_stage=0, evidence_refs=[],
                                   attempts=0, last_attempt_at=None, next_eligible_at=None, now=now))
    if handler is None:
        store.update_task(task.task_id, {"status": "HELD", "updated_at": now.isoformat()})
        summary.update(decision="NRE_HANDLER_NOT_IMPLEMENTED", progress_after=summary["progress_before"])
        return summary
    if not store.claim_task(task.task_id, owner, LEASE_TTL_SECONDS):
        summary.update(decision="LEASE_HELD", progress_after=summary["progress_before"])
        return summary
    # Attempt times are on the run's clock (wall clock in production) so that
    # scheduling compares like with like.
    started, t0 = now, time.monotonic()
    attempt_id = sha256_json({"task_id": task.task_id, "started_at": started.isoformat(), "owner": owner})
    try:
        work = handler(task, fetcher, store, attempt_id, now, store.strategy_history(), tuple(blocker.leads))
    except FetchBlocked as exc:
        work = Work(outcome="BUDGET_EXHAUSTED" if "BUDGET" in exc.code or "DEADLINE" in exc.code
                    else "ACQUISITION_FAILED", errors=[exc.code])
    finished = now + timedelta(seconds=time.monotonic() - t0)
    handoff = factory_handoff(task, work)
    if handoff:
        for f in work.findings:
            if f["finding_id"] == handoff["handoff"]["finding_id"]:
                f["accepted_for_handoff"] = handoff["factory_receipt"]["status"] == \
                    "QUEUED_FOR_INDEPENDENT_SOURCE_REVIEW"
    swarm = swarm_receipt(task, work.outcome, work)
    receipt = {"engine": ENGINE_VERSION, "worker_id": RESEARCH_WORKER_ID, "authority_ceiling": AUTHORITY_CEILING,
               "decision": decision.reason, "budget": fetcher.budget(), "errors": work.errors,
               "swarm_receipt": swarm, "factory": handoff, "paid_providers_used": [], "spend_usd": 0, **INERT}
    store.insert_attempt({
        "attempt_id": attempt_id, "task_id": task.task_id, "required_source_type": task.required_source_type,
        "schedule_reason": decision.reason, "outcome": work.outcome, "progress_stage": PROGRESS_STAGE[work.outcome],
        "strategies": work.strategies, "started_at": started.isoformat(), "finished_at": finished.isoformat(),
        "worker_owner": owner, "receipt": receipt, "receipt_sha256": sha256_json(receipt), **INERT})
    for row in work.rights:
        store.insert_rights({k: v for k, v in row.items()})
    stored_docs = sum(1 for doc in work.documents if store.insert_document(doc))
    for f in work.findings:
        store.insert_finding(f)
    best_prior = int((prior or {}).get("best_progress_stage") or 0)
    row = task_row(task, status="OPEN", outcome=work.outcome, best_stage=best_prior,
                   evidence_refs=[f["finding_id"] for f in work.findings],
                   attempts=len(attempts) + 1, last_attempt_at=finished,
                   next_eligible_at=next_eligible_after(task, work.outcome, finished), now=now)
    row.update({"lease_owner": None, "lease_expires_at": None})
    store.update_task(task.task_id, row)
    summary.update(outcome=work.outcome, attempt_id=attempt_id, progress_after=row["best_progress_stage"],
                   findings=len(work.findings), documents_new=stored_docs,
                   documents_duplicate=len(work.documents) - stored_docs, rights_records=len(work.rights),
                   factory_status=(handoff or {}).get("factory_receipt", {}).get("status"),
                   swarm_receipt_sha256=swarm["receipt_sha256"], remaining_blockers=row["remaining_blockers"])
    return summary


def run_native_research(store: Any, blockers: Sequence[BlockerInput], *, fetcher: NativeFetcher | None = None,
                        now: datetime | None = None, owner: str = "wow-nre:render-web") -> dict[str, Any]:
    now = _now(now)
    receipt: dict[str, Any] = {"schema_version": RUN_SCHEMA, "engine": ENGINE_VERSION,
                               "worker_id": RESEARCH_WORKER_ID, "authority_ceiling": AUTHORITY_CEILING,
                               "as_of_utc": now.isoformat(), "status": "STARTED", "tasks": [],
                               "paid_providers_used": [], "spend_usd": 0, **INERT}
    try:
        fetcher = fetcher or NativeFetcher(deadline_s=150)
        ordered = sorted(blockers, key=lambda b: (b.blocker_code, b.league or "", b.sport))
        for blocker in ordered:
            try:
                receipt["tasks"].append(_run_task(store, blocker, fetcher, now, owner))
            except StoreError as exc:
                receipt["tasks"].append({"blocker_code": blocker.blocker_code, "league": blocker.league,
                                         "status": "FAILED", "code": exc.code})
            except ValueError as exc:
                receipt["tasks"].append({"blocker_code": blocker.blocker_code, "league": blocker.league,
                                         "status": "FAILED", "code": str(exc)[:120]})
        receipt["budget"] = fetcher.budget()
        receipt["status"] = "COMPLETED"
    except Exception as exc:  # noqa: BLE001 - never raises into the caller
        receipt.update(status="FAILED", code=f"NRE_RUN_{type(exc).__name__.upper()}")
    return receipt


def soccer_results_research_after_forward(db: Any, forward: Mapping[str, Any], *,
                                          now: datetime | None = None, fetcher: NativeFetcher | None = None,
                                          intake_issues: Sequence[int] | None = None) -> dict[str, Any]:
    """Maintenance hook: research soccer leagues blocked by stale results, plus any
    structured evidence posted on the intake incidents (default #2880)."""
    from .intake import DEFAULT_INTAKE_ISSUES

    if intake_issues is None:
        intake_issues = DEFAULT_INTAKE_ISSUES
    from .store import SupabaseResearchStore

    now = _now(now)
    year = now.year if now.month >= 7 else now.year - 1
    season = f"{year}-{(year + 1) % 100:02d}"
    blockers = []
    for league, rec in sorted((forward.get("leagues") or {}).items()):
        if not isinstance(rec, Mapping) or not (rec.get("blocked_by_code") or {}).get("SOCCER_SOURCE_RESULTS_STALE"):
            continue
        blockers.append(BlockerInput(
            sport="SOCCER", league=league, season=season,
            market_route=f"SOCCER:{league}:OUTRIGHT_WINNER:THREE_WAY_1X2",
            specialist_id=str(rec.get("model_family") or f"SOCCER_1X2_{league}"),
            candidate_id=rec.get("frozen_artifact_checksum"), blocker_code="SOCCER_SOURCE_RESULTS_STALE",
            candidate_ready=bool(rec.get("registration_id")),
            context={"source": "OPENFOOTBALL:football.json", "registration_id": rec.get("registration_id"),
                     "frozen_artifact_checksum": rec.get("frozen_artifact_checksum")}))
    # Temporary external intake: structured evidence posted on WOW incidents.
    fetcher = fetcher or NativeFetcher(deadline_s=60)
    intake: dict[str, Any] = {"accepted": 0, "rejected": []}
    if intake_issues:
        from .intake import blockers_from_submissions, fetch_submissions

        try:
            subs = fetch_submissions(fetcher, intake_issues)
            intake = {"accepted": len(subs["accepted"]), "rejected": subs["rejected"][:50]}
            blockers.extend(blockers_from_submissions(subs["accepted"]))
        except FetchBlocked as exc:
            intake = {"accepted": 0, "rejected": [], "code": exc.code}
    if not blockers:
        return {"status": "NO_RESEARCHABLE_BLOCKERS", "tasks": [], "intake": intake, "spend_usd": 0, **INERT}
    receipt = run_native_research(SupabaseResearchStore(db), blockers, now=now, fetcher=fetcher)
    receipt["intake"] = intake
    return receipt
