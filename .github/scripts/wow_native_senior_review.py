#!/usr/bin/env python3
"""GitHub-backed, vendor-independent, advisory WOW senior code review.

Runs ONLY from a trusted protected-main checkout in a read-only PR intake
environment. Never checks out or executes PR source. No paid AI/API provider.
PR comments are advisory and cannot become QA, release or model receipts.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "artifacts/wow-engine"))

from v17.native_senior_review import build_review_packet  # noqa: E402
from v17.native_senior_review_static import run_static_review  # noqa: E402
from v17.native_senior_review_local_ai import (  # noqa: E402
    review_with_local_model, LocalSemanticHold,
)

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
API_ROOT = "https://api.github.com/repos/" + REPO + "/"
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_MAX_RESPONSE = 3_000_000
_MAX_FILE = 250_000
_MAX_FILES = 50
_MAX_CONTEXT = 12
_COMMENT_MARKER = "<!-- WOW_NATIVE_SENIOR_ENGINEER_ADVISORY_V1 -->"
_AGENT = "wow-native-senior-reviewer/1"


class ReviewHold(Exception):
    """Typed non-acceptance; not a model failure or governance verdict."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class GitHubClient:
    def __init__(self, token: str):
        if not isinstance(token, str) or not token.strip():
            raise ReviewHold("GITHUB_TOKEN_MISSING")
        self._token = token
        self._http = build_opener(_NoRedirect())

    def request(self, path: str, *, method: str = "GET", body: dict | None = None):
        if not isinstance(path, str) or not path.startswith("/") or any(
                x in path for x in ("\r", "\n", "\\", "..")):
            raise ReviewHold("GITHUB_API_PATH_UNTRUSTED")
        if method not in {"GET", "POST", "PATCH"}:
            raise ReviewHold("GITHUB_METHOD_UNSUPPORTED")
        content = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            API_ROOT + path.lstrip("/"), method=method, data=content,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + self._token,
                "User-Agent": _AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
                **({"Content-Type": "application/json"} if content else {}),
            },
        )
        try:
            with self._http.open(request, timeout=15) as response:
                raw = response.read(_MAX_RESPONSE + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            code = exc.code if isinstance(exc, HTTPError) else "TRANSPORT"
            raise ReviewHold(f"GITHUB_API_{code}") from None
        if len(raw) > _MAX_RESPONSE:
            raise ReviewHold("GITHUB_RESPONSE_OVERSIZED")
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ReviewHold("GITHUB_JSON_INVALID") from None


def _path(path: str) -> str:
    if (not isinstance(path, str) or len(path) > 400 or not path
            or path.startswith("/") or "\\" in path
            or any(ord(c) < 32 or ord(c) == 127 or c == "`" for c in path)
            or any(p in {"", ".", ".."} for p in path.split("/"))):
        raise ReviewHold("GITHUB_FILE_PATH_INVALID")
    return path


def _encoded(path: str) -> str:
    return "/".join(quote(x, safe="") for x in _path(path).split("/"))


def _pr(client, number: int, head_sha: str, *, expected_base: str | None = None):
    pr = client.request(f"/pulls/{number}")
    if (not isinstance(pr, dict) or pr.get("number") != number
            or pr.get("state") != "open" or pr.get("draft") is True
            or pr.get("head", {}).get("sha") != head_sha
            or pr.get("head", {}).get("repo", {}).get("full_name") != REPO
            or pr.get("base", {}).get("ref") != "main"
            or pr.get("base", {}).get("repo", {}).get("full_name") != REPO):
        raise ReviewHold("PR_IDENTITY_OR_SHA_CHANGED")
    base = pr.get("base", {}).get("sha")
    if not isinstance(base, str) or not SHA_RE.fullmatch(base) or base == head_sha:
        raise ReviewHold("PR_BASE_SHA_INVALID")
    if expected_base is not None and base != expected_base:
        raise ReviewHold("PR_BASE_SHA_CHANGED")
    return pr


def _fetch_source(client, path: str, sha: str) -> str:
    payload = client.request(f"/contents/{_encoded(path)}?ref={sha}")
    if not isinstance(payload, dict) or payload.get("type") != "file":
        raise ReviewHold("PR_SOURCE_NOT_FILE")
    if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        raise ReviewHold("PR_SOURCE_ENCODING_UNSUPPORTED")
    try:
        raw = base64.b64decode("".join(payload["content"].split()), validate=True)
        if len(raw) > _MAX_FILE:
            raise ReviewHold("PR_SOURCE_OVERSIZED")
        return raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise ReviewHold("PR_SOURCE_INVALID_UTF8") from None


def _incident(pr: dict, number: int) -> str:
    body = pr.get("body") or ""
    if not isinstance(body, str):
        return "PR-" + str(number)
    named = re.findall(r"(?im)^(?:Incident|Parent):\s*#([1-9][0-9]*)(?:\s|;|$)", body)
    if len(set(named)) > 1:
        raise ReviewHold("PR_INCIDENT_AMBIGUOUS")
    return named[0] if named else "PR-" + str(number)


def _files(client, number: int, count: int) -> list[dict]:
    if type(count) is not int or count < 1:
        raise ReviewHold("PR_FILE_COUNT_INVALID")
    if count > _MAX_FILES:
        raise ReviewHold("PR_FILE_COUNT_EXCEEDS_LIMIT")
    raw = client.request(f"/pulls/{number}/files?per_page=100&page=1")
    if not isinstance(raw, list) or len(raw) != count:
        raise ReviewHold("PR_FILE_INVENTORY_INCOMPLETE")
    names = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ReviewHold("PR_FILE_INVENTORY_INVALID")
        name = _path(item.get("filename"))
        if name in names:
            raise ReviewHold("PR_FILE_DUPLICATED")
        names.add(name)
        status = item.get("status")
        if status not in {"added", "modified", "removed",
                          "renamed", "copied", "changed"}:
            raise ReviewHold("PR_FILE_STATUS_UNSUPPORTED")
        # Deleted and renamed paths cannot be inspected through a head-only
        # source lookup. Holding is safer than certifying a false-clean diff.
        # Base-side compatibility review requires a separately accepted adapter.
        if status == "removed":
            raise ReviewHold("PR_DELETION_BASE_REVIEW_UNAVAILABLE")
        if status == "renamed":
            raise ReviewHold("PR_RENAME_BASE_REVIEW_UNAVAILABLE")
        if status == "copied":
            raise ReviewHold("PR_COPY_PROVENANCE_UNAVAILABLE")
        if not isinstance(item.get("patch"), str) or not item["patch"]:
            raise ReviewHold("PR_PATCH_UNAVAILABLE")
    return raw


def _related_sources(client, paths: list[str], head: str):
    """Bounded sibling import scan, not claimed complete-repository coverage."""
    dirs = sorted({p.rpartition("/")[0] for p in paths if p.endswith(".py") and "/" in p})
    related = {}
    warnings = []
    selected = set(paths)
    for directory in dirs[:4]:
        try:
            manifest = client.request(f"/contents/{_encoded(directory)}?ref={head}")
        except ReviewHold:
            warnings.append("SIBLING_DIRECTORY_READ_FAILED:" + directory)
            continue
        if not isinstance(manifest, list):
            warnings.append("SIBLING_DIRECTORY_UNAVAILABLE:" + directory)
            continue
        if len(manifest) >= 1000:
            warnings.append("SIBLING_DIRECTORY_INVENTORY_MAY_BE_TRUNCATED:" + directory)
        choices = sorted(x.get("path") for x in manifest
                         if isinstance(x, dict) and x.get("type") == "file"
                         and isinstance(x.get("path"), str)
                         and x["path"].endswith(".py") and x["path"] not in selected)
        # Prefer modules sharing a changed stem; broad repo-wide caller
        # references remain unknown and are never called fully audited.
        stems = [p.rsplit("/", 1)[-1].removesuffix(".py").split("_")[0]
                 for p in paths if p.startswith(directory + "/") and p.endswith(".py")]
        choices.sort(key=lambda name: (
            not any(name.rsplit("/", 1)[-1].startswith(stem) for stem in stems),
            name))
        for path in choices[:_MAX_CONTEXT]:
            if len(related) >= _MAX_CONTEXT:
                warnings.append("SIBLING_CONTEXT_BUDGET_EXHAUSTED")
                break
            try:
                candidate = _fetch_source(client, path, head)
                if (len(candidate.encode("utf-8")) > 24_000
                        or sum(len(v.encode("utf-8")) for v in related.values())
                        + len(candidate.encode("utf-8")) > 128_000):
                    warnings.append("SIBLING_CONTEXT_TOO_LARGE:" + path)
                    continue
                related[_path(path)] = candidate
            except ReviewHold:
                warnings.append("SIBLING_FILE_UNREADABLE:" + path)
        if len(choices) > _MAX_CONTEXT:
            warnings.append("SIBLING_CONTEXT_TRUNCATED:" + directory)
    if len(dirs) > 4:
        warnings.append("SIBLING_DIRECTORY_BUDGET_EXHAUSTED")
    return related, sorted(set(warnings))


def collect_pr_review(client, *, number: int, expected_head: str,
                      local_model: str | None = None):
    if type(number) is not int or number < 1:
        raise ReviewHold("PR_NUMBER_INVALID")
    if not isinstance(expected_head, str) or not SHA_RE.fullmatch(expected_head):
        raise ReviewHold("EXPECTED_HEAD_INVALID")
    pr = _pr(client, number, expected_head)
    changes = _files(client, number, pr.get("changed_files"))
    patches = {}
    sources = {}
    for item in changes:
        path = item["filename"]
        patches[path] = item["patch"]
        if item["status"] == "removed":
            sources[path] = ""
        else:
            sources[path] = _fetch_source(client, path, expected_head)
    siblings, warnings = _related_sources(client, list(patches), expected_head)
    sources.update(siblings)
    pkt = build_review_packet(
        incident_id=_incident(pr, number), pr_number=number,
        base_sha=pr["base"]["sha"], head_sha=expected_head,
        changed_patches=patches, repository_sources=sources,
    )
    # Re-fetch metadata: no report or comment can refer to a displaced PR head.
    _pr(client, number, expected_head, expected_base=pr["base"]["sha"])
    pkt["authenticated_head"] = expected_head
    pkt["context_coverage"] = "CHANGED_FILES_PLUS_BOUNDED_SIBLING_PYTHON"
    pkt["context_warnings"] = warnings
    # Context-packet hash is invalidated by metadata attachment; intentionally
    # restore canonical hash after adding independently sourced intake data.
    check = dict(pkt)
    check.pop("packet_sha256", None)
    pkt["packet_sha256"] = hashlib.sha256(json.dumps(
        check, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")).hexdigest()
    result = run_static_review(pkt)
    local = None
    semantic_status = "NOT_RUN"
    if local_model is not None:
        try:
            local = review_with_local_model(pkt, model=local_model)
            semantic_status = "LOCAL_HYPOTHESES_ONLY"
        except LocalSemanticHold as exc:
            semantic_status = "LOCAL_REVIEW_HELD:" + str(exc)
    _pr(client, number, expected_head, expected_base=pr["base"]["sha"])
    return {"schema": "WOW_NATIVE_REVIEW_RUN_V1",
            "review": result,
            "pr": number, "head_sha": expected_head,
            "base_sha": pkt["base_sha"], "incident_id": pkt["incident_id"],
            "packet_sha256": pkt["packet_sha256"],
            "coverage": pkt["context_coverage"],
            "warnings": warnings, "can_execute": False,
            "release_authority": "NONE_ADVISORY_ONLY",
            "semantic_review": semantic_status,
            "local_semantic_result": local,
            "deployment_verified": False}


def markdown_summary(result: dict) -> str:
    report = result["review"]
    lines = [
        _COMMENT_MARKER,
        "### WOW Native Senior Review — advisory, not a release gate",
        "",
        f"PR #{result['pr']} · exact head `{result['head_sha']}`",
        f"Canonical incident: `{result['incident_id']}`",
        f"Review: `{report['status']}`; changed paths: {len(report.get('changed_paths', []))}",
        f"Static hypotheses: {report['finding_count']} (not proven defects)",
        f"AI semantic review: **{result['semantic_review']}**; QA/Release: **NOT PERFORMED**",
        "Source coverage: changed files plus bounded sibling Python imports, **not** whole-repo proof.",
        "",
    ]
    for item in report["findings"][:10]:
        lines.append(f"- **{item['severity']} candidate** `{item['rule_id']}` "
                     f"in `{item['file']}:{item['line']}`: {item['reason']}. "
                     "Owner: AMRT Root Cause Investigator; reproduce before repair.")
    if report.get("unreported_findings"):
        lines.append(f"- {report['unreported_findings']} additional findings omitted; "
                     "the run is incomplete.")
    semantic = result.get("local_semantic_result")
    if isinstance(semantic, dict):
        for item in semantic.get("findings", [])[:8]:
            lines.append(f"- **Local AI {item['severity']} hypothesis** in "
                         f"`{item['file']}`: {item['reason']}. "
                         "Requires independent reproduction.")
    if result["warnings"]:
        lines.append("- Limited context: " + ", ".join(result["warnings"][:6]))
    lines += ["", f"Packet digest: `{result['packet_sha256']}`",
              "**No self-certification, production activation, wager execution or automatic repair.**",
              "Existing independent SIRT, QA/Release and protected merge remain mandatory."]
    return "\n".join(lines)[:13_000]


def post_advisory_comment(client, *, number: int, head_sha: str, text: str,
                          base_sha: str | None = None):
    """One bot-owned comment per PR. Revalidate SHA before each write."""
    _pr(client, number, head_sha, expected_base=base_sha)
    actor = []
    for page in range(1, 6):
        part = client.request(f"/issues/{number}/comments?per_page=100&page={page}")
        if not isinstance(part, list):
            raise ReviewHold("PR_COMMENT_INVENTORY_INVALID")
        actor.extend(part)
        if len(part) < 100:
            break
    else:
        raise ReviewHold("PR_COMMENT_INVENTORY_LIMIT_REACHED")
    existing = [c for c in actor
                if isinstance(c, dict)
                and isinstance(c.get("body"), str)
                and c["body"].startswith(_COMMENT_MARKER)
                and c.get("user", {}).get("login") == "github-actions[bot]"]
    if len(existing) > 1:
        raise ReviewHold("PR_ADVISORY_COMMENT_AMBIGUOUS")
    _pr(client, number, head_sha, expected_base=base_sha)
    if existing:
        comment_id = existing[0].get("id")
        if type(comment_id) is not int or comment_id < 1:
            raise ReviewHold("PR_COMMENT_ID_INVALID")
        client.request(f"/issues/comments/{comment_id}", method="PATCH", body={"body": text})
    else:
        client.request(f"/issues/{number}/comments", method="POST", body={"body": text})



def post_existing_incident_handoff(client, *, result: dict):
    """Update one existing AMRT issue; never dispatch repairs or add incidents."""
    number, head = result["pr"], result["head_sha"]
    review = result["review"]
    clean = review.get("status") == "ADVISORY_STATIC_SCAN_COMPLETE"
    if not clean and review.get("status") != "ADVISORY_FINDINGS_REQUIRE_TRIAGE":
        raise ReviewHold("INCIDENT_HANDOFF_REVIEW_INCOMPLETE")
    incident = result["incident_id"]
    if not isinstance(incident, str) or not re.fullmatch(r"[1-9][0-9]*", incident):
        return "INCIDENT_ID_UNRESOLVED_PR_ADVISORY_ONLY"
    _pr(client, number, head, expected_base=result["base_sha"])
    target = client.request(f"/issues/{incident}")
    if (not isinstance(target, dict) or target.get("number") != int(incident)
            or target.get("state") != "open" or "pull_request" in target):
        raise ReviewHold("CANONICAL_INCIDENT_NOT_OPEN_ISSUE")
    marker = f"<!-- WOW_NATIVE_SENIOR_AMRT_ADVISORY_PR_{number} -->"
    lines = [
        marker,
        "### WOW native senior reviewer — advisory AMRT intake",
        f"Source: PR #{number}, exact head {head}",
        f"Existing incident: #{incident}; can_execute=false",
        f"Review packet: {result['packet_sha256']}",
        "",
    ]
    if clean:
        lines.extend((
            "Latest exact-head native static scan: NO NEW PATTERNS DETECTED.",
            "Prior static hypotheses from this PR are superseded as current-head "
            "advisories, NOT proven false or independently resolved.",
        ))
    else:
        lines.extend((
            "Unconfirmed static patterns; not verified P0/P1 incidents.",
            "Root Cause Investigator must reproduce a finding before a leased repair.",
        ))
    for finding in review["findings"][:20]:
        lines.append(
            f"- {finding['finding_id']} ({finding['severity']} candidate): "
            f"{finding['rule_id']} in {finding['file']}:{finding['line']}. "
            "Disposition: ADVISORY_PENDING_REPRODUCTION."
        )
    lines.append(
        "No automatic writer dispatch, new issue, release approval, "
        "probability or terminal change. SIRT and protected QA remain independent."
    )
    body = "\n".join(lines)
    comments = []
    # Busy canonical P0 incidents can exceed 500 comments. Bound the scan
    # at 2,000 while failing closed on an unproven exhaustive inventory.
    for page in range(1, 21):
        portion = client.request(f"/issues/{incident}/comments?per_page=100&page={page}")
        if not isinstance(portion, list):
            raise ReviewHold("INCIDENT_COMMENT_INVENTORY_INVALID")
        comments.extend(portion)
        if len(portion) < 100:
            break
    else:
        raise ReviewHold("INCIDENT_COMMENT_INVENTORY_LIMIT_REACHED")
    matching = [
        c for c in comments if isinstance(c, dict)
        and isinstance(c.get("body"), str) and c["body"].startswith(marker)
        and isinstance(c.get("user"), dict)
        and c["user"].get("login") == "github-actions[bot]"
    ]
    if len(matching) > 1:
        raise ReviewHold("INCIDENT_HANDOFF_DUPLICATE_BOT_COMMENTS")
    _pr(client, number, head, expected_base=result["base_sha"])
    if clean and not matching:
        return "NO_NEW_STATIC_FINDINGS"
    if matching:
        comment_id = matching[0].get("id")
        if type(comment_id) is not int or comment_id <= 0:
            raise ReviewHold("INCIDENT_HANDOFF_COMMENT_ID_INVALID")
        client.request(f"/issues/comments/{comment_id}", method="PATCH", body={"body": body})
        return "EXISTING_INCIDENT_NOTE_UPDATED"
    client.request(f"/issues/{incident}/comments", method="POST", body={"body": body})
    return "EXISTING_INCIDENT_NOTE_CREATED"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--post-comment", action="store_true")
    parser.add_argument("--post-incident-handoff", action="store_true")
    parser.add_argument("--local-model", default=None,
                        help="Explicit opt-in: existing localhost Ollama model only")
    args = parser.parse_args(argv)
    try:
        client = GitHubClient(os.environ.get("GH_TOKEN", ""))
        result = collect_pr_review(client, number=args.pr, expected_head=args.expected_head,
                                   local_model=args.local_model)
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, sort_keys=True, indent=2), encoding="utf-8")
        summary = markdown_summary(result)
        print(summary)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as dest:
                dest.write(summary + "\n")
        if args.post_comment:
            post_advisory_comment(client, number=args.pr,
                                  head_sha=args.expected_head, text=summary,
                                  base_sha=result["base_sha"])
        if args.post_incident_handoff:
            outcome = post_existing_incident_handoff(client, result=result)
            print("AMRT_ADVISORY_HANDOFF:" + outcome)
        # Incomplete reviews are visible but cannot be mistaken for complete.
        return 0 if result["review"]["status"] not in {
            "REVIEW_CONTEXT_INCOMPLETE", "REVIEW_FINDINGS_TRUNCATED"} else 2
    except (ReviewHold, ValueError) as exc:
        # Catch only typed review/input faults. No credential or response logging.
        print("WOW_NATIVE_REVIEW_HOLD:" + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
