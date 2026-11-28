"""WOW Source Discovery and Native Search front-ends (no paid search provider)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from .extract import LEAGUE_NAMES
from .fetcher import FetchBlocked, NativeFetcher

GITHUB_API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"


@dataclass(frozen=True)
class SourceCandidate:
    source_id: str          # e.g. "github:owner/repo"
    kind: str               # GITHUB_REPOSITORY | REGISTERED | URL
    locator: str            # owner/repo or URL
    discovered_via: str     # strategy that produced it
    title: str = ""
    description: str = ""
    pushed_at: str | None = None
    software_spdx: str | None = None
    default_branch: str | None = None


# Sources WOW already uses per lane; their current status is always re-checked.
REGISTERED_SOURCES: dict[tuple[str, str], tuple[SourceCandidate, ...]] = {
    ("SOCCER", "RESULTS_FEED"): (
        SourceCandidate("github:openfootball/football.json", "REGISTERED", "openfootball/football.json",
                        "REGISTERED_SOURCE", "OpenFootball football.json", "CC0 results (incumbent)",
                        None, "CC0-1.0", "master"),
    ),
}


def registered_sources(sport: str, source_type: str) -> tuple[SourceCandidate, ...]:
    return REGISTERED_SOURCES.get((sport.upper(), source_type), ())


def github_queries(sport: str, league: str | None, season: str | None, source_type: str,
                   now: datetime) -> list[str]:
    since = (now - timedelta(days=21)).date().isoformat()
    if sport == "SOCCER" and source_type in ("RESULTS_FEED", "FIXTURE_FEED"):
        name = LEAGUE_NAMES.get(league or "", "football")
        label = f"{season[:4]}/{season[-2:]}" if season else ""
        return [f"{name} results csv pushed:>{since}",
                f"football results {label} csv pushed:>{since}".replace("  ", " ")]
    return [f"{sport.lower()} {source_type.lower().replace('_', ' ')} dataset pushed:>{since}"]


def github_dataset_search(fetcher: NativeFetcher, queries: Sequence[str], *, per_query: int = 8
                          ) -> list[SourceCandidate]:
    seen: dict[str, SourceCandidate] = {}
    for query in queries:
        body = fetcher.get_json(f"{GITHUB_API}/search/repositories?q={quote(query, safe=':>')}"
                                f"&sort=updated&order=desc&per_page={per_query}")
        for item in (body.get("items") or [])[:per_query]:
            name = item.get("full_name")
            if not isinstance(name, str) or item.get("archived") or item.get("disabled") or item.get("private"):
                continue
            lic = item.get("license") or {}
            seen.setdefault(name, SourceCandidate(
                f"github:{name}", "GITHUB_REPOSITORY", name, "GITHUB_DATASET_DISCOVERY",
                str(item.get("name") or ""), str(item.get("description") or "")[:300],
                item.get("pushed_at"), lic.get("spdx_id") if lic.get("spdx_id") not in (None, "NOASSERTION")
                else None, item.get("default_branch")))
    return list(seen.values())


def github_repo(fetcher: NativeFetcher, locator: str) -> Mapping[str, Any]:
    return fetcher.get_json(f"{GITHUB_API}/repos/{locator}")


def github_tree(fetcher: NativeFetcher, locator: str, branch: str) -> list[str]:
    body = fetcher.get_json(f"{GITHUB_API}/repos/{locator}/git/trees/{quote(branch)}?recursive=1")
    return [t["path"] for t in body.get("tree") or [] if t.get("type") == "blob" and isinstance(t.get("path"), str)]


def github_raw(fetcher: NativeFetcher, locator: str, branch: str, path: str, **kwargs: Any):
    return fetcher.get(f"{RAW}/{locator}/{quote(branch)}/{quote(path)}", **kwargs)


def readme_linked_paths(fetcher: NativeFetcher, locator: str, branch: str, *, max_docs: int = 2) -> list[str]:
    """Approved-domain links: file paths the repository itself publishes in its docs.

    Used when the tree API is unavailable (rate limits, access). Paths come from
    raw links and relative links in README and linked index documents, plus
    folder names applied to a published file template. Every inferred path is
    still fetched and verified before it is used.
    """
    import re

    raw = re.compile(rf"https://raw\.githubusercontent\.com/{re.escape(locator)}/{re.escape(branch)}/([^\s)\"'>`]+)")
    rel = re.compile(r"\]\((?!https?:|#)([^)#\s]+)\)")
    tick = re.compile(r"`([A-Za-z0-9][A-Za-z0-9_\-]{2,80})`")
    readme = github_raw(fetcher, locator, branch, "README.md", max_bytes=400_000).text()
    files = set(raw.findall(readme))
    docs = [p for p in rel.findall(readme) if p.lower().endswith(".md")]
    docs.sort(key=lambda p: (0 if re.search(r"index|league|competition|catalog", p, re.I) else 1, p))
    folders: set[str] = set()
    for doc in docs[:max_docs]:
        try:
            text = github_raw(fetcher, locator, branch, doc, max_bytes=400_000).text()
        except FetchBlocked:
            continue
        files.update(raw.findall(text))
        folders.update(tick.findall(text))
    files.update(p for p in rel.findall(readme) if p.lower().endswith((".csv", ".json")))
    inferred = set()
    for path in files:
        parts = path.split("/")
        if len(parts) >= 3:
            for folder in folders:
                inferred.add("/".join(parts[:-2] + [folder, parts[-1]]))
    return sorted(files | inferred)


def crossref_search(fetcher: NativeFetcher, query: str, *, rows: int = 5) -> list[dict[str, Any]]:
    """Scientific metadata (titles, DOIs, licence links) from the free Crossref API."""
    body = fetcher.get_json(f"https://api.crossref.org/works?query={quote(query)}&rows={rows}"
                            "&select=DOI,title,issued,URL,license,container-title,type")
    out = []
    for item in ((body.get("message") or {}).get("items") or [])[:rows]:
        title = (item.get("title") or [""])[0]
        issued = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
        out.append({"doi": item.get("DOI"), "title": str(title)[:400], "url": item.get("URL"),
                    "year": issued[0] if issued else None,
                    "venue": ((item.get("container-title") or [""])[0])[:200],
                    "licenses": [lic.get("URL") for lic in item.get("license") or [] if lic.get("URL")]})
    return out


def rank_candidates(candidates: Sequence[SourceCandidate], reliability: Mapping[str, float]
                    ) -> list[SourceCandidate]:
    """Known-reliable sources first (learning), then most recently updated."""
    return sorted(candidates, key=lambda c: (-reliability.get(c.source_id, 0.0), _neg_time(c.pushed_at),
                                             c.source_id))


def _neg_time(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return -datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def safe_search(fn, *args, **kwargs) -> tuple[list, str | None]:
    try:
        return fn(*args, **kwargs), None
    except FetchBlocked as exc:
        return [], exc.code
