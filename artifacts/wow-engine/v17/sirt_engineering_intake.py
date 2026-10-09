"""Controlled GitHub bridge for persisted SIRT findings, not a SIRT release agent.

The independently read-only SIRT monitor persists canonical evidence. Only the
trusted-main GitHub Actions bridge receives issues:write. No code/release power.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
AUTHORITY = "V17_TERMINAL_REDUCER"
FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
SHA = re.compile(r"^[a-f0-9]{40}$")
ISSUE = re.compile(r"^[1-9][0-9]{0,7}$")
INTAKE_PREFIX = "SIRT-FINDING-"
ESCALATION_PREFIX = "SIRT-P0-ESCALATION-"


def parsed_time(value):
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if out.tzinfo is not None:
            return out.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    raise ValueError("SIRT_FINDING_TIMESTAMP_INVALID")


def safe_text(value, limit=120):
    """Never echo unfiltered evidence dictionaries, URLs, secrets or payloads."""
    value = str(value or "")
    value = re.sub(r"[^A-Za-z0-9 _./:#@-]", "_", value)
    return value[:limit]


def classify(finding):
    if finding.get("can_execute") is not False or finding.get("terminal_authority") != AUTHORITY:
        raise ValueError("SIRT_FINDING_GOVERNANCE_INVALID")
    if finding.get("status") != "OPEN":
        raise ValueError("SIRT_FINDING_NOT_OPEN")
    fp = str(finding.get("fingerprint") or "")
    if not FINGERPRINT.fullmatch(fp):
        raise ValueError("SIRT_FINGERPRINT_INVALID")
    priority = str(finding.get("severity") or "")
    if priority not in {"P0", "P1", "P2", "P3", "P4"}:
        raise ValueError("SIRT_SEVERITY_INVALID")
    first = parsed_time(finding.get("first_detected_at"))
    category = str(finding.get("finding_type") or "")
    if category not in {"STALE_WORK", "CODE_HEALTH", "GOVERNANCE_DRIFT", "AUDITOR_HEALTH"}:
        raise ValueError("SIRT_FINDING_TYPE_INVALID")
    evidence = finding.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("SIRT_EVIDENCE_INVALID")
    head = str(evidence.get("head_sha") or "")
    if head and not SHA.fullmatch(head):
        raise ValueError("SIRT_HEAD_SHA_INVALID")
    component = safe_text(finding.get("component"), 100)
    if not component:
        raise ValueError("SIRT_COMPONENT_MISSING")
    owner = "ENGINEERING_RELIABILITY" if category in {"STALE_WORK", "AUDITOR_HEALTH"} else "ENGINEERING_PLATFORM"
    lane = "RAPID" if priority == "P0" else "STANDARD"
    return dict(fingerprint=fp, priority=priority, first=first, category=category,
                component=component, head=head or "UNVERIFIED", owner=owner, lane=lane,
                evidence_ref=safe_text(evidence.get("run_ref") or evidence.get("source_ref")
                                       or finding.get("source_ref"), 80))


def render_intake(item, *, parent=""):
    return (f"<!-- {INTAKE_PREFIX}{item['fingerprint']} -->\n"
            f"SIRT finding delivered by controlled intake; NOT Engineering acknowledgment.\n\n"
            f"- Priority: **{item['priority']}**, lane: **{item['lane']}**\n"
            f"- Engineering triage owner: **{item['owner']}**\n"
            f"- Finding: {item['category']} on {item['component']}\n"
            f"- Persistent fingerprint: {item['fingerprint']}\n"
            f"- Source reference: {item['evidence_ref'] or 'NOT_RECORDED'}\n"
            f"- Exact observed SHA: {item['head']} (UNVERIFIED if not supplied)\n"
            f"- First detected: {item['first'].isoformat()}\n"
            f"- Source: wow_engineering_audit_findings\n"
            + (f"- Original Engineering issue: #{parent}\n" if parent else "")
            + "\nEngineering must acknowledge with a comment containing "
              f"Engineering-ACK: {item['fingerprint']} and separate Owner: and Next action: lines. "
              "Only a separately authenticated repo-write collaborator qualifies; intake bot does not.\n\n"
              "Engineering reproduces and repairs; independent QA checks deployed SHA and production; "
              "SIRT audits original failure. Neither delivery nor ACK authorizes issue closure.\n"
              "New issues require governed dispatch-manifest admission and conflict lease.\n"
              "can_execute=false; terminal_authority=V17_TERMINAL_REDUCER; "
              "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true.\n")


class Transport:
    def __init__(self, github_token, supabase_url, supabase_key):
        if not github_token or not supabase_url.startswith("https://") or not supabase_key:
            raise ValueError("SIRT_BRIDGE_CREDENTIALS_MISSING")
        self.token, self.url, self.key = github_token, supabase_url.rstrip("/"), supabase_key

    def request(self, host, path, method="GET", payload=None):
        if host == "github":
            if not path.startswith(f"/repos/{REPO}/") and not path.startswith("/search/issues?"):
                raise ValueError("GITHUB_ENDPOINT_NOT_ALLOWED")
            url = "https://api.github.com" + path
            headers = {"Authorization": "Bearer " + self.token,
                       "Accept": "application/vnd.github+json",
                       "X-GitHub-Api-Version": "2022-11-28"}
        else:
            if not path.startswith("/rest/v1/wow_engineering_audit_"):
                raise ValueError("SUPABASE_ENDPOINT_NOT_ALLOWED")
            url = self.url + path
            headers = {"apikey": self.key, "Authorization": "Bearer " + self.key,
                       "Accept": "application/json"}
        headers["User-Agent"] = "WOW-SIRT-controlled-engineering-intake"
        if payload is not None:
            headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode() if payload is not None else None
        with urlopen(Request(url, headers=headers, data=body, method=method), timeout=12) as response:
            return json.load(response)

    def gh(self, path, method="GET", payload=None):
        return self.request("github", f"/repos/{REPO}/{path}", method, payload)

    def findings(self):
        fields = ("fingerprint,can_execute,terminal_authority,status,severity,"
                  "first_detected_at,finding_type,component,source_ref,evidence,work_item_id")
        rows = self.request("supabase", "/rest/v1/wow_engineering_audit_findings?" + urlencode({
            "select": fields, "status": "eq.OPEN", "order": "severity.asc,first_detected_at.asc", "limit": 501}))
        if not isinstance(rows, list) or len(rows) > 500:
            raise RuntimeError("SIRT_FINDINGS_INVENTORY_INCOMPLETE")
        return rows

    def issue_from_work_item(self, finding):
        work_id = str(finding.get("work_item_id") or "")
        if not re.fullmatch(r"[a-fA-F0-9-]{36}", work_id):
            return None
        rows = self.request("supabase", "/rest/v1/wow_engineering_audit_work_items?" + urlencode({
            "select": "source_kind,source_ref,repository", "work_item_id": "eq." + work_id, "limit": 2}))
        if not isinstance(rows, list) or len(rows) != 1:
            raise RuntimeError("SIRT_WORK_ITEM_IDENTITY_UNVERIFIABLE")
        row = rows[0]
        if row.get("source_kind") == "GITHUB_ISSUE" and row.get("repository") == REPO:
            number = str(row.get("source_ref") or "")
            if not ISSUE.fullmatch(number):
                raise ValueError("SIRT_SOURCE_ISSUE_INVALID")
            return int(number)
        return None

    def issue(self, number):
        obj = self.gh(f"issues/{number}")
        if obj.get("number") != number or "pull_request" in obj:
            raise RuntimeError("SIRT_ISSUE_IDENTITY_INVALID")
        return obj

    def comments(self, number):
        rows, page = [], 1
        while True:
            chunk = self.gh(f"issues/{number}/comments?per_page=100&page={page}")
            if not isinstance(chunk, list):
                raise RuntimeError("SIRT_COMMENT_INVENTORY_INVALID")
            rows.extend(chunk)
            if len(chunk) < 100:
                return rows
            page += 1
            if page > 30:
                raise RuntimeError("SIRT_COMMENT_INVENTORY_INCOMPLETE")

    def find_generated_issue(self, fingerprint):
        q = quote(f'repo:{REPO} is:issue in:body "{INTAKE_PREFIX}{fingerprint}"')
        obj = self.request("github", "/search/issues?q=" + q + "&per_page=20")
        rows = obj.get("items") if isinstance(obj, dict) else None
        if not isinstance(rows, list) or obj.get("incomplete_results") or obj.get("total_count", 0) > 20:
            raise RuntimeError("SIRT_ISSUE_SEARCH_INCOMPLETE")
        matches = [int(row["number"]) for row in rows
                   if INTAKE_PREFIX + fingerprint in str(row.get("body") or "")]
        if len(matches) > 1:
            raise RuntimeError("SIRT_DUPLICATE_ISSUES_REQUIRE_RECONCILIATION")
        return matches[0] if matches else None

    def route_issue(self, number, item):
        """Assign verified GitHub priority and lane labels, not worker admission."""
        names = (item["priority"], "wow-sirt-engineering-" + item["lane"].lower())
        for name in names:
            try:
                self.gh("labels/" + quote(name, safe=""))
            except HTTPError as exc:
                if exc.code != 404:
                    raise
                self.gh("labels", "POST", {"name": name, "color": "687980",
                                          "description": "Controlled SIRT Engineering intake"})
        self.gh(f"issues/{number}/labels", "POST", {"labels": list(names)})
        observed = self.issue(number)
        labels = {x.get("name") for x in observed.get("labels") or [] if isinstance(x, dict)}
        if not set(names).issubset(labels):
            raise RuntimeError("SIRT_ENGINEERING_LANE_ROUTING_UNVERIFIED")

    def post_comment(self, number, body):
        result = self.gh(f"issues/{number}/comments", "POST", {"body": body})
        comment_id = result.get("id") if isinstance(result, dict) else None
        if not isinstance(comment_id, int):
            raise RuntimeError("SIRT_ISSUE_COMMENT_WRITE_UNVERIFIED")
        receipt = self.gh(f"issues/comments/{comment_id}")
        if receipt.get("id") != comment_id or receipt.get("body") != body:
            raise RuntimeError("SIRT_ISSUE_COMMENT_READBACK_MISMATCH")

    def create_issue(self, item):
        title = f"[{item['priority']}][SIRT to Engineering][{item['lane']}] {item['category']}: {item['component']}"
        body = render_intake(item)
        result = self.gh("issues", "POST", {"title": title, "body": body})
        number = result.get("number") if isinstance(result, dict) else None
        if not isinstance(number, int) or self.issue(number).get("body") != body:
            raise RuntimeError("SIRT_ISSUE_CREATE_READBACK_MISMATCH")
        return number

    def authorized_ack(self, comment, fingerprint, first=None):
        if first is not None:
            try:
                if parsed_time(comment.get("created_at")) < first:
                    return False
            except ValueError:
                return False
        body = str(comment.get("body") or "")
        if not re.search(r"(?m)^Engineering-ACK: " + re.escape(fingerprint) + r"\s*$", body):
            return False
        if not re.search(r"(?mi)^Owner:\s*\S+", body) or not re.search(r"(?mi)^Next action:\s*\S+", body):
            return False
        user = str((comment.get("user") or {}).get("login") or "")
        if (not re.fullmatch(r"[A-Za-z0-9-]{1,39}", user) or user.endswith("[bot]")
                or user.lower() in {"github-actions", "dependabot"}):
            return False
        access = self.gh("collaborators/" + user + "/permission")
        return access.get("permission") in {"admin", "maintain", "write"}


def act(transport, finding, now):
    item = classify(finding)
    if item["first"] > now + timedelta(minutes=2):
        raise ValueError("SIRT_FINDING_FUTURE_TIMESTAMP")
    origin = transport.issue_from_work_item(finding)
    number = origin or transport.find_generated_issue(item["fingerprint"])
    if number is None:
        number = transport.create_issue(item)
        outcome = "DELIVERED_NEW_ENGINEERING_ISSUE"
    else:
        issue = transport.issue(number)
        if issue.get("state") != "open":
            raise RuntimeError("SIRT_ENGINEERING_ISSUE_CLOSED_UNVERIFIED")
        outcome = "DELIVERED_EXISTING_ENGINEERING_ISSUE"
    transport.route_issue(number, item)
    comments = transport.comments(number)
    marker = INTAKE_PREFIX + item["fingerprint"]
    if origin and not any(marker in str(c.get("body") or "") for c in comments):
        transport.post_comment(number, render_intake(item, parent=number))
        comments = transport.comments(number)
        if not any(marker in str(c.get("body") or "") for c in comments):
            raise RuntimeError("SIRT_DELIVERY_NOT_VISIBLE")
    if any(transport.authorized_ack(c, item["fingerprint"], item["first"]) for c in comments):
        return {"fingerprint": item["fingerprint"], "issue": number,
                "status": "ENGINEERING_ACKNOWLEDGED"}
    if item["priority"] == "P0" and now - item["first"] >= timedelta(minutes=15):
        marker = ESCALATION_PREFIX + item["fingerprint"]
        prior = [parsed_time(c["created_at"]) for c in comments
                 if marker in str(c.get("body") or "")]
        if not prior or now - max(prior) >= timedelta(hours=1):
            msg = (f"<!-- {marker} -->\n**P0 UNACKNOWLEDGED** fingerprint {item['fingerprint']}. "
                   f"Engineering owner {item['owner']} / RAPID must claim and admit "
                   "this issue under the governed manifest/conflict lease. "
                   "No dispatch or release authority was granted.\n")
            transport.post_comment(number, msg)
            outcome = "P0_ESCALATED_UNACKNOWLEDGED"
        # Separate read-back and retry budget for the parent Engineering alert:
        # partial success on the source issue must never suppress central delivery.
        if number != 1021:
            central_comments = transport.comments(1021)
            central_prior = [parsed_time(c["created_at"]) for c in central_comments
                             if marker in str(c.get("body") or "")]
            if not central_prior or now - max(central_prior) >= timedelta(hours=1):
                transport.post_comment(1021, f"<!-- {marker} -->\nP0 unacknowledged: "
                                       f"#{number}, fingerprint {item['fingerprint']}; "
                                       "Engineering must assign owner and next action.")
                outcome = "P0_ESCALATED_UNACKNOWLEDGED"
    return {"fingerprint": item["fingerprint"], "issue": number,
            "status": outcome + "_ACK_PENDING"}


def main():
    try:
        transport = Transport(os.getenv("GH_TOKEN", ""), os.getenv("SUPABASE_URL", ""),
                              os.getenv("SUPABASE_SERVICE_ROLE_KEY")
                              or os.getenv("SUPABASE_SERVICE_KEY", ""))
        findings = transport.findings()
    except (ValueError, RuntimeError, HTTPError, URLError, OSError) as exc:
        code = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "SIRT_BRIDGE_TRANSPORT_UNAVAILABLE"
        print(json.dumps({"status": "BLOCKED", "code": code, "can_execute": False}))
        return 2
    errors = 0
    for finding in findings:
        try:
            receipt = act(transport, finding, datetime.now(timezone.utc))
        except (ValueError, RuntimeError, HTTPError, URLError, OSError) as exc:
            errors += 1
            receipt = {"fingerprint": safe_text(finding.get("fingerprint")), "status": "BLOCKED",
                       "code": (str(exc) if isinstance(exc, (ValueError, RuntimeError))
                                else "SIRT_BRIDGE_TRANSPORT_UNAVAILABLE")}
        print(json.dumps({**receipt, "can_execute": False, "terminal_authority": AUTHORITY}, sort_keys=True))
    print(json.dumps({"total": len(findings), "delivery_failures": errors,
                      "status": "BLOCKED" if errors else "INTAKE_RECONCILED", "can_execute": False}))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
