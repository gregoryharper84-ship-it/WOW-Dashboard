"""Read-only, fail-closed Viktor evidence extractor for an already admitted incident.

Comments are not dispatch signals, approval, or source-of-truth. Only provenance-
checked GitHub App comments with a narrow data-only envelope may contribute a
link to the existing incident's reproduction_evidence. No natural language is
copied to the agent context; agents must reproduce findings independently.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

APP_ID = 2603499  # Verified GitHub API performed_via_github_app for Viktor smoke test.
APP_SLUG = "viktor-com"
AUTHOR_ID = 285088163
MARKER = "<!-- WOW_VIKTOR_AUDIT_V1 -->\n"
REQUIRED = frozenset({"schema", "incident_id", "source_sha", "evidence_url", "fingerprint", "failure_code"})
SHA = re.compile(r"[0-9a-f]{40}\Z")
FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
FAILURE = re.compile(r"[A-Z][A-Z0-9_]{2,63}\Z")
INCIDENT = re.compile(r"[1-9][0-9]{0,9}\Z")
MAX_COMMENT_BYTES = 1500
MAX_ADVISORY = 3
MAX_AGE = timedelta(hours=48)
REPO = "gregoryharper84-ship-it/WOW-Dashboard"


def _comment_timestamp(text: object, now: datetime) -> bool:
    if not isinstance(text, str):
        return False
    try:
        created = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return created.tzinfo is not None and timedelta(0) <= now - created <= MAX_AGE


def _safe_url(text: object) -> bool:
    if not isinstance(text, str) or len(text) > 300 or any(ord(c) < 32 for c in text):
        return False
    p = urlsplit(text)
    if p.scheme != "https" or p.hostname != "github.com" or p.port is not None or p.username or p.password:
        return False
    if p.query or p.fragment or p.path.count("/") < 4:
        return False
    prefix = "/" + REPO + "/"
    return p.path.startswith(prefix) and p.path[len(prefix):].split("/", 1)[0] in {"blob", "commit", "actions", "issues", "pull"}


def parse_comment(comment: object, *, incident: str, head_sha: str, now: datetime) -> dict | None:
    if not isinstance(comment, dict) or not INCIDENT.fullmatch(incident) or not SHA.fullmatch(head_sha):
        return None
    app = comment.get("performed_via_github_app")
    user = comment.get("user")
    if not isinstance(app, dict) or app.get("id") != APP_ID or app.get("slug") != APP_SLUG:
        return None
    if not isinstance(user, dict) or user.get("id") != AUTHOR_ID:
        return None
    if not _comment_timestamp(comment.get("created_at"), now):
        return None
    body = comment.get("body")
    if not isinstance(body, str) or not body.startswith(MARKER) or len(body.encode("utf-8")) > MAX_COMMENT_BYTES:
        return None
    try:
        row = json.loads(body[len(MARKER):])
    except (ValueError, TypeError):
        return None
    if not isinstance(row, dict) or row.keys() != REQUIRED or row.get("schema") != "WOW_VIKTOR_AUDIT_V1":
        return None
    if row.get("incident_id") != incident or row.get("source_sha") != head_sha:
        return None
    if not _safe_url(row.get("evidence_url")):
        return None
    if not isinstance(row.get("fingerprint"), str) or not FINGERPRINT.fullmatch(row["fingerprint"]):
        return None
    if not isinstance(row.get("failure_code"), str) or not FAILURE.fullmatch(row["failure_code"]):
        return None
    if type(comment.get("id")) is not int or comment["id"] <= 0:
        return None
    return {"comment_id": comment["id"], "source": "VIKTOR_ADVISORY_NOT_AUTHORITY", "url": row["evidence_url"],
            "failure_code": row["failure_code"], "fingerprint": row["fingerprint"], "head_sha": head_sha}


def collect(comments: object, *, incident: str, head_sha: str, now: datetime) -> list[dict]:
    # A full API page may be truncated; missing authoritative completeness fails closed.
    if not isinstance(comments, list) or len(comments) >= 100:
        return []
    by_fingerprint: dict[str, list[dict]] = {}
    for comment in comments:
        row = parse_comment(comment, incident=incident, head_sha=head_sha, now=now)
        if row:
            by_fingerprint.setdefault(row["fingerprint"], []).append(row)
    # Ambiguous repetitions of the same fingerprint are rejected, not arbitrarily ordered.
    unique = [rows[0] for rows in by_fingerprint.values() if len(rows) == 1]
    return sorted(unique, key=lambda x: x["comment_id"], reverse=True)[:MAX_ADVISORY]


def attach_to_ticket(context: dict, rows: list[dict], *, incident: str) -> dict:
    if not isinstance(context, dict) or context.get("incident_id") != incident:
        raise ValueError("VIKTOR_CONTEXT_INCIDENT_MISMATCH")
    if context.get("can_execute") is not False or context.get("terminal_authority") != "V17_TERMINAL_REDUCER":
        raise ValueError("VIKTOR_CONTEXT_GOVERNANCE_INVALID")
    result = dict(context)
    prior = result.get("reproduction_evidence")
    if not isinstance(prior, list):
        raise ValueError("VIKTOR_CONTEXT_EVIDENCE_INVALID")
    result["reproduction_evidence"] = list(prior) + [
        "UNTRUSTED_VIKTOR_POINTER only; independently verify: " + row["url"]
        for row in rows
    ]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--incident", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--comments", required=True, type=Path)
    parser.add_argument("--context", required=True, type=Path)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    comments = json.loads(args.comments.read_text())
    rows = collect(comments, incident=args.incident, head_sha=args.head_sha, now=now)
    path: Path = args.context
    original = json.loads(path.read_text())
    if rows:
        output = attach_to_ticket(original, rows, incident=args.incident)
        path.write_text(json.dumps(output, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"status": "ADVISORY_ONLY", "accepted": len(rows), "incident": args.incident,
                      "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER"}, sort_keys=True))


if __name__ == "__main__":
    main()
