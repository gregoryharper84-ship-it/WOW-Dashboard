"""Temporary external research intake (ChatGPT -> GitHub incident -> NRE).

During development, structured research evidence may be posted on a WOW
incident issue as a fenced ``wow-research-evidence`` JSON block. The NRE reads
it as untrusted data: only the repository owner's comments are accepted, only
mapped blockers become tasks, probability-like fields are refused, and every
reported source is verified natively before it can count. Reported claims stay
REPORTED-UNVERIFIED until native acquisition confirms them, so native research
progressively replaces this handoff.

Accepted block:

    ```wow-research-evidence
    {"schema_version": "WOW_EXTERNAL_RESEARCH_EVIDENCE_V1", "sport": "NFL",
     "blocker_code": "NFLVERSE_INJURY_REPORT_STALE", "season": "2026",
     "sources": [{"url": "https://github.com/owner/repo", "title": "...", "claimed_license": "..."}]}
    ```
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence
from urllib.parse import quote, urlsplit

from .contract import RESEARCH_SPECS, BlockerInput
from .discovery import SourceCandidate
from .fetcher import NativeFetcher

INTAKE_SCHEMA = "WOW_EXTERNAL_RESEARCH_EVIDENCE_V1"
REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
OWNER_LOGIN = REPOSITORY.split("/")[0]
DEFAULT_INTAKE_ISSUES = (2880,)
MAX_SOURCES = 10
_FENCE = re.compile(r"```wow-research-evidence[ \t]*\r?\n(.*?)```", re.S)
_FORBIDDEN = re.compile(r"probab|calibrat|confidence|edge$|odds|stake|wager|approv|certif|pick|predict|order",
                        re.I)
_ALLOWED_KEYS = {"schema_version", "sport", "league", "season", "blocker_code", "market_route", "specialist_id",
                 "candidate_id", "sources", "submitted_by", "summary"}
_SOURCE_KEYS = {"url", "title", "claimed_license", "note", "kind"}


def _forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_FORBIDDEN.search(str(k)) or _forbidden(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_forbidden(v) for v in value)
    return False


def parse_comment(body: str) -> list[Any]:
    out: list[Any] = []
    for block in _FENCE.findall(body or "")[:5]:
        try:
            out.append(json.loads(block))
        except ValueError:
            out.append({"_invalid": "NRE_INTAKE_JSON_INVALID"})
    return out


def lead_from_url(url: str) -> SourceCandidate | None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return None
    host, segs = parts.hostname.lower(), [s for s in parts.path.split("/") if s]
    if host == "github.com" and len(segs) >= 2:
        locator = f"{segs[0]}/{segs[1].removesuffix('.git')}"
        branch = segs[3] if len(segs) >= 4 and segs[2] in ("tree", "blob") else None
        return SourceCandidate(f"github:{locator}", "GITHUB_REPOSITORY", locator, "EXTERNAL_EVIDENCE_INTAKE",
                               default_branch=branch)
    if host == "raw.githubusercontent.com" and len(segs) >= 3:
        locator = f"{segs[0]}/{segs[1]}"
        return SourceCandidate(f"github:{locator}", "GITHUB_REPOSITORY", locator, "EXTERNAL_EVIDENCE_INTAKE",
                               default_branch=segs[2])
    return SourceCandidate(f"url:{host}{parts.path}", "URL", url, "EXTERNAL_EVIDENCE_INTAKE")


def validate_submission(payload: Any, *, author: str, allowed_authors: Sequence[str] = (OWNER_LOGIN,)
                        ) -> tuple[dict[str, Any] | None, str | None]:
    if author not in allowed_authors:
        return None, "NRE_INTAKE_AUTHOR_NOT_ALLOWED"
    if not isinstance(payload, Mapping) or payload.get("_invalid"):
        return None, (payload or {}).get("_invalid", "NRE_INTAKE_SCHEMA_INVALID") \
            if isinstance(payload, Mapping) else "NRE_INTAKE_SCHEMA_INVALID"
    if payload.get("schema_version") != INTAKE_SCHEMA or set(payload) - _ALLOWED_KEYS:
        return None, "NRE_INTAKE_SCHEMA_INVALID"
    if _forbidden(payload):
        return None, "NRE_INTAKE_FORBIDDEN_FIELD"
    if payload.get("blocker_code") not in RESEARCH_SPECS:
        return None, "NRE_INTAKE_BLOCKER_UNMAPPED"
    sources = payload.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        return None, "NRE_INTAKE_SOURCES_INVALID"
    leads = []
    for src in sources:
        if not isinstance(src, Mapping) or set(src) - _SOURCE_KEYS or not isinstance(src.get("url"), str):
            return None, "NRE_INTAKE_SOURCES_INVALID"
        lead = lead_from_url(src["url"].strip())
        if lead is None:
            return None, "NRE_INTAKE_URL_INVALID"
        leads.append(lead)
    if not isinstance(payload.get("sport"), str) or not payload["sport"].strip():
        return None, "NRE_INTAKE_SCHEMA_INVALID"
    return {**dict(payload), "leads": leads}, None


def fetch_submissions(fetcher: NativeFetcher, issues: Sequence[int] = DEFAULT_INTAKE_ISSUES, *,
                      repository: str = REPOSITORY) -> dict[str, Any]:
    """Read incident comments (public API) and return accepted and rejected submissions."""
    accepted, rejected = [], []
    for issue in issues:
        comments = fetcher.get_json(f"https://api.github.com/repos/{quote(repository)}/issues/{int(issue)}"
                                    "/comments?per_page=100")
        for comment in comments if isinstance(comments, list) else []:
            author = str((comment.get("user") or {}).get("login") or "")
            for payload in parse_comment(str(comment.get("body") or "")):
                sub, code = validate_submission(payload, author=author)
                ref = {"issue": int(issue), "comment_id": comment.get("id"), "author": author,
                       "created_at": comment.get("created_at")}
                if code:
                    rejected.append({**ref, "code": code})
                else:
                    accepted.append({**ref, "submission": sub})
    return {"accepted": accepted, "rejected": rejected}


def blockers_from_submissions(accepted: Sequence[Mapping[str, Any]]) -> list[BlockerInput]:
    """One blocker per (sport, league, blocker); leads from every comment are merged."""
    merged: dict[tuple, dict[str, Any]] = {}
    for item in accepted:
        sub = item["submission"]
        sport = str(sub["sport"]).strip().upper()
        key = (sport, sub.get("league"), sub["blocker_code"])
        entry = merged.setdefault(key, {"sub": sub, "leads": {}, "refs": []})
        for lead in sub["leads"]:
            entry["leads"].setdefault(lead.source_id, lead)
        entry["refs"].append(item.get("comment_id"))
    out = []
    for (sport, league, code), entry in sorted(merged.items(), key=lambda kv: json.dumps(kv[0], default=str)):
        sub = entry["sub"]
        out.append(BlockerInput(
            sport=sport, league=(str(league).upper() if league else None), season=sub.get("season"),
            market_route=str(sub.get("market_route") or f"{sport}:PLAYER_PROP"),
            specialist_id=str(sub.get("specialist_id") or f"{sport}_PROP_SPECIALIST"),
            candidate_id=sub.get("candidate_id"), blocker_code=code,
            context={"intake": "GITHUB_INCIDENT"},  # leads, not comment count, are the evidence
            leads=tuple(entry["leads"][k] for k in sorted(entry["leads"]))))
    return out
